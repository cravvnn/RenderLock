"""Generate reviewed audio signatures; never approve a new DLL by its RVAs alone.

Run with --game-dir to rebuild the profile from the exact reviewed binary, or
without it to reproduce the header from the saved profile after a game update.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from generate_profile import Image, ProfileError, ROOT, build_signature, resolve_game_dir

LEGACY_PROFILE = ROOT / "native/profiles/soundsystem-2026-09-09.json"
PROFILE = ROOT / "native/profiles/soundsystem-2026-11-18-6864.json"
RECENT_PROFILE = ROOT / "native/profiles/soundsystem-2026-11-05-6853.json"
PREVIOUS_PROFILE = ROOT / "native/profiles/soundsystem-2026-10-20-6801.json"
HEADER = ROOT / "native/src/dolly_sound_compat_generated.hpp"
REVIEWED_HASH = "f4dd8b65101495c89ba5e97a87b698bd051c80387d392eda1b50c163c7cebe2e"
REVIEWED_VOICE_TABLE = 0x658fa0
# Reviewed prefixes span complete instructions. Stop has split unwind records;
# its first .pdata entry covers only the prologue, not the function body.
ANCHORS = (('voice_start', 614784, 283, 14), ('voice_stop', 623472, 285, 13), ('voice_map_remove', 506944, 286, 20), ('vmix_start', 1724832, 288, 14), ('event_name', 1823968, 91, 6))


def build_sound_profile(image):
    if image.sha256 != REVIEWED_HASH or image.image_size != 0x6c0000:
        raise ProfileError("Audio signatures require the exact reviewed soundsystem.dll; re-review changed code first")
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    text_rva, text = image.section(".text")
    symbols, references = [], []
    for index, (name, rva, length, prologue) in enumerate(ANCHORS):
        instructions = list(md.disasm(image.read(rva, length), image.base + rva))
        length = sum(ins.size for ins in instructions)
        signature, mask = build_signature(image, rva, length)
        pattern = b"".join(re.escape(bytes([b])) if m else b"." for b, m in zip(signature, mask))
        matches = list(re.finditer(b"(?=(" + pattern + b"))", text, re.DOTALL))
        if len(matches) != 1 or text_rva + matches[0].start() != rva:
            raise ProfileError(f"Audio signature is not unique: {name}")
        symbols.append(dict(name=name, rva=rva, prologue_size=prologue,
                            signature=signature.hex(), mask=mask.hex()))
        for ins in instructions:
            if any(op.type == capstone.x86.X86_OP_MEM and
                   op.mem.base == capstone.x86.X86_REG_RIP and
                   ins.address + ins.size + op.mem.disp == image.base + REVIEWED_VOICE_TABLE
                   for op in ins.operands):
                references.append(dict(symbol=index, offset=ins.address-image.base-rva,
                                       displacement=ins.disp_offset, size=ins.size))
    if {ref["symbol"] for ref in references} != {0, 1, 3}:
        raise ProfileError("Reviewed voice-table references changed")
    return dict(sha256=image.sha256, image_size=image.image_size, voice_table=REVIEWED_VOICE_TABLE,
                voice_map_offset=0x100, parameter_volume_offset=0x20, parameter_rate_offset=0x2c,
                symbols=symbols, table_references=references,
                validation=("6801 offline: complete voice start/stop/map, VMix, event-name and release bodies "
                            "are relocation-equivalent to reviewed snapshots, including chained unwind ranges; "
                            "voice table relocated to 0x658e00. Live audio acceptance is separate."))


def _emit_profile(profile):
    lines = []
    for index, symbol in enumerate(profile["symbols"]):
        for key in ("signature", "mask"):
            values = ",".join(f"0x{b:02x}" for b in bytes.fromhex(symbol[key]))
            lines.append(f"inline constexpr unsigned char k{key}{index}[] = {{{values}}};")
    lines.append("inline constexpr Signature kSignatures[] = {")
    for i, s in enumerate(profile["symbols"]):
        lines.append(f'    {{ksignature{i}, kmask{i}, sizeof(ksignature{i}), '
                     f'{s["prologue_size"]}, {s["rva"]}}},')
    lines.append("};")
    lines.append("inline constexpr TableReference kTableReferences[] = {")
    for r in profile["table_references"]:
        lines.append("    {%d, %d, %d, %d}," % (r["symbol"], r["offset"], r["displacement"], r["size"]))
    lines.extend(["};", f'inline constexpr char kHash[] = "{profile["sha256"]}";',
                  f'inline constexpr unsigned kImageSize = {profile["image_size"]};',
                  f'inline constexpr unsigned kVoiceTable = {profile["voice_table"]};',
                  f'inline constexpr unsigned kVoiceMapOffset = {profile.get("voice_map_offset", 0x118)};',
                  f'inline constexpr unsigned kParameterVolumeOffset = {profile["parameter_volume_offset"]};',
                  f'inline constexpr unsigned kParameterRateOffset = {profile["parameter_rate_offset"]};'])
    return lines


def emit_sound_header(profile, destination=HEADER, *, legacy_profile=None, previous_profile=None):
    legacy = (json.loads(LEGACY_PROFILE.read_text(encoding="utf-8"))
              if legacy_profile is None else legacy_profile)
    previous = (json.loads(PREVIOUS_PROFILE.read_text(encoding="utf-8"))
                if previous_profile is None else previous_profile)
    lines = ["// Generated by tools/generate_sound_profile.py. Do not edit by hand.",
             "#pragma once", '#include "dolly_sound_compat.hpp"',
             "namespace dolly_sound_compat {"]
    lines.extend(_emit_profile(profile))
    lines.append("namespace legacy {")
    lines.extend(_emit_profile(legacy))
    lines.append("} // namespace legacy")
    lines.append("namespace previous {")
    lines.extend(_emit_profile(previous))
    lines.append("} // namespace previous")
    lines.append("namespace recent {")
    lines.extend(_emit_profile(json.loads(RECENT_PROFILE.read_text(encoding="utf-8"))))
    lines.append("} // namespace recent")
    lines.append("inline constexpr Profile kProfiles[] = {")
    for prefix in ("", "legacy::", "recent::"):
        lines.append("    {%skImageSize, %skSignatures, sizeof(%skSignatures)/sizeof(%skSignatures[0]), "
                     "%skTableReferences, sizeof(%skTableReferences)/sizeof(%skTableReferences[0]), "
                     "%skVoiceTable, %skVoiceMapOffset, %skParameterVolumeOffset, %skParameterRateOffset},"
                     % ((prefix,) * 11))
    lines.extend(["};", "} // namespace dolly_sound_compat", ""])
    destination.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path)
    args = parser.parse_args()
    if args.game_dir:
        image = Image(resolve_game_dir(args.game_dir) / "bin/win64/soundsystem.dll")
        try:
            profile = build_sound_profile(image)
        finally:
            image.close()
        PROFILE.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    else:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    emit_sound_header(profile)


if __name__ == "__main__":
    main()
