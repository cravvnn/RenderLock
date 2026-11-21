"""Build a Dolly native compatibility profile from a real Deadlock install.

This is the maintainer "packer". Given the installed ``client.dll`` (and the
unchanged ``engine2.dll``/``tier0.dll``), it:

1. hashes the modules and locates the client camera symbols by signature,
   RTTI (CViewRender vtable), the SetGlobals store and the aspect-source
   reference;
2. merges the engine/tier0 blocks and effects from a previous profile;
3. writes a reviewed profile JSON, refreshes ``native/profiles/manifest.json``
   and emits ``native/src/dolly_compat_generated.hpp`` for the native bridge.

It does not bundle or copy any game binary. It requires ``pefile`` and
``capstone`` (build-time only, see requirements-build.txt).

Usage::

    python tools/generate_profile.py \
        --game-dir "B:/SteamLibrary/steamapps/common/Deadlock" \
        --previous native/profiles/deadlock-2026-09-09-complete.json \
        --label 2026-09-11
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]

MANIFEST_FORMAT = 1
NATIVE_ABI = 4
MODULE_RELATIVES = {
    "client": "citadel/bin/win64/client.dll",
    "engine": "bin/win64/engine2.dll",
    "tier0": "bin/win64/tier0.dll",
}
SETUP_PROLOGUE = bytes.fromhex(
    "48 8b c4 48 89 58 10 55 56 57 41 54 41 55 41 56 41 57 48 81 ec c0 08 00 00 0f 29 70 b8 4c 8b f9"
)
# SetGlobals: xor ecx,ecx; lea r8,[rip+..]; cmp [rip+..],r8; ...
SETGLOBALS_ANCHOR = bytes.fromhex("33 c9 4c 8d 05")
SETGLOBALS_TICK = bytes.fromhex("c7 05")  # mov dword [rip+disp], 0x3c888889
SETGLOBALS_TICK_IMM = struct.pack("<I", 0x3C888889)
SETGLOBALS_STORE = bytes.fromhex("48 89 15")  # mov [rip+disp], rdx
# aspect source: mov rcx,[rip+..]; mov rax,[rcx]; call qword [rax+0x2b0]
ASPECT_SOURCE = bytes.fromhex("48 8b 0d") + b"\x00" * 4 + bytes.fromhex("48 8b 01 ff 90 c0 02 00 00")


class ProfileError(RuntimeError):
    pass


class Image:
    def __init__(self, path: Path):
        import pefile

        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.pe = pefile.PE(str(self.path), fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.image_size = self.pe.OPTIONAL_HEADER.SizeOfImage
        self.timestamp = self.pe.FILE_HEADER.TimeDateStamp
        self.sections = [
            (s.VirtualAddress, s.Misc_VirtualSize, s.PointerToRawData, s.SizeOfRawData,
             s.Name.rstrip(b"\x00").decode())
            for s in self.pe.sections
        ]
        self.sha256 = hashlib.sha256(self.data).hexdigest()

    def close(self) -> None:
        self.pe.close()

    def rva_to_off(self, rva: int) -> int | None:
        for va, vs, praw, rs, _ in self.sections:
            if va <= rva < va + max(vs, rs):
                return praw + (rva - va)
        return None

    def off_to_rva(self, off: int) -> int | None:
        for va, vs, praw, rs, _ in self.sections:
            if praw <= off < praw + rs:
                return va + (off - praw)
        return None

    def read(self, rva: int, size: int) -> bytes:
        off = self.rva_to_off(rva)
        if off is None:
            raise ProfileError(f"rva {rva:#x} is outside the image")
        return self.data[off:off + size]

    def section(self, name: str) -> tuple[int, bytes]:
        for va, vs, praw, rs, n in self.sections:
            if n == name:
                return va, self.data[praw:praw + rs]
        raise ProfileError(f"missing section {name}")

    def find_all(self, needle: bytes) -> list[int]:
        out: list[int] = []
        start = 0
        while True:
            index = self.data.find(needle, start)
            if index < 0:
                return out
            out.append(index)
            start = index + 1

    def functions(self) -> list[tuple[int, int]]:
        import pefile

        self.pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXCEPTION"]])
        return sorted(
            (e.struct.BeginAddress, e.struct.EndAddress)
            for e in getattr(self.pe, "DIRECTORY_ENTRY_EXCEPTION", [])
            if e.struct.BeginAddress and e.struct.EndAddress
        )

    def function_at(self, rva: int) -> tuple[int, int] | None:
        import bisect

        functions = self.functions()
        begins = [b for b, _ in functions]
        index = bisect.bisect_right(begins, rva) - 1
        if index >= 0:
            begin, end = functions[index]
            if begin <= rva < end:
                return begin, end
        return None


def u32(image: Image, rva: int) -> int:
    return struct.unpack("<I", image.read(rva, 4))[0]


def u64(image: Image, rva: int) -> int:
    return struct.unpack("<Q", image.read(rva, 8))[0]


def locate_setup(image: Image) -> int:
    text_va, text = image.section(".text")
    index = text.find(SETUP_PROLOGUE)
    if index < 0:
        raise ProfileError("main view setup prologue was not found; this build needs manual review")
    rva = text_va + index
    if text.find(SETUP_PROLOGUE, index + 1) >= 0:
        raise ProfileError("main view setup prologue is ambiguous in this build")
    return rva


def locate_caller(image: Image, setup_rva: int) -> int:
    text_va, text = image.section(".text")
    hits: list[int] = []
    for index in range(len(text) - 5):
        if text[index] != 0xE8:
            continue
        rel = struct.unpack_from("<i", text, index + 1)[0]
        if text_va + index + 5 + rel == setup_rva:
            hits.append(text_va + index)
    if not hits:
        raise ProfileError("no direct caller of the main view setup was found")
    if len(hits) > 1:
        raise ProfileError(f"multiple main view setup call sites found: {[hex(h) for h in hits]}")
    return hits[0] + 5


def locate_vtable(image: Image, class_name: str = "CViewRender") -> int:
    needle = (".?AV" + class_name + "@@").encode("ascii")
    names = image.find_all(needle)
    if not names:
        raise ProfileError(f"{class_name} RTTI type name was not found")
    for name_off in names:
        descriptor_rva = image.off_to_rva(name_off) - 0x10
        # Complete Object Locator references the descriptor as an RVA at +0xC.
        for col_off in image.find_all(struct.pack("<I", descriptor_rva)):
            start = col_off - 0xC
            if start < 0:
                continue
            signature, offset, cd = struct.unpack_from("<III", image.data, start)
            if signature != 1 or offset != 0 or cd != 0:
                continue
            col_rva = image.off_to_rva(start)
            for ptr_off in image.find_all(struct.pack("<Q", image.base + col_rva)):
                vtable_rva = image.off_to_rva(ptr_off)
                if vtable_rva is not None:
                    return vtable_rva + 8
    raise ProfileError(f"no {class_name} vtable references its RTTI locator")


def locate_globals(image: Image) -> tuple[int, int]:
    text_va, text = image.section(".text")
    for index in range(len(text) - 7):
        if text[index:index + 2] != SETGLOBALS_TICK:
            continue
        immediate = text[index + 6:index + 10]
        if immediate != SETGLOBALS_TICK_IMM:
            continue
        # mov [rip+disp], rdx lives later in the same function
        for forward in range(index, min(index + 0x60, len(text) - 7)):
            if text[forward:forward + 3] == SETGLOBALS_STORE:
                instruction_rva = text_va + forward
                disp = struct.unpack_from("<i", text, forward + 3)[0]
                pointer_rva = instruction_rva + 7 + disp
                fallback = None
                # lea r8,[rip+disp] earlier in the function gives the fallback
                for backward in range(max(0, index - 0x40), index):
                    if text[backward:backward + 3] == bytes.fromhex("4c 8d 05"):
                        rva = text_va + backward
                        off = struct.unpack_from("<i", text, backward + 3)[0]
                        fallback = rva + 7 + off
                        break
                if fallback is None:
                    raise ProfileError("SetGlobals fallback pointer was not found")
                return pointer_rva, fallback
    raise ProfileError("SetGlobals pattern was not found")


def locate_engine_client_candidates(image: Image, setup_begin: int, setup_end: int) -> list[int]:
    """Collect every interface pointer read before a ``call [rax+0x2b0]`` in setup.

    There can be more than one such virtual call. The caller disambiguates by
    proximity to the previous profile's reviewed pointer.
    """
    text_va, text = image.section(".text")
    start = setup_begin - text_va
    stop = setup_end - text_va
    anchor = bytes.fromhex("48 8b 01 ff 90 b0 02 00 00")
    candidates: list[int] = []
    index = text.find(anchor, start, stop)
    while index >= 0:
        for pointer in range(index, max(start, index - 0x80) - 1, -1):
            if text[pointer:pointer + 3] == b"\x48\x8b\x0d":
                instruction_rva = text_va + pointer
                disp = struct.unpack_from("<i", text, pointer + 3)[0]
                candidates.append(instruction_rva + 7 + disp)
                break
        index = text.find(anchor, index + 1, stop)
    if not candidates:
        raise ProfileError("aspect source virtual calls were not found in the main view setup")
    return candidates


def locate_engine_client(image: Image, setup_begin: int, setup_end: int,
                         previous_pointer: int | None = None) -> int:
    candidates = locate_engine_client_candidates(image, setup_begin, setup_end)
    if previous_pointer is None or len(candidates) == 1:
        return candidates[-1]
    return min(candidates, key=lambda candidate: abs(candidate - previous_pointer))


def build_signature(image: Image, rva: int, length: int = 0x120) -> tuple[bytes, bytes]:
    """Wildcard every rip-relative displacement and relative call/jmp operand."""
    import capstone

    image_bytes = image.read(rva, length)
    mask = bytearray(b"\x01" * len(image_bytes))
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    for insn in md.disasm(image_bytes, image.base + rva):
        offset = insn.address - (image.base + rva)
        for operand in insn.operands:
            if operand.type == capstone.x86.X86_OP_MEM and operand.mem.base == capstone.x86.X86_REG_RIP:
                start = offset + insn.disp_offset
                for i in range(start, start + insn.disp_size):
                    if 0 <= i < len(mask):
                        mask[i] = 0
            if operand.type == capstone.x86.X86_OP_IMM and insn.mnemonic in ("call", "jmp"):
                start = offset + insn.imm_offset
                for i in range(start, start + insn.imm_size):
                    if 0 <= i < len(mask):
                        mask[i] = 0
    return bytes(image_bytes), bytes(mask)


def _merge_module_block(previous: dict, role: str) -> dict:
    key = {"client": "client", "engine": "engine", "tier0": "tier0"}[role]
    block = dict(previous.get(key, {}))
    return block


def build_profile(client: Image, engine: Image, tier0: Image, previous: dict, label: str) -> dict:
    if reviewed_camera_layout(previous.get("client", {})):
        reviewed = previous["client"]
        if client.sha256 != reviewed["client_sha256"]:
            raise ProfileError("Virtual camera wrapper requires a new exact-build review")
        for role, image, size_key in (("engine", engine, "image_size"),
                                      ("tier0", tier0, "size_of_image")):
            block = previous.get(role, {})
            expected_size = block.get(size_key, "0")
            expected_size = int(expected_size, 16) if isinstance(expected_size, str) else expected_size
            if (image is None or image.sha256 != block.get("sha256") or
                    image.image_size != expected_size):
                raise ProfileError(f"{role} requires a new exact-build review")
        for function in reviewed["camera_layout_review"]["functions"].values():
            start, stop = int(function["rva"], 16), int(function["end_rva"], 16)
            if hashlib.sha256(client.read(start, stop - start)).hexdigest() != function["sha256"]:
                raise ProfileError("Reviewed camera function bytes differ")
        if locate_vtable(client) != int(reviewed["primary_vtable_rva"], 16):
            raise ProfileError("Reviewed CViewRender RTTI differs")
        return json.loads(json.dumps(previous)), {
            "setup_rva": int(reviewed["main_view_setup_rva"], 16),
            "caller_rva": int(reviewed["view_setup_abi"]["caller_return_rva"], 16),
            "image": client,
        }
    # The general path carries the engine/tier0 blocks forward unchanged. Refuse
    # a changed identity instead of silently publishing stale engine data.
    for role, image, size_key in (("engine", engine, "image_size"),
                                  ("tier0", tier0, "size_of_image")):
        block = previous.get(role, {})
        expected_size = block.get(size_key, "0")
        expected_size = int(expected_size, 16) if isinstance(expected_size, str) else expected_size
        if (image is None or image.sha256 != block.get("sha256") or
                image.image_size != expected_size):
            raise ProfileError(
                f"{role} changed; use tools/update_game.py for a new exact-build review")
    setup_rva = locate_setup(client)
    try:
        caller_rva = locate_caller(client, setup_rva)
    except ProfileError:
        # Current builds call the wrapper virtually; carry the reviewed caller
        # through the same relocation delta as the wrapper.
        previous_setup = int(previous["client"]["main_view_setup_rva"], 16)
        previous_caller = int(previous["client"]["view_setup_abi"]["caller_return_rva"], 16)
        caller_rva = previous_caller + (setup_rva - previous_setup)
    vtable_rva = locate_vtable(client)
    globals_rva, fallback_rva = locate_globals(client)
    bounds = client.function_at(setup_rva)
    if bounds is None:
        raise ProfileError("main view setup has no .pdata function bounds")
    setup_begin, setup_end = bounds
    try:
        previous_pointer = int(
            previous["client"]["aspect_scaling"]["aspect_source_interface_pointer_rva"], 16)
    except (KeyError, TypeError, ValueError):
        previous_pointer = None
    engine_client_rva = locate_engine_client(client, setup_begin, setup_end, previous_pointer)
    matrix_bounds = client.function_at(caller_rva - 5)
    matrix_begin, matrix_end = matrix_bounds if matrix_bounds else (caller_rva - 5 - 0x3F, caller_rva - 5 + 0x211)
    profile = json.loads(json.dumps(previous))  # deep copy of the previous profile shape
    profile["validation"] = (
        f"Static review generated by tools/generate_profile.py for {label}. "
        "Native view cadence and visual smoothness still require the user test."
    )
    client_block = profile.setdefault("client", {})
    client_block.update({
        "client_sha256": client.sha256,
        "file_size": len(client.data),
        "timestamp": client.timestamp,
        "size_of_image": client.image_size,
        "preferred_image_base": hex(client.base),
        "main_view_setup_rva": hex(setup_begin),
        "main_view_setup_end_rva": hex(setup_end),
        "main_view_setup_prologue_32": SETUP_PROLOGUE.hex(" "),
        "main_view_setup_sha256": hashlib.sha256(client.read(setup_begin, setup_end - setup_begin)).hexdigest(),
        "matrix_setup_rva": hex(matrix_begin),
        "matrix_setup_end_rva": hex(matrix_end),
        "matrix_setup_sha256": hashlib.sha256(client.read(matrix_begin, matrix_end - matrix_begin)).hexdigest(),
        "primary_vtable_rva": hex(vtable_rva),
        "call_site_rva": hex(caller_rva - 5),
        "call_site_bytes": client.read(caller_rva - 5, 5).hex(" "),
        "globals": dict(client_block.get("globals", {}), pointer_rva=hex(globals_rva), fallback_rva=hex(fallback_rva)),
        "aspect_scaling": dict(client_block.get("aspect_scaling", {}),
                               aspect_source_interface_pointer_rva=hex(engine_client_rva)),
    })
    # Persist the reviewed instruction source while this exact binary exists;
    # never carry another build's signature through the deep-copy above.
    signature, signature_mask = build_signature(client, setup_begin)
    client_block["camera_signature"] = {
        "client_sha256": client.sha256, "bytes": signature.hex(), "mask": signature_mask.hex(),
    }

    old_setup = int(previous["client"]["main_view_setup_rva"], 16)
    delta = setup_begin - old_setup

    def resolve_call(rva: int) -> int | None:
        try:
            if client.read(rva, 1) != b"\xe8":
                return None
            return rva + 5 + struct.unpack("<i", client.read(rva + 1, 4))[0]
        except ProfileError:
            return None

    aspect = client_block.setdefault("aspect_scaling", {})
    aspect["original_call_rva"] = hex(int(aspect["original_call_rva"], 16) + delta)
    helper = resolve_call(int(aspect["original_call_rva"], 16))
    if helper is not None:
        aspect["helper_rva"] = hex(helper)
    aspect["aspect_source_call_rva"] = hex(int(aspect["aspect_source_call_rva"], 16) + delta)

    followup = client_block.setdefault("matrix_followup", {})
    followup["first_call_rva"] = hex(int(followup["first_call_rva"], 16) + delta)
    first_builder = resolve_call(int(followup["first_call_rva"], 16))
    if first_builder is not None:
        followup["first_matrix_builder_rva"] = hex(first_builder)
    followup["internal_call_rva"] = hex(int(followup["internal_call_rva"], 16) + delta)
    internal_builder = resolve_call(int(followup["internal_call_rva"], 16))
    if internal_builder is not None:
        followup["internal_view_builder_rva"] = hex(internal_builder)

    client_block.setdefault("view_setup_abi", {})["caller_return_rva"] = hex(caller_rva)
    client_block["view_setup_abi"]["direct_call_sites"] = [hex(caller_rva - 5)]
    review = profile.setdefault("compatibility_review", {})
    review.update({
        "client_sha256": client.sha256,
        "file_size": len(client.data),
        "timestamp": client.timestamp,
        "image_size": client.image_size,
        "functions": {
            "main_view_setup": {
                "rva": hex(setup_begin),
                "end_rva": hex(setup_end),
                "sha256": hashlib.sha256(client.read(setup_begin, setup_end - setup_begin)).hexdigest(),
            },
            "matrix_setup": {
                "rva": hex(matrix_begin),
                "end_rva": hex(matrix_end),
                "sha256": hashlib.sha256(client.read(matrix_begin, matrix_end - matrix_begin)).hexdigest(),
            },
        },
        "validation": "Generated static review; current-game execution still required.",
    })
    relocated = []
    for entry in previous.get("compatibility_review", {}).get("relocated_calls", []):
        new_call = int(entry["call_rva"], 16) + delta
        target = resolve_call(new_call)
        if target is not None:
            relocated.append({
                "call_rva": hex(new_call),
                "old_target": entry.get("new_target", entry.get("old_target")),
                "new_target": hex(target),
            })
    if relocated:
        review["relocated_calls"] = relocated
    return profile, {
        "setup_rva": setup_begin,
        "caller_rva": caller_rva,
        "vtable_rva": vtable_rva,
        "globals_rva": globals_rva,
        "engine_client_rva": engine_client_rva,
        "signature_rva": setup_begin,
    }


def reviewed_render_fraction(client: dict) -> int:
    """Do not inherit a clock-field review when a new client hash is profiled."""
    review = client.get("globals", {}).get("replay_clock_review") or {}
    if not review or review.get("client_sha256") != client.get("client_sha256"):
        return 0
    offset = int(review["render_fraction_offset"], 16)
    if offset != 0x38:
        raise ValueError("Replay render fraction needs an explicit native layout review")
    return offset


def reviewed_camera_layout(client: dict) -> int:
    """A copied profile must never authorize a changed structure/call chain."""
    revision = client.get("camera_layout_revision", 0)
    if revision not in (0, 1):
        raise ProfileError("Unknown reviewed camera layout revision")
    if revision and client.get("camera_layout_review", {}).get("client_sha256") != client.get("client_sha256"):
        raise ProfileError("Camera layout review does not match the client hash")
    if revision == 1:
        expected = {"view_setup_this_offset": 0x10, "origin_offset": 0x4a0,
                    "angles_offset": 0x4b8, "fov_offset": 0x498, "aspect_offset": 0x4d4,
                    "view_flag_byte_offset": 0x551, "auxiliary_origin_offset": 0x554,
                    "auxiliary_angles_offset": 0x560, "extra_origin_offset": 0x56c}
        if any(int(client.get(key, "0"), 16) != value for key, value in expected.items()):
            raise ProfileError("Camera layout fields differ from the native revision")
        if not int(client.get("view_setup_delegate_rva", "0"), 16):
            raise ProfileError("Virtual camera wrapper has no reviewed producer")
    return revision


def emit_header(entries: list[tuple[dict, dict | None]], destination: Path) -> None:
    """Emit the native profile table.

    ``entries`` pairs each reviewed profile with a resolved symbol map when its
    original binary is available; only those entries receive an AOB signature.
    Profiles without a binary stay exact-hash only.
    """
    lines = [
        "// Generated by tools/generate_profile.py. Do not edit by hand.",
        "#pragma once",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        "// Deliberately not nested under `dolly`: this header is included from inside",
        "// bridge_win.cpp's anonymous namespace, where any `dolly` namespace would",
        "// shadow the real one and make `dolly::...` ambiguous.",
        "namespace dolly_compat {",
        "struct CompatClientProfile {",
        "    const char* sha256;",
        "    std::uint32_t image_size;",
        "    std::uintptr_t main_view_setup;",
        "    std::uintptr_t main_view_caller;",
        "    std::uintptr_t primary_vtable;",
        "    std::uintptr_t globals;",
        "    std::uintptr_t engine_client;",
        "    std::uintptr_t render_fraction;",
        "    const unsigned char* signature;",
        "    const unsigned char* signature_mask;",
        "    std::size_t signature_size;",
        "    unsigned camera_layout_revision;",
        "    std::uintptr_t view_setup_delegate;",
        "    unsigned char setup_prologue[32];",
        "};",
        "",
    ]
    signatures: dict[int, tuple[str, str, int]] = {}
    for index, (_profile, resolved) in enumerate(entries):
        client = _profile["client"]
        if reviewed_camera_layout(client):
            continue
        saved = client.get("camera_signature", {})
        if resolved:
            signature, mask = build_signature(resolved["image"], resolved["setup_rva"])
        elif saved:
            if saved.get("client_sha256") != client["client_sha256"]:
                raise ProfileError("Saved camera signature belongs to a different client")
            signature, mask = bytes.fromhex(saved["bytes"]), bytes.fromhex(saved["mask"])
            if len(signature) != 0x120 or len(mask) != len(signature) or any(b not in (0, 1) for b in mask):
                raise ProfileError("Invalid saved camera signature")
            if signature[:len(SETUP_PROLOGUE)] != SETUP_PROLOGUE or not all(mask[:len(SETUP_PROLOGUE)]):
                raise ProfileError("Saved camera signature has an unreviewed prologue")
        else:
            continue
        name = f"kSignature{index}"
        lines.append(f"inline const unsigned char {name}[] = {{")
        lines.append("    " + ",".join(f"0x{b:02x}" for b in signature))
        lines.append("};")
        lines.append(f"inline const unsigned char {name}Mask[] = {{")
        lines.append("    " + ",".join("1" if m else "0" for m in mask))
        lines.append("};")
        signatures[index] = (name, f"{name}Mask", len(signature))
    rows = []
    for index, (profile, resolved) in enumerate(entries):
        client = profile["client"]
        if len(bytes.fromhex(client["main_view_setup_prologue_32"])) != 32:
            raise ProfileError("Camera setup prologue must contain exactly 32 bytes")
        caller = (resolved["caller_rva"] if resolved
                  else int(client["view_setup_abi"]["caller_return_rva"], 16))
        setup = resolved["setup_rva"] if resolved else int(client["main_view_setup_rva"], 16)
        signature, mask, size = signatures.get(index, ("nullptr", "nullptr", 0))
        rows.append(
            "    { \"%s\", %d, %s, %s, %s, %s, %s, %s, %s, %s, %d, %d, %s, {%s} }," % (
                client["client_sha256"], client["size_of_image"], hex(setup), hex(caller),
                client["primary_vtable_rva"], client["globals"]["pointer_rva"],
                client["aspect_scaling"]["aspect_source_interface_pointer_rva"],
                hex(reviewed_render_fraction(client)), signature, mask, size,
                reviewed_camera_layout(client), client.get("view_setup_delegate_rva", "0x0"),
                ",".join("0x%02x" % b for b in bytes.fromhex(client["main_view_setup_prologue_32"])),
            )
        )
    lines.append("")
    lines.append("inline const CompatClientProfile kCompatClientProfiles[] = {")
    lines.extend(rows)
    lines.append("};")
    lines.append("inline constexpr std::size_t kCompatClientProfileCount = "
                 "sizeof(kCompatClientProfiles) / sizeof(kCompatClientProfiles[0]);")
    lines.append("}  // namespace dolly_compat")
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def refresh_manifest(profiles_dir: Path, profile_name: str, client: Image, engine: Image,
                     tier0: Image, label: str) -> dict:
    manifest_path = profiles_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {
        "manifest_format": MANIFEST_FORMAT, "native_abi": NATIVE_ABI, "modules": {}, "profiles": []}
    entries = {
        "citadel/bin/win64/client.dll": ("client", label, client.timestamp, client.sha256),
        "bin/win64/engine2.dll": ("engine", label, engine.timestamp, engine.sha256),
        "bin/win64/tier0.dll": ("tier0", label, tier0.timestamp, tier0.sha256),
    }
    for relative, (role, reviewed, timestamp, digest) in entries.items():
        module = manifest.setdefault("modules", {}).setdefault(
            relative, {"role": role, "reviewed_utc": reviewed, "accepted": []})
        module["role"] = role
        accepted = module.setdefault("accepted", [])
        if digest not in accepted:
            accepted.append(digest)
            module["reviewed_utc"] = reviewed
        module["latest_timestamp"] = max(module.get("latest_timestamp", 0), timestamp)
    names = manifest.setdefault("profiles", [])
    if profile_name not in names:
        names.append(profile_name)
    manifest["generated_utc"] = label
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def resolve_game_dir(path: Path) -> Path:
    """Accept either the Deadlock install root or its ``game`` directory."""
    if (path / MODULE_RELATIVES["client"]).is_file():
        return path
    nested = path / "game"
    if (nested / MODULE_RELATIVES["client"]).is_file():
        return nested
    raise ProfileError(f"could not find {MODULE_RELATIVES['client']} under {path}")


def collect_profile_entries(profiles_dir: Path, image: Image | None, *, profile_names=None) -> list[tuple[dict, dict | None]]:
    """Pair every reviewed profile with resolved symbols when its binary is installed."""
    entries: list[tuple[dict, dict | None]] = []
    seen: dict[str, int] = {}
    paths = (profiles_dir.glob("*.json") if profile_names is None
             else (profiles_dir / name for name in profile_names))
    for path in sorted(paths):
        if path.name == "manifest.json":
            continue
        profile = json.loads(path.read_text(encoding="utf-8"))
        client = profile.get("client")
        if not isinstance(client, dict) or "client_sha256" not in client:
            continue
        if image is not None and client["client_sha256"] == image.sha256:
            resolved = {
                "setup_rva": int(client["main_view_setup_rva"], 16),
                "caller_rva": int(client["view_setup_abi"]["caller_return_rva"], 16),
                "image": image,
            }
        else:
            resolved = None
        digest = client["client_sha256"]
        if digest in seen:
            # Prefer a duplicate that carries a signature source.
            index = seen[digest]
            if entries[index][1] is None and resolved is not None:
                entries[index] = (profile, resolved)
            continue
        seen[digest] = len(entries)
        entries.append((profile, resolved))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--previous", type=Path, default=None,
                        help="Previous profile whose engine/tier0/effects blocks are carried forward.")
    parser.add_argument("--label", default=None, help="Review label, for example 2026-09-11.")
    parser.add_argument("--out-profile", type=Path, default=None)
    parser.add_argument("--out-header", type=Path, default=ROOT / "native/src/dolly_compat_generated.hpp")
    parser.add_argument("--out-sound-header", type=Path,
                        default=ROOT / "native/src/dolly_sound_compat_generated.hpp")
    parser.add_argument("--profiles-dir", type=Path, default=ROOT / "native/profiles")
    parser.add_argument("--update-manifest", action="store_true")
    parser.add_argument("--header-only", action="store_true",
                        help="Regenerate the native header from profiles without touching the manifest.")
    args = parser.parse_args(argv)
    args.game_dir = resolve_game_dir(args.game_dir)
    # Sound signatures are preserved with their reviewed binary identity. Never
    # regenerate them from unreviewed installed RVAs during a client update.
    from generate_sound_profile import PROFILE as SOUND_PROFILE, emit_sound_header
    sound_profile = json.loads((args.profiles_dir / SOUND_PROFILE.name).read_text(encoding="utf-8"))
    emit_sound_header(sound_profile, args.out_sound_header)

    if args.header_only:
        image = Image(args.game_dir / MODULE_RELATIVES["client"])
        try:
            entries = collect_profile_entries(args.profiles_dir, image)
            if not entries:
                print("No profiles with client data were found.", file=sys.stderr)
                return 1
            emit_header(entries, args.out_header)
            print(f"Wrote {args.out_header}")
        finally:
            image.close()
        return 0

    if not args.previous or not args.label:
        parser.error("--previous and --label are required unless --header-only is used")
    previous = json.loads(args.previous.read_text(encoding="utf-8"))
    client = Image(args.game_dir / MODULE_RELATIVES["client"])
    engine = Image(args.game_dir / MODULE_RELATIVES["engine"])
    tier0 = Image(args.game_dir / MODULE_RELATIVES["tier0"])
    try:
        profile, _resolved = build_profile(client, engine, tier0, previous, args.label)
        out_profile = args.out_profile or (args.profiles_dir / f"deadlock-{args.label}-complete.json")
        out_profile.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out_profile}")
        if args.update_manifest:
            refresh_manifest(args.profiles_dir, out_profile.name, client, engine, tier0, args.label)
            print(f"Updated {args.profiles_dir / 'manifest.json'}")
        entries = collect_profile_entries(args.profiles_dir, client)
        emit_header(entries, args.out_header)
        print(f"Wrote {args.out_header}")
    finally:
        client.close()
        engine.close()
        tier0.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
