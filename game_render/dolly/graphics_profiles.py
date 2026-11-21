"""Local Dolly-only graphics presets and recovery for older video.txt transactions.

Profiles copy an allowlist of quality, upscaler/DLSS and display (resolution,
window mode, refresh, vsync) values from a video.cfg. They never replay
gameinfo, commands, GPU/device IDs or the monitor choice.
New launches apply presets inside editing_session's separate settings folder.
Legacy journals remain recoverable until the older owned game process exits.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
import re
import uuid

from .launch_errors import LaunchError
from .convar_response import read_cvar_value
from .keyvalues import _parse, _tokens
from .gameinfo_transaction import load_record as _load_record
from .file_ops import _atomic_write, _plain_ancestors
from .settings import _unique_object, settings_path

MAX_BYTES = 128 * 1024
MAX_PROFILES = 32
VIDEO_VERSION = "20"
JOURNAL = "graphics-session.json"
ORIGINAL = "original.video.txt"
APPLIED = "applied.video.txt"
CURRENT = "Clean Dolly settings (default)"
# Values are copied, not synthesized. Validation below bounds syntax and size;
# it does not claim to know every engine build's accepted quality enum values.
QUALITY_FIELDS = {
    "setting.r_citadel_ssao_quality": "Ambient occlusion",
    "setting.r_citadel_distancefield_ao_quality": "Distance-field occlusion",
    "setting.r_effects_bloom": "Effects bloom",
    "setting.r_post_bloom": "Post-process bloom",
    "setting.r_citadel_antialiasing": "Anti-aliasing",
    "setting.r_depth_of_field": "Depth of field",
    "setting.r_arealights": "Area lights",
    "setting.r_texture_stream_mip_bias": "Texture detail bias",
    "setting.r_dashboard_render_quality": "Dashboard quality",
    "setting.r_particle_depth_feathering": "Soft particles",
    "setting.shaderquality": "Shader quality",
    "setting.r_citadel_fog_quality": "Fog quality",
    "setting.r_citadel_motion_blur": "Motion blur",
    "setting.r_citadel_shadow_quality": "Shadow quality",
    "setting.useadvanced": "Advanced graphics controls",
}
UPSCALER_FIELDS = {
    "setting.r_citadel_upscaling": "Upscaler",
    "setting.r_citadel_dlss_settings_mode": "DLSS mode",
    "setting.r_dlss_preset": "DLSS preset",
    "setting.mat_viewportscale": "Render scale",
    "setting.r_citadel_fsr_rcas_sharpness": "FSR sharpness",
    "setting.r_citadel_fsr2_sharpness": "FSR 2 sharpness",
}
# Bounded integers, as editing_session copies them from normal settings.
DISPLAY_FIELDS = {
    "setting.defaultres": ("Width", 320, 16384),
    "setting.defaultresheight": ("Height", 200, 16384),
    "setting.refreshrate_numerator": ("Refresh rate", 0, 1_000_000),
    "setting.refreshrate_denominator": ("Refresh rate divisor", 0, 1_000_000),
    "setting.fullscreen": ("Fullscreen", 0, 1),
    "setting.coop_fullscreen": ("Borderless fullscreen", 0, 1),
    "setting.nowindowborder": ("No window border", 0, 1),
    "setting.mat_vsync": ("Vertical sync", 0, 1),
    "setting.aspectratiomode": ("Aspect ratio", 0, 4),
}
FIELD_LABELS = {**QUALITY_FIELDS, **UPSCALER_FIELDS,
                **{key: spec[0] for key, spec in DISPLAY_FIELDS.items()}}
LIBRARY_FORMATS = (1, 2)
# The game's own defaults for the profile fields a Dolly launch does not write
# (its cvar defaults, as it writes them to video.txt). Used only to tell a real
# in-game change from an untouched setting; a launch never writes these.
GAME_DEFAULTS = {
    "setting.r_citadel_dlss_settings_mode": "0",
    "setting.r_dlss_preset": "5",
    "setting.r_citadel_fsr_rcas_sharpness": "0.25",
    "setting.r_citadel_fsr2_sharpness": "0.5",
}
_PLAIN_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def same_value(first: str, second: str) -> bool:
    """Whether two video.txt values mean the same; the game rewrites 1.000000 as 1."""
    if first == second:
        return True
    if _PLAIN_NUMBER.fullmatch(first) and _PLAIN_NUMBER.fullmatch(second):
        return Decimal(first) == Decimal(second)
    return first.casefold() == second.casefold()


def _read(path: Path) -> bytes:
    if not _plain_ancestors(path) or not path.is_file():
        raise LaunchError(f"Graphics configuration must be an ordinary local file: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise LaunchError(f"Graphics configuration exceeds {MAX_BYTES // 1024} KiB: {path}")
    return data


def read_video(path: Path) -> bytes:
    """Read bounded, ordinary local video settings for a profile preview."""
    return _read(path)


def excluded_fields(data: bytes, profile: dict) -> list[str]:
    """Names kept from the destination rather than saved in this profile."""
    return sorted(_values(data).keys() - profile["values"].keys())


def capture_source() -> Path:
    """Resolve saved graphics only when Deadlock has finished writing them."""
    from . import game_processes
    if game_processes._game_is_running(game_processes.running_processes()):
        raise ValueError("Close Deadlock before saving its graphics profile, so the file contains its final saved settings.")
    return current_video()


def _document(data: bytes):
    try:
        if len(data) > MAX_BYTES:
            raise ValueError("file is too large")
        text = data.decode("utf-8-sig")
        tokens = _tokens(text)
        # Reject nesting/conditions/includes before invoking the recursive parser.
        if sum(t.kind == "brace" for t in tokens) != 2 or any(t.kind == "condition" for t in tokens):
            raise ValueError("expected a single flat video.cfg block")
        roots = _parse(text)
        if len(roots) != 1 or roots[0].key.value.casefold() != "video.cfg" or roots[0].children is None:
            raise ValueError("expected a video.cfg block")
        entries = {}
        for entry in roots[0].children:
            key = entry.key.value.casefold()
            if entry.value is None or key in entries:
                raise ValueError(f"duplicate or nested video setting: {key}")
            entries[key] = entry
        if "version" not in entries or entries["version"].value.value != VIDEO_VERSION:
            raise ValueError(f"only video.cfg Version {VIDEO_VERSION} is supported; save a new profile after a format update")
        return text, entries
    except (ValueError, LaunchError) as exc:
        raise LaunchError("Cannot use this video.txt: " + str(exc).replace("gameinfo.gi", "video.txt")) from exc


def _values(data: bytes) -> dict[str, str]:
    return {key: entry.value.value for key, entry in _document(data)[1].items()}


def _semantic_values(data: bytes) -> dict:
    values = _values(data)
    for key in values.keys() & FIELD_LABELS.keys():
        value = values[key].casefold()
        if value in ("true", "false"):
            values[key] = Decimal(int(value == "true"))
        elif re.fullmatch(r"-?\d+(?:\.\d+)?", value) and len(value) <= 32:
            values[key] = Decimal(value)
    return values


def _quality(values):
    if not isinstance(values, dict) or not values or not values.keys() <= FIELD_LABELS.keys():
        raise LaunchError("Graphics profile has missing or unsupported quality fields.")
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 32:
            raise LaunchError(f"Invalid graphics value: {key}")
        if key in DISPLAY_FIELDS:
            _label, minimum, maximum = DISPLAY_FIELDS[key]
            if not re.fullmatch(r"[0-9]{1,7}", value) or not minimum <= int(value) <= maximum:
                raise LaunchError(f"Invalid display value: {key}")
            continue
        if value.casefold() in ("true", "false"):
            continue
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", value) or not math.isfinite(float(value)) or abs(float(value)) > 10000:
            raise LaunchError(f"Invalid numeric graphics value: {key}")
    return values


def profile_from_bytes(name: str, data: bytes) -> dict:
    values = _values(data)
    profile = {"id": uuid.uuid4().hex, "name": name, "video_version": VIDEO_VERSION,
               "values": _quality({key: value for key, value in values.items() if key in FIELD_LABELS})}
    _validate_profile(profile)
    return profile


def _validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) != {"id", "name", "video_version", "values"}:
        raise LaunchError("Unrecognized graphics profile format.")
    if not isinstance(profile["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", profile["id"]):
        raise LaunchError("Invalid graphics profile ID.")
    name = profile["name"]
    if (not isinstance(name, str) or not 1 <= len(name) <= 64 or name != name.strip()
            or any(ord(c) < 32 for c in name) or name.casefold() == CURRENT.casefold()):
        raise LaunchError("Use a unique graphics profile name of 1–64 characters.")
    if profile["video_version"] != VIDEO_VERSION:
        raise LaunchError("Graphics profile format is no longer supported; save a new profile.")
    _quality(profile["values"])


def library_path() -> Path:
    return settings_path().parent / "graphics-profiles.json"


def _validate_library(library):
    if (not isinstance(library, dict) or set(library) != {"format", "selected", "profiles"}
            or type(library["format"]) is not int or library["format"] not in LIBRARY_FORMATS
            or not isinstance(library["profiles"], list) or len(library["profiles"]) > MAX_PROFILES):
        raise LaunchError("Unrecognized graphics profile library format.")
    ids, names = {""}, set()
    for profile in library["profiles"]:
        _validate_profile(profile)
        if profile["id"] in ids or profile["name"].casefold() in names:
            raise LaunchError("Duplicate graphics profile ID or name.")
        ids.add(profile["id"])
        names.add(profile["name"].casefold())
    if not isinstance(library["selected"], str) or library["selected"] not in ids:
        raise LaunchError("Selected graphics profile is missing.")
    return library


def load_library() -> dict:
    path = library_path()
    if not path.exists():
        return {"format": 1, "selected": "", "profiles": []}
    try:
        return _validate_library(json.loads(_read(path), object_pairs_hook=_unique_object))
    except (OSError, ValueError, LaunchError, RecursionError) as exc:
        raise LaunchError(f"Could not read graphics profiles at {path}: {exc}. The file was left untouched.") from exc


def save_library(library: dict) -> None:
    # Format 2 profiles may carry upscaler and display values.
    library = {**library, "format": 2}
    _validate_library(library)
    data = (json.dumps(library, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_BYTES:
        raise LaunchError("Graphics profile library is too large.")
    path = library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _plain_ancestors(path.parent) or (path.exists() and not _plain_ancestors(path)):
        raise LaunchError("Linked graphics library left untouched.")
    _atomic_write(path, data)


def steam_root() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value = winreg.QueryValueEx(key, "SteamPath")[0]
        root = Path(value)
        if not root.is_absolute() or not _plain_ancestors(root) or not (root / "steam.exe").is_file():
            raise ValueError("invalid Steam directory")
        return root
    except (ImportError, OSError, ValueError, TypeError) as exc:
        raise LaunchError("Cannot find this Windows user's Steam installation. Open Steam and sign in, or use current game settings.") from exc


def active_account() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            value = winreg.QueryValueEx(key, "ActiveUser")[0]
        if type(value) is not int or not 0 < value < 2**32:
            raise ValueError("no active Steam user")
        return str(value)
    except (ImportError, OSError, ValueError, TypeError) as exc:
        raise LaunchError("No active Steam user was found. Sign in to Steam before selecting graphics settings.") from exc


def video_path(account: str, root: Path | None = None) -> Path:
    if not isinstance(account, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", account) or int(account) >= 2**32:
        raise LaunchError("Invalid Steam user in graphics recovery record.")
    root = steam_root() if root is None else root
    target = root / "userdata" / account / "1422450/local/cfg/video.txt"
    if not _plain_ancestors(target) or not target.is_file():
        raise LaunchError(f"No ordinary Deadlock video.txt exists for the active Steam user. Save graphics settings in Deadlock first. Expected: {target}")
    return target


def current_video() -> Path:
    return video_path(active_account())


def preview(profile: dict, data: bytes) -> tuple[bytes, list[str]]:
    _validate_profile(profile)
    text, entries = _document(data)
    missing = profile["values"].keys() - entries.keys()
    if missing:
        raise LaunchError("Current video.txt lacks profile settings; save a new profile: " + ", ".join(sorted(missing)))
    edits, lines = [], []
    for key, value in profile["values"].items():
        token = entries[key].value
        if value != token.value:
            edits.append((token.start, token.end, '"' + value + '"'))
        lines.append(f"{FIELD_LABELS[key]}: {token.value} → {value}")
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    result = (b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b"") + text.encode("utf-8")
    return result, lines


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save_journal(session: Path, record: dict) -> None:
    _atomic_write(session / JOURNAL, (json.dumps(record, indent=2) + "\n").encode("utf-8"))


def _transaction(session: Path):
    _load_record(session)
    try:
        record = json.loads(_read(session / JOURNAL), object_pairs_hook=_unique_object)
        if (not isinstance(record, dict) or record.get("format") != 1
                or record.get("state") not in ("prepared", "applied", "conflict", "restored")):
            raise ValueError("unrecognized graphics journal")
        root = steam_root()
        target = video_path(record["account"], root)
        if Path(record["steam_root"]) != root or Path(record["target"]) != target:
            raise ValueError("graphics target does not match this Steam installation")
        mode = record["original_mode"]
        if type(mode) is not int or mode < 0 or mode > 0o777:
            raise ValueError("invalid original file mode")
        original, applied = _read(session / ORIGINAL), _read(session / APPLIED)
        if _hash(original) != record["original_sha256"] or _hash(applied) != record["applied_sha256"]:
            raise ValueError("graphics backup failed its hash check")
        expected, _ = preview(record["profile"], original)
        if applied != expected:
            raise ValueError("applied graphics backup does not match the recorded profile")
        return record, target, original, applied
    except (OSError, ValueError, KeyError, TypeError, LaunchError) as exc:
        raise LaunchError(f"Graphics recovery needs attention: {exc}. Original backup: {session / ORIGINAL}") from exc


def pending(session: Path) -> bool:
    path = session / JOURNAL
    if not path.exists():
        return False
    try:
        record = json.loads(_read(path), object_pairs_hook=_unique_object)
        if not isinstance(record, dict) or record.get("format") != 1:
            raise ValueError("unrecognized journal")
        return record.get("state") != "restored"
    except (OSError, ValueError) as exc:
        raise LaunchError(f"Cannot read graphics recovery record: {path}: {exc}") from exc


def restore(session: Path) -> None:
    """Caller must establish that no game is running. Never discard conflicts."""
    if not pending(session):
        return
    record, target, original, applied = _transaction(session)
    current = _read(target)
    safe = current in (original, applied)
    if not safe:
        try:
            # Engine reserialization may change whitespace/order, but changes to
            # ANY value (including unmanaged keys) are a conflict, not permission.
            safe = _semantic_values(current) in (_semantic_values(original), _semantic_values(applied))
        except LaunchError:
            safe = False
    if not safe:
        record["state"] = "conflict"
        _save_journal(session, record)
        raise LaunchError(f"video.txt changed during the Dolly session. Newer settings were left untouched. "
                          f"Compare with the original backup: {session / ORIGINAL}. Graphics recovery remains pending.")
    # Recheck before replacing, including formatting-only changes made mid-read.
    if _read(target) != current:
        raise LaunchError(f"video.txt is still changing; close Steam settings and retry recovery. Backup: {session / ORIGINAL}")
    _atomic_write(target, original, record["original_mode"])
    if _read(target) != original:
        raise LaunchError(f"Original graphics settings did not read back correctly. Backup: {session / ORIGINAL}")
    record["state"] = "restored"
    _save_journal(session, record)


def audit_runtime(session: Path, query) -> dict | None:
    """Read render CVARs once; record overrides without fighting engine settings.

    Non-render settings are file-only evidence. Matching CVARs do not establish
    visual correctness. The controller treats failures here as diagnostic only.
    """
    if (session / 'editing-session.json').exists() and not (session / JOURNAL).exists():
        from .editing_session import descriptor
        record = descriptor(session)
        values = _quality(record['quality_values'])
        profile_name = record.get('profile', {}).get('name', CURRENT)
    elif (session / JOURNAL).exists():
        # Retain exact recovery and diagnostics for older versions' transactions.
        record, _target, _original, _applied = _transaction(session)
        values = record["profile"]["values"]
        profile_name = record['profile']['name']
    else:
        return None
    names = {key.removeprefix("setting."): value for key, value in values.items() if key.startswith("setting.r_")}
    report = {"profile": profile_name, "observed": {}, "mismatches": [], "unreadable": [],
              "not_echoed": [],
              "file_only": sorted(key for key in values if not key.startswith("setting.r_")),
              "visual_verified": False}
    for name, value in names.items():
        # Keep each observation tied to its own console request. A partial
        # multi-command reply must not hide otherwise readable trailing values.
        output = query(name)
        if not str(output or "").strip():
            # Deadlock hides some render CVARs from the console echo. The
            # command is accepted but prints nothing, so there is no value to
            # compare. That is not a mismatch and not a broken profile; record
            # it separately instead of warning on every session.
            report["not_echoed"].append(name)
            continue
        try:
            actual = read_cvar_value(name, output)
        except ValueError:
            report["unreadable"].append(name)
            continue
        expected = float(value) if value.casefold() not in ("true", "false") else float(value.casefold() == "true")
        report["observed"][name] = actual
        if not math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6):
            report["mismatches"].append(name)
    if report["mismatches"] or report["unreadable"]:
        report["warning"] = "Some graphics profile values differ or could not be checked in the game. See the activity log or export diagnostics."
    _atomic_write(session / "graphics-runtime.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    return report
