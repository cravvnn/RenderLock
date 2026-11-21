"""Remote game-build definitions: fetched pins, offsets and AOB signatures.

Dolly's reviewed build data normally ships inside a release: the compatibility
manifest, ``dolly_compat_generated.hpp`` and the hardcoded engine/tier0 pins.
That means a Deadlock update always needs a new Dolly build. This module lets
the maintainer publish one small JSON document instead, on the repository's
default branch, and have installed copies pick it up over the GitHub raw
endpoint.

The document is strictly additive. A remote entry can only *add* an accepted
build; it can never remove, replace or reinterpret a pin that shipped with the
release. A malformed, oversized or unrecognised document is ignored whole, so
a bad edit on the branch can never take a working build away.

Nothing here talks to the game. It fetches, validates, caches to the portable
application folder, and hands the native bridge a compact line document it can
parse without a JSON dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from .runtime import application_root

LOG = logging.getLogger("dolly")

REPOSITORY = "cravvnn/deadlock-dolly"
# The published document lives on the default branch, next to README.md.
BRANCH = "main"
DEFINITIONS_PATH = "definitions.json"
URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/{DEFINITIONS_PATH}"
CACHE_NAME = "DOLLY_DEFINITIONS.json"
NATIVE_NAME = "dolly_definitions.txt"
FORMAT = 1
MAX_BYTES = 512 * 1024
MAX_CLIENT_PROFILES = 32
MAX_ENGINE_PROFILES = 64
MAX_TIER0_PROFILES = 64
MAX_LAYOUTS = 16
MAX_MODULE_PINS = 64
MAX_STOCK_FILES = 256

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[a-z0-9_]{1,24}\Z")

CAMERA_MODULE_RELATIVES = (
    "citadel/bin/win64/client.dll",
    "bin/win64/engine2.dll",
    "bin/win64/tier0.dll",
)
OTHER_MODULE_RELATIVES = (
    "bin/win64/scenesystem.dll",
    "bin/win64/soundsystem.dll",
    "bin/win64/resourcesystem.dll",
    "bin/win64/rendersystemdx11.dll",
)
MODULE_RELATIVES = CAMERA_MODULE_RELATIVES + OTHER_MODULE_RELATIVES
# Editing-configuration consumers are a different gate from the native camera
# manifest, so they keep their own allowlist.
EDITING_MODULE_RELATIVES = (
    "bin/win64/engine2.dll",
    "bin/win64/tier0.dll",
    "bin/win64/filesystem_stdio.dll",
    "bin/win64/rendersystemdx11.dll",
)
ENGINE_LAYOUT_FIELDS = (
    "demo_global", "demo_table", "engine_table", "playing", "active",
    "paused", "seeking", "tick", "filename",
)
TIER0_LAYOUT_FIELDS = ("table", "setter", "find", "data")


class DefinitionsError(RuntimeError):
    """A definitions document is unreadable, malformed or out of bounds."""


@dataclass(frozen=True)
class ClientProfile:
    """One reviewed client camera layout, as the native bridge consumes it."""

    sha256: str
    image_size: int
    main_view_setup: int
    main_view_caller: int
    primary_vtable: int
    globals: int
    engine_client: int
    render_fraction: int
    camera_layout_revision: int
    view_setup_delegate: int
    setup_prologue: bytes
    signature: bytes | None
    signature_mask: bytes | None


@dataclass(frozen=True)
class EngineProfile:
    sha256: str
    image_size: int
    layout: str


@dataclass(frozen=True)
class Tier0Profile:
    sha256: str
    image_size: int
    layout: str


@dataclass(frozen=True)
class Definitions:
    """A validated definitions document. Empty sections mean "bundled only"."""

    updated: str
    notes: str
    modules: dict[str, tuple[str, ...]]
    engine_layouts: dict[str, dict[str, int]]
    engine_profiles: tuple[EngineProfile, ...]
    tier0_layouts: dict[str, dict[str, int]]
    tier0_profiles: tuple[Tier0Profile, ...]
    client_profiles: tuple[ClientProfile, ...]
    editing_modules: dict[str, tuple[str, ...]]
    stock_provenance_files: dict[str, tuple[tuple[int, str], ...]]

    @property
    def empty(self) -> bool:
        return not (self.modules or self.client_profiles or self.engine_profiles
                    or self.tier0_profiles or self.editing_modules
                    or self.stock_provenance_files)

    def describe(self) -> str:
        pieces = []
        if self.client_profiles:
            pieces.append(f"{len(self.client_profiles)} client")
        if self.engine_profiles:
            pieces.append(f"{len(self.engine_profiles)} engine")
        if self.tier0_profiles:
            pieces.append(f"{len(self.tier0_profiles)} tier0")
        if self.modules:
            pieces.append(f"{len(self.modules)} module")
        if self.editing_modules:
            pieces.append("editing")
        return ", ".join(pieces) or "empty"


EMPTY = Definitions(
    updated="", notes="", modules={}, engine_layouts={}, engine_profiles=(),
    tier0_layouts={}, tier0_profiles=(), client_profiles=(), editing_modules={},
    stock_provenance_files={},
)


def cache_path(root: Path | None = None) -> Path:
    base = application_root() if root is None else Path(root)
    return base / CACHE_NAME


def _object(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise DefinitionsError(f"definitions: {field} must be an object")
    return value


def _list(value, field: str, limit: int) -> list:
    if not isinstance(value, list):
        raise DefinitionsError(f"definitions: {field} must be a list")
    if len(value) > limit:
        raise DefinitionsError(f"definitions: {field} is over its limit")
    return value


def _text(value, field: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise DefinitionsError(f"definitions: {field} must be a bounded string")
    return value


def _sha256(value, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DefinitionsError(f"definitions: {field} is not a SHA-256 digest")
    return value


def _number(value, field: str, limit: int = 0x7FFFFFFF) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"0x[0-9a-f]{1,16}", value):
        result = int(value, 16)
    else:
        raise DefinitionsError(f"definitions: {field} is not an integer")
    if not 0 <= result <= limit:
        raise DefinitionsError(f"definitions: {field} is out of range")
    return result


def _hex_bytes(value, field: str, limit: int = 512) -> bytes:
    if not isinstance(value, str) or len(value) % 2 or len(value) > limit * 2:
        raise DefinitionsError(f"definitions: {field} is not bounded hex")
    if re.fullmatch(r"[0-9a-f]*", value) is None:
        raise DefinitionsError(f"definitions: {field} is not lowercase hex")
    return bytes.fromhex(value)


def _layout(value, field: str, fields: tuple[str, ...]) -> dict[str, int]:
    data = _object(value, field)
    if set(data) != set(fields):
        raise DefinitionsError(f"definitions: {field} has unexpected fields")
    return {name: _number(data[name], f"{field}.{name}") for name in fields}


def _tier0_layout(value, field: str) -> dict[str, int]:
    data = _object(value, field)
    fields = TIER0_LAYOUT_FIELDS + ("data_slot", "extended")
    if set(data) != set(fields):
        raise DefinitionsError(f"definitions: {field} has unexpected fields")
    layout = {name: _number(data[name], f"{field}.{name}") for name in TIER0_LAYOUT_FIELDS}
    if data["data_slot"] not in (41, 43) or type(data["data_slot"]) is not int:
        raise DefinitionsError(f"definitions: {field}.data_slot is invalid")
    if type(data["extended"]) is not bool:
        raise DefinitionsError(f"definitions: {field}.extended must be boolean")
    layout["data_slot"] = data["data_slot"]
    layout["extended"] = int(data["extended"])
    return layout


def _layout_name(value, field: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise DefinitionsError(f"definitions: {field} is not a layout name")
    return value


def _profile(value, field: str, has_signature: bool) -> ClientProfile:
    data = _object(value, field)
    required = {
        "sha256", "image_size", "main_view_setup", "main_view_caller",
        "primary_vtable", "globals", "engine_client", "setup_prologue",
    }
    optional = {"render_fraction", "camera_layout_revision",
                "view_setup_delegate", "signature", "signature_mask"}
    if not required <= set(data) or set(data) - required - optional:
        raise DefinitionsError(f"definitions: {field} has unexpected fields")
    prologue = _hex_bytes(data["setup_prologue"], f"{field}.setup_prologue", 512)
    if len(prologue) != 32:
        raise DefinitionsError(f"definitions: {field}.setup_prologue must be 32 bytes")
    signature = mask = None
    if has_signature and ("signature" in data or "signature_mask" in data):
        if "signature" not in data or "signature_mask" not in data:
            raise DefinitionsError(f"definitions: {field} needs both signature and signature_mask")
        signature = _hex_bytes(data["signature"], f"{field}.signature", 512)
        mask = _hex_bytes(data["signature_mask"], f"{field}.signature_mask", 512)
        if not signature or len(signature) != len(mask) or set(mask) - {0, 1}:
            raise DefinitionsError(f"definitions: {field} has an invalid signature mask")
        overlap = min(len(prologue), len(signature))
        if any(mask[index] and signature[index] != prologue[index] for index in range(overlap)):
            raise DefinitionsError(f"definitions: {field} signature disagrees with its prologue")
    return ClientProfile(
        sha256=_sha256(data["sha256"], f"{field}.sha256"),
        image_size=_number(data["image_size"], f"{field}.image_size", 0x40000000),
        main_view_setup=_number(data["main_view_setup"], f"{field}.main_view_setup"),
        main_view_caller=_number(data["main_view_caller"], f"{field}.main_view_caller"),
        primary_vtable=_number(data["primary_vtable"], f"{field}.primary_vtable"),
        globals=_number(data["globals"], f"{field}.globals"),
        engine_client=_number(data["engine_client"], f"{field}.engine_client"),
        render_fraction=_number(data.get("render_fraction", 0), f"{field}.render_fraction"),
        camera_layout_revision=_number(
            data.get("camera_layout_revision", 0), f"{field}.camera_layout_revision", 255),
        view_setup_delegate=_number(
            data.get("view_setup_delegate", 0), f"{field}.view_setup_delegate"),
        setup_prologue=prologue,
        signature=signature,
        signature_mask=mask,
    )


def _module_hash_list(value, field: str) -> tuple[str, ...]:
    hashes = tuple(_sha256(item, field) for item in _list(value, field, MAX_MODULE_PINS))
    if not hashes:
        raise DefinitionsError(f"definitions: {field} is empty")
    return hashes


def parse(document, *, native_abi: int = 4) -> Definitions:
    """Validate one decoded definitions document. Never repairs bad input."""
    data = _object(document, "document")
    if set(data) - {"format", "native_abi", "updated", "notes", "modules", "engine",
                    "tier0", "client", "editing"}:
        raise DefinitionsError("definitions: document has unexpected fields")
    if data.get("format") != FORMAT or type(data.get("format")) is not int:
        raise DefinitionsError("definitions: unsupported format")
    if data.get("native_abi") != native_abi or type(data.get("native_abi")) is not int:
        raise DefinitionsError("definitions: document targets another native ABI")
    updated = _text(data.get("updated", ""), "updated", 40) if data.get("updated") else ""
    notes = data.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise DefinitionsError("definitions: notes must be a bounded string")

    modules: dict[str, tuple[str, ...]] = {}
    for relative, entry in _object(data.get("modules", {}), "modules").items():
        if relative not in MODULE_RELATIVES:
            raise DefinitionsError(f"definitions: unknown module {relative}")
        entry = _object(entry, f"modules.{relative}")
        if set(entry) - {"accepted"}:
            raise DefinitionsError(f"definitions: modules.{relative} has unexpected fields")
        modules[relative] = _module_hash_list(entry.get("accepted"), f"modules.{relative}.accepted")

    engine = _object(data.get("engine", {}), "engine")
    if set(engine) - {"layouts", "profiles"}:
        raise DefinitionsError("definitions: engine has unexpected fields")
    engine_layouts = {
        _layout_name(name, "engine.layouts"): _layout(value, f"engine.layouts.{name}", ENGINE_LAYOUT_FIELDS)
        for name, value in _object(engine.get("layouts", {}), "engine.layouts").items()
    }
    if len(engine_layouts) > MAX_LAYOUTS:
        raise DefinitionsError("definitions: too many engine layouts")
    engine_profiles = tuple(
        EngineProfile(
            sha256=_sha256(_object(item, "engine.profiles[]").get("sha256"),
                           "engine.profiles[].sha256"),
            image_size=_number(_object(item, "engine.profiles[]").get("image_size"),
                               "engine.profiles[].image_size", 0x40000000),
            layout=_layout_name(_object(item, "engine.profiles[]").get("layout"),
                                "engine.profiles[].layout"),
        )
        for item in _list(engine.get("profiles", []), "engine.profiles", MAX_ENGINE_PROFILES)
    )

    tier0 = _object(data.get("tier0", {}), "tier0")
    if set(tier0) - {"layouts", "profiles"}:
        raise DefinitionsError("definitions: tier0 has unexpected fields")
    tier0_layouts = {}
    for name, value in _object(tier0.get("layouts", {}), "tier0.layouts").items():
        tier0_layouts[_layout_name(name, "tier0.layouts")] = _tier0_layout(
            value, f"tier0.layouts.{name}")
    if len(tier0_layouts) > MAX_LAYOUTS:
        raise DefinitionsError("definitions: too many tier0 layouts")
    tier0_profiles = tuple(
        Tier0Profile(
            sha256=_sha256(_object(item, "tier0.profiles[]").get("sha256"),
                           "tier0.profiles[].sha256"),
            image_size=_number(_object(item, "tier0.profiles[]").get("image_size"),
                               "tier0.profiles[].image_size", 0x40000000),
            layout=_layout_name(_object(item, "tier0.profiles[]").get("layout"),
                                "tier0.profiles[].layout"),
        )
        for item in _list(tier0.get("profiles", []), "tier0.profiles", MAX_TIER0_PROFILES)
    )

    client_profiles = tuple(
        _profile(item, f"client.profiles[{index}]", True)
        for index, item in enumerate(
            _list(_object(data.get("client", {}), "client").get("profiles", []),
                  "client.profiles", MAX_CLIENT_PROFILES))
    )

    editing = _object(data.get("editing", {}), "editing")
    if set(editing) - {"configuration_modules", "stock_provenance_files"}:
        raise DefinitionsError("definitions: editing has unexpected fields")
    editing_modules = {}
    for relative, value in _object(editing.get("configuration_modules", {}),
                                   "editing.configuration_modules").items():
        if relative not in EDITING_MODULE_RELATIVES:
            raise DefinitionsError(f"definitions: unknown editing module {relative}")
        editing_modules[relative] = _module_hash_list(
            value if isinstance(value, list) else [value], f"editing.{relative}")

    stock = {}
    for path, entry in _object(editing.get("stock_provenance_files", {}),
                               "editing.stock_provenance_files").items():
        if (not isinstance(path, str) or len(path) > 512 or "\\" in path
                or path.startswith("/") or ".." in path.split("/")):
            raise DefinitionsError("definitions: unsafe stock provenance path")
        rows = entry if isinstance(entry, list) else [entry]
        if not rows or len(rows) > 4:
            raise DefinitionsError("definitions: invalid stock provenance rows")
        variants = ()
        for row in rows:
            row = _object(row, f"editing.stock_provenance_files.{path}")
            if set(row) != {"bytes", "sha1"}:
                raise DefinitionsError("definitions: invalid stock provenance row")
            digest = row["sha1"]
            if not isinstance(digest, str) or _SHA1.fullmatch(digest) is None:
                raise DefinitionsError("definitions: invalid stock provenance digest")
            size = _number(row["bytes"], f"editing.{path}.bytes", 128 * 1024)
            variants += ((size, digest),)
        stock[path] = variants
    if len(stock) > MAX_STOCK_FILES:
        raise DefinitionsError("definitions: too many stock provenance entries")

    _check_references(engine_layouts, engine_profiles, "engine")
    _check_references(tier0_layouts, tier0_profiles, "tier0")
    return Definitions(
        updated=updated, notes=notes, modules=modules,
        engine_layouts=engine_layouts, engine_profiles=engine_profiles,
        tier0_layouts=tier0_layouts, tier0_profiles=tier0_profiles,
        client_profiles=client_profiles, editing_modules=editing_modules,
        stock_provenance_files=stock,
    )


def _check_references(layouts: dict, profiles: tuple, field: str) -> None:
    for profile in profiles:
        if profile.layout not in layouts:
            raise DefinitionsError(f"definitions: {field} profile names an unknown layout")


def decode(raw: bytes, *, native_abi: int = 4) -> Definitions:
    if not isinstance(raw, (bytes, bytearray)):
        raise DefinitionsError("definitions: document is not bytes")
    if len(raw) > MAX_BYTES:
        raise DefinitionsError("definitions: document is too large")
    try:
        document = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DefinitionsError("definitions: document is not valid UTF-8 JSON") from exc
    return parse(document, native_abi=native_abi)


def load(path: Path, *, native_abi: int = 4) -> Definitions:
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise DefinitionsError("definitions: cache is unreadable") from exc
    return decode(raw, native_abi=native_abi)


_ACTIVE: Definitions | None = None
_ACTIVE_LOCK = threading.Lock()


def current() -> Definitions:
    """Load the bounded cache once; never expose a provisional empty document."""
    global _ACTIVE
    with _ACTIVE_LOCK:
        if _ACTIVE is None:
            try:
                _ACTIVE = load(cache_path())
            except DefinitionsError:
                _ACTIVE = EMPTY
        return _ACTIVE


def adopt(document: Definitions | None) -> None:
    """Replace session definitions after any in-progress first cache read."""
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = document or EMPTY


def reset() -> None:
    """Forget session state; the next current call reloads the cache."""
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = None


def _redirect_allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "raw.githubusercontent.com"


class _RawRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _redirect_allowed(newurl):
            raise DefinitionsError("Unexpected definitions redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str = URL, *, timeout: float = 8.0) -> bytes:
    """Download a bounded definitions document. HTTPS only, no other host."""
    if not _redirect_allowed(url):
        raise DefinitionsError("The definitions URL is not the pinned raw endpoint")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Deadlock-Dolly-Definitions",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.build_opener(_RawRedirect).open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
            declared = response.headers.get("Content-Length")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DefinitionsError(f"definitions: fetch failed ({exc})") from exc
    if len(raw) > MAX_BYTES:
        raise DefinitionsError("definitions: document is too large")
    if declared is not None:
        try:
            if int(declared) != len(raw):
                raise DefinitionsError("definitions: download length mismatch")
        except ValueError as exc:
            raise DefinitionsError("definitions: invalid download length") from exc
    return raw


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dolly-definitions-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh(url: str = URL, *, cache: Path | None = None, timeout: float = 8.0,
            native_abi: int = 4) -> Definitions:
    """Fetch, validate and cache; adopt the result for this process.

    Raises :class:`DefinitionsError` when the download is unusable. A cache
    write failure is logged and ignored: the validated document still applies
    to this session, and the next session simply refetches.

    A valid document always replaces the cache, including an empty one. That is
    how a maintainer withdraws published support.
    """
    raw = fetch(url, timeout=timeout)
    document = decode(raw, native_abi=native_abi)
    adopt(document)
    target = cache_path() if cache is None else Path(cache)
    try:
        _atomic_write(target, raw)
    except OSError as exc:
        LOG.warning("Definitions: could not cache the published document (%s)", exc)
    LOG.info("Definitions: adopted %s", document.describe())
    return document


def module_pins() -> dict[str, tuple[str, ...]]:
    return dict(current().modules)


def native_document(document: Definitions | None = None) -> str:
    """Render the compact line document the injected bridge parses."""
    data = current() if document is None else document
    lines = ["DOLLY_DEFINITIONS 1", "abi 4"]
    for name in sorted(data.engine_layouts):
        layout = data.engine_layouts[name]
        lines.append("+layout %s %s" % (
            name, " ".join(hex(layout[field]) for field in ENGINE_LAYOUT_FIELDS)))
    for profile in data.engine_profiles:
        lines.append("+engine %s %#x %s" % (
            profile.sha256, profile.image_size, profile.layout))
    for name in sorted(data.tier0_layouts):
        layout = data.tier0_layouts[name]
        lines.append("+cvar %s %s %d %d" % (
            name, " ".join(hex(layout[field]) for field in TIER0_LAYOUT_FIELDS),
            layout["data_slot"], int(layout["extended"])))
    for profile in data.tier0_profiles:
        lines.append("+tier0 %s %#x %s" % (
            profile.sha256, profile.image_size, profile.layout))
    for profile in data.client_profiles:
        signature = profile.signature.hex() if profile.signature else "-"
        mask = profile.signature_mask.hex() if profile.signature_mask else "-"
        lines.append(
            "+client %s %#x %#x %#x %#x %#x %#x %#x %#x %#x %s %s %s" % (
                profile.sha256, profile.image_size, profile.main_view_setup,
                profile.main_view_caller, profile.primary_vtable, profile.globals,
                profile.engine_client, profile.render_fraction,
                profile.camera_layout_revision, profile.view_setup_delegate,
                profile.setup_prologue.hex(), signature, mask))
    lines.append("end")
    return "\n".join(lines) + "\n"


def write_native(path: Path, document: Definitions | None = None) -> Path:
    """Write the bridge document beside the deployed DLL."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, native_document(document).encode("ascii"))
    return target
