"""Prepare Dolly-owned settings without replacing a player's Steam settings.

The reviewed launcher owns the stock cfg mounts and the engine's
host_readconfig_ignore_userconfig gate. This module supplies only the private
USRLOCAL directory and child-process environment; it does not control Steam
Cloud, mutate the parent environment, or restore files outside the session.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
from pathlib import Path
import re
import uuid

from . import gameinfo_transaction, graphics_profiles
from .file_ops import _atomic_write, _plain_ancestors
from .game_installation import GamePaths
from .launch_errors import LaunchError
from .convar_response import read_cvar_value
from .runtime import resource_root
from .replays import parse_launch_options
from .settings import _unique_object


EDITING_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "assets" / "editing"
DIRECTORY = "editing-config"
JOURNAL = "editing-session.json"
MAX_BYTES = 128 * 1024
EMPTY_CONVARS = b'"config"\n{\n\t"convars"\n\t{\n\t}\n}\n'

# Audio levels are player preferences, not rendering overrides. Dolly's
# isolated settings folder otherwise starts from game defaults, which can make
# a quiet player's mix unexpectedly loud. Copy only these reviewed audio keys
# from the active Steam profile; performance, input and UI preferences remain
# isolated.
AUDIO_CONVARS = frozenset({
    "volume",
    "dsp_volume",
    "snd_gamevolume",
    "snd_musicvolume",
    "snd_voipvolume",
    "snd_gamevoicevolume",
    "snd_toolvolume",
    "snd_ducktovolume",
    "snd_mute_losefocus",
    "snd_mixahead",
    "snd_gain",
    "snd_duckerattacktime",
    "snd_duckerreleasetime",
    "snd_duckerthreshold",
    "citadel_previous_umuted_audio_level",
    "snd_steamaudio_enable_perspective_correction",
})

# Carry device/display identity only. Performance tiers, rendering quality,
# upscalers, render scale, commands and all unknown fields remain excluded.
DISPLAY_FIELDS = {
    "vendorid": (0, 65535),
    "deviceid": (0, 65535),
    "setting.knowndevice": (0, 1),
    "setting.defaultres": (320, 16384),
    "setting.defaultresheight": (200, 16384),
    "setting.refreshrate_numerator": (0, 1_000_000),
    "setting.refreshrate_denominator": (0, 1_000_000),
    "setting.fullscreen": (0, 1),
    "setting.coop_fullscreen": (0, 1),
    "setting.nowindowborder": (0, 1),
    "setting.mat_vsync": (0, 1),
    "setting.fullscreen_min_on_focus_loss": (0, 1),
    "setting.high_dpi": (0, 1),
    "setting.monitor_index": (0, 31),
    "setting.aspectratiomode": (0, 4),
}


def _read(path: Path) -> bytes:
    if not _plain_ancestors(path) or not path.is_file():
        raise LaunchError(f"Editing settings must be ordinary local files: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise LaunchError(f"Editing settings exceed {MAX_BYTES // 1024} KiB: {path}")
    return data


def _values(data: bytes) -> dict[str, str]:
    return {name: entry.value.value for name, entry in graphics_profiles._document(data)[1].items()}


def _convar_values(data: bytes) -> dict[str, str]:
    """Read one ``config { convars { ... } }`` VCFG without other settings."""
    from . import keyvalues
    roots = keyvalues._parse(data.decode("utf-8-sig"))
    config = keyvalues._named(roots, "config")
    if len(config) != 1 or config[0].children is None:
        raise ValueError("Convar VCFG lacks one config block")
    convars = keyvalues._named(config[0].children, "convars")
    if len(convars) != 1 or convars[0].children is None:
        raise ValueError("Convar VCFG lacks one convars block")
    result = {}
    for entry in convars[0].children:
        if entry.value is None:
            raise ValueError("Convar VCFG contains a non-value entry")
        result[entry.key.value] = entry.value.value
    return result


def _audio() -> tuple[dict[str, str], str | None]:
    """Copy reviewed audio levels from the active profile without other settings."""
    try:
        cfg = Path(graphics_profiles.current_video()).parent
        values: dict[str, str] = {}
        for name in ("machine_convars.vcfg", "user_convars_0_slot0.vcfg"):
            path = cfg / name
            if not path.is_file():
                continue
            try:
                for key, value in _convar_values(_read(path)).items():
                    if (key in AUDIO_CONVARS and isinstance(value, str)
                            and len(value) <= 64 and not any(ord(c) < 32 for c in value)):
                        values[key] = value
            except (OSError, LaunchError, ValueError, TypeError):
                continue
        return values, None
    except (OSError, LaunchError, ValueError, TypeError) as exc:
        return {}, f"Audio preferences were unavailable ({type(exc).__name__}); using game defaults."


def _convars(values: dict[str, str]) -> bytes:
    if not values:
        return EMPTY_CONVARS

    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    data = ('"config"\n{\n\t"convars"\n\t{\n' + "".join(
        f"\t\t{quote(name)}\t\t{quote(value)}\n" for name, value in sorted(values.items())
    ) + "\t}\n}\n").encode("utf-8")
    _convar_values(data)
    return data


def _baseline(editing_root: Path | None = None) -> bytes | None:
    """A declared baseline must be exact; an absent optional one stays absent."""
    editing_root = EDITING_ROOT if editing_root is None else Path(editing_root)
    profile = json.loads(_read(editing_root / "profile.json"), object_pairs_hook=_unique_object)
    if not isinstance(profile, dict) or profile.get("format") != 1:
        raise LaunchError("Dolly's editing profile has an unsupported format.")
    digest = profile.get("video_sha256")
    path = editing_root / "video.txt"
    if digest is None and not path.exists():
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise LaunchError("Dolly's editing video baseline has no reviewed fingerprint.")
    data = _read(path)
    if hashlib.sha256(data).hexdigest() != digest:
        raise LaunchError("Dolly's editing video baseline failed its SHA-256 check.")
    values = _values(data)
    if not graphics_profiles.QUALITY_FIELDS.keys() <= values.keys():
        raise LaunchError("Dolly's editing video baseline lacks required quality settings.")
    return data


def _display() -> tuple[dict[str, str], str | None]:
    """Reading an unavailable active Steam profile is optional, never a write."""
    try:
        values = _values(graphics_profiles.read_video(graphics_profiles.current_video()))
        result = {}
        for name, (minimum, maximum) in DISPLAY_FIELDS.items():
            value = values.get(name)
            if value is not None and re.fullmatch(r"[0-9]{1,7}", value) and minimum <= int(value) <= maximum:
                result[name] = str(int(value))
        return result, None
    except (OSError, LaunchError, ValueError, TypeError) as exc:
        # Do not persist the user's Steam path/account or arbitrary file content.
        return {}, f"Display settings were unavailable ({type(exc).__name__}); using Dolly/game defaults."


def _video(baseline: bytes | None, display: dict[str, str], profile_id: str,
           overrides: dict[str, str] | None = None) -> tuple[bytes | None, dict]:
    """Baseline, then normal display identity, then the profile, then explicit
    Display launch options (which win for this launch)."""
    overrides = overrides or {}
    values = _values(baseline) if baseline is not None else {"version": graphics_profiles.VIDEO_VERSION}
    values.update(display)
    profile = None
    if profile_id:
        if baseline is None:
            raise LaunchError("A reviewed editing video baseline is required to use a saved graphics profile.")
        library = graphics_profiles.load_library()
        profile = next((p for p in library["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            raise LaunchError("Selected graphics profile is missing. Choose a profile in Settings.")
        values.update(profile["values"])
    values.update(overrides)
    if baseline is None and not display and not overrides:
        return None, {"baseline": "game_defaults", "quality_values": {}}
    # Baselines are pinned data, and copied display values are bounded integers.
    # Escaping also keeps future reviewed flat video values in their own token.
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    data = ('"video.cfg"\n{\n' + "".join(
        f"\t{quote(name)}\t{quote(value)}\n" for name, value in values.items()) + "}\n").encode("utf-8")
    _values(data)
    audit = {
        "baseline": "reviewed" if baseline is not None else "game_defaults_with_display",
        "quality_values": {key: value for key, value in values.items() if key in graphics_profiles.QUALITY_FIELDS},
        # What this launch wrote, so in-game changes can be offered for saving.
        "profile_fields": {key: value for key, value in values.items() if key in graphics_profiles.FIELD_LABELS},
    }
    if profile is not None:
        audit["profile"] = {"id": profile["id"], "name": profile["name"]}
    return data, audit


def _environment(paths: GamePaths, directory: Path) -> dict[str, str]:
    # The installed engine forms USRLOCAL%s from its initialized -game value.
    # Dolly supplies an absolute -game path. Include slash spelling and the
    # stock short name too; the runtime gate must confirm the selected root.
    game = str(paths.citadel_dir.resolve())
    suffixes = (game, game.replace("\\", "/"), "citadel", "")
    additions = {"USRLOCAL" + suffix: str(directory) for suffix in suffixes}
    if any("=" in name or "\0" in name for name in additions):
        raise LaunchError("The game path cannot be represented by Dolly's isolated settings environment.")
    return additions


def descriptor(session_dir: Path) -> dict:
    """Read only the settings record belonging to this ordinary owned session."""
    gameinfo_transaction.load_record(session_dir)
    data = json.loads(_read(session_dir / JOURNAL), object_pairs_hook=_unique_object)
    expected = session_dir / DIRECTORY
    if (not isinstance(data, dict) or type(data.get('format')) is not int or data['format'] != 1
            or data.get('owner') != 'Deadlock Dolly'
            or Path(data.get('root', '')).resolve() != expected.resolve()
            or not _plain_ancestors(expected)):
        raise LaunchError("Dolly's isolated editing settings record does not match this session.")
    return data


def verify_runtime(session_dir: Path, query, *, pid: int) -> dict:
    """Confirm the live engine's settings root and config gate before replay load.

    Preparation is not runtime proof. Read the normal console's filesystem
    search paths and current ConVar value; no game or Steam settings are set.
    """
    record = descriptor(session_dir)
    record['runtime_verified'] = False
    _atomic_write(session_dir / JOURNAL, (json.dumps(record, indent=2) + '\n').encode('utf-8'))
    try:
        gate = read_cvar_value('host_readconfig_ignore_userconfig',
                               query('host_readconfig_ignore_userconfig'))
        if gate != 1:
            raise LaunchError('Deadlock did not keep user configuration disabled for this Dolly session.')
        output = query('path')
        expected = ntpath.normcase(ntpath.normpath(str((session_dir / DIRECTORY).resolve())))
        roots = _user_settings_roots(output)
        if not roots or set(roots) != {expected}:
            raise LaunchError("Deadlock's USRLOCAL search path could not be confirmed as Dolly's separate settings folder. "
                              "The replay was not opened; close this session and export diagnostics.")
        record.update(runtime_verified=True, pid=pid, user_configuration_value=gate,
                      search_paths_sha256=hashlib.sha256(output.encode('utf-8')).hexdigest())
        record.pop('runtime_error', None)
    except (OSError, ValueError, RuntimeError) as exc:
        record['runtime_error'] = str(exc)
        _atomic_write(session_dir / JOURNAL, (json.dumps(record, indent=2) + '\n').encode('utf-8'))
        raise LaunchError(f"Dolly's editing configuration check failed: {exc}") from exc
    _atomic_write(session_dir / JOURNAL, (json.dumps(record, indent=2) + '\n').encode('utf-8'))
    return record


def _user_settings_roots(output: str) -> list[str]:
    """Read path IDs as tokens, outside quoted directory names."""
    roots = []
    in_block = False
    for original in output.splitlines():
        line = original.strip()
        if line.startswith('[FileSystem]'):
            line = line[len('[FileSystem]'):].strip()
        registration = re.fullmatch(r'"?USRLOCAL"?(?:\s*[:=])?(?:\s+(.*))?', line, re.I)
        if registration:
            raw = (registration.group(1) or '').strip()
            in_block = not raw
        elif in_block:
            if (not ntpath.isabs(line.strip('"'))
                    and re.match(r'^"?[A-Za-z][A-Za-z0-9_]*"?(?:\s*:|\s+)', line)):
                in_block = False
                continue
            raw = line
        else:
            continue
        if not raw:
            continue
        quoted = re.fullmatch(r'"([^"]+)"', raw)
        if quoted:
            raw = quoted.group(1)
        if not ntpath.isabs(raw) or '"' in raw:
            raise LaunchError('The live USRLOCAL search-path listing could not be read safely.')
        roots.append(ntpath.normcase(ntpath.normpath(raw)))
    return roots


def preview_video(profile_id: str = '') -> tuple[bytes | None, dict]:
    """Describe the clean settings for a future session without writing files."""
    display, warning = _display()
    video, audit = _video(_baseline(), display, profile_id)
    if warning:
        audit['warning'] = warning
    return video, audit


def prepare(session_dir: Path, paths: GamePaths, *, graphics_profile: str = "",
            editing_root: Path | None = None, launch_options: str = "") -> dict:
    """Return child environment additions and a durable settings descriptor.

    Call after the original gameinfo journal exists and before replacing the
    installed gameinfo or starting a process. Every write belongs to this new
    session. The ordinary user's saved settings are only optionally read.
    """
    try:
        session_dir = Path(session_dir)
        if not session_dir.is_absolute() or not _plain_ancestors(session_dir):
            raise LaunchError("The editing session directory must be an ordinary absolute path.")
        record = gameinfo_transaction.load_record(session_dir)
        if Path(record["original_gameinfo"]).resolve() != paths.gameinfo.resolve():
            raise LaunchError("The editing settings journal belongs to another game installation.")
        if not isinstance(graphics_profile, str):
            raise LaunchError("The selected graphics profile must be text.")
        directory = session_dir / DIRECTORY
        journal = session_dir / JOURNAL
        if directory.exists() or directory.is_symlink() or journal.exists() or journal.is_symlink():
            raise LaunchError("This session already has editing settings; existing files were left untouched.")
        environment = _environment(paths, directory)
        baseline = _baseline(editing_root)
        display, warning = _display()
        options = parse_launch_options(launch_options)
        overrides: dict[str, str] = {}
        if '-windowed' in options:
            overrides.update({'setting.fullscreen': '0', 'setting.coop_fullscreen': '0'})
        if '-fullscreen' in options:
            overrides.update({'setting.fullscreen': '1', 'setting.coop_fullscreen': '0',
                              'setting.nowindowborder': '0'})
        if '-noborder' in options:
            overrides['setting.nowindowborder'] = '1'
        for flag, field in (('-w', 'setting.defaultres'), ('-h', 'setting.defaultresheight')):
            if flag in options:
                overrides[field] = options[options.index(flag) + 1]
        if '-refresh' in options:
            overrides['setting.refreshrate_numerator'] = options[options.index('-refresh') + 1]
            overrides['setting.refreshrate_denominator'] = '1'
        video, video_audit = _video(baseline, display, graphics_profile, overrides)
        audio, audio_warning = _audio()
        files = {
            "cfg/machine_convars.vcfg": _convars(audio),
            "cfg/user_convars_0_slot0.vcfg": EMPTY_CONVARS,
        }
        if video is not None:
            files["cfg/video.txt"] = video
        directory.mkdir(exist_ok=False)
        cfg = directory / "cfg"
        cfg.mkdir(exist_ok=False)
        if not _plain_ancestors(cfg):
            raise LaunchError("The editing settings directory became linked during preparation.")
        for relative, data in files.items():
            target = directory / relative
            _atomic_write(target, data)
            if _read(target) != data:
                raise LaunchError("Dolly's isolated editing settings did not read back correctly.")
        audit = {
            "format": 1,
            "owner": "Deadlock Dolly",
            "root": str(directory),
            "environment_keys": sorted(environment),
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "display_fields_preserved": sorted(display),
            "launch_option_fields": sorted(overrides),
            "audio_convar_count": len(audio),
            "audio_convar_names": sorted(audio),
            "user_configuration_gate": "host_readconfig_ignore_userconfig",
            "runtime_verified": False,
            **video_audit,
        }
        if warning is not None:
            audit["warning"] = warning
        if audio_warning is not None:
            audit["audio_warning"] = audio_warning
        _atomic_write(journal, (json.dumps(audit, indent=2) + "\n").encode("utf-8"))
        return {"environment": environment, "audit": audit}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise LaunchError(f"Could not prepare Dolly's isolated editing settings: {exc}") from exc

def session_graphics(session_dir: Path) -> dict | None:
    """Graphics the user changed in-game during a finished Dolly session.

    Compares the session's own video.txt (written by the game) with what the
    launch wrote, over the profile allowlist. Returns None when nothing
    changed or the session has no comparable record. Normal settings are
    never read or written here.

    Applying any setting in-game makes the game rewrite every setting, in its
    own number format and including fields this launch never wrote. Only real
    differences count: a field the launch did not write is compared with the
    game's default, and is left out when that is unknown.
    """
    session_dir = Path(session_dir)
    try:
        audit = json.loads(_read(session_dir / JOURNAL))
        launched = audit.get("profile_fields") if isinstance(audit, dict) else None
        if not isinstance(launched, dict):
            return None
        written = _values(_read(session_dir / DIRECTORY / "cfg" / "video.txt"))
    except (OSError, ValueError, LaunchError):
        return None
    changes = {}
    for key, value in written.items():
        if key not in graphics_profiles.FIELD_LABELS:
            continue
        before = launched.get(key, graphics_profiles.GAME_DEFAULTS.get(key))
        if isinstance(before, str) and not graphics_profiles.same_value(before, value):
            changes[key] = value
    if not changes:
        return None
    try:
        graphics_profiles._quality(changes)
    except LaunchError:
        return None
    profile = audit.get("profile") if isinstance(audit.get("profile"), dict) else None
    return {"profile": profile, "changes": changes}


def save_session_graphics(session_dir: Path, *, profile_id: str = "", name: str = "") -> dict:
    """Save the graphics changed in a finished session into a Dolly profile.

    Exactly the changed settings are saved, so a profile never pins values the
    user did not choose. With profile_id, that profile takes them; otherwise a
    new profile named ``name`` is created and selected for the next launch.
    """
    found = session_graphics(session_dir)
    if found is None:
        raise LaunchError("This Dolly session has no graphics changes to save.")
    library = graphics_profiles.load_library()
    if profile_id:
        profile = next((p for p in library["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            raise LaunchError("That graphics profile no longer exists. Choose a profile in Settings.")
        profile["values"] = graphics_profiles._quality({**profile["values"], **found["changes"]})
    else:
        if any(p["name"].casefold() == name.strip().casefold() for p in library["profiles"]):
            raise LaunchError("A graphics profile with that name already exists.")
        profile = {"id": uuid.uuid4().hex, "name": name.strip(),
                   "video_version": graphics_profiles.VIDEO_VERSION,
                   "values": graphics_profiles._quality(dict(found["changes"]))}
        graphics_profiles._validate_profile(profile)
        library["profiles"].append(profile)
        library["selected"] = profile["id"]
    graphics_profiles.save_library(library)
    return profile
