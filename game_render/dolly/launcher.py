"""Windows-only Deadlock development launcher with recoverable config mounting.

``game_path`` may be the Deadlock installation root, its ``game`` or ``citadel``
directory, or ``game/bin/win64/deadlock.exe`` (legacy ``citadel.exe`` is also
supported). Steam launch settings are not edited.
The current gameinfo is backed up and temporarily replaced with a reviewed editing
configuration and plugin mount. Original bytes are restored after unlocker
initialization or game exit; recovery also runs before the next launch.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any
import uuid
from .assert_watcher import dismiss_assert_dialogs
from . import gameinfo_transaction, game_processes, session_recovery
from .game_installation import GamePaths, GAME_EXECUTABLE_NAMES, _find_game_executable, validate_game
# Compatibility exports for existing launcher callers.
from .launch_errors import LaunchError
from .file_ops import _atomic_write
from .keyvalues import _Token, _Entry, _tokens, _parse, _named
from .runtime import application_root, resource_root, external_program_environment
from .native_bridge import NativeBridge, ABI as NATIVE_ABI
from ._runtime_generated import Unlocker as _Unlocker
from .panorama_assets import verify_stock_ui_assets
from . import __version__ as DOLLY_VERSION
from .compatibility import (
    CompatibilityError,
    SUPPORTED,
    UNSUPPORTED,
    accepted_pins,
    merged_pins,
    scan_game_modules,
)
from . import definitions

PACKAGE_ROOT = application_root(Path(__file__).resolve().parent.parent)
UNLOCKER_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "third_party" / "cvar_unlocker"
NATIVE_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "native"
EDITING_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "assets" / "editing"
CONFETTI_PACK = NATIVE_ROOT / "assets" / "confetti" / "pak01_dir.vpk"
CONFETTI_PACK_SHA256 = "99c0325fe333bfa12c3f23a2808767c2fd27b49fe0e5abe4531fa472519f8ae6"
UI_OVERRIDE_PACK = NATIVE_ROOT / "assets" / "ui" / "pak02_dir.vpk"
UI_OVERRIDE_PACK_SHA256 = "1f6619324ebccebe0a88c47e1b7754703ed090e3ddff57a68873bc49eddccfc5"
UNLOCKER_SHA256 = _Unlocker.SHA256
# Accepted game-module SHA-256 pins come from native/profiles/manifest.json, the
# single source of truth shared with the native bridge and its build tests.
try:
    NATIVE_GAME_SHA256 = accepted_pins()
except CompatibilityError:
    NATIVE_GAME_SHA256 = {}


def game_pins() -> dict[str, tuple[str, ...]]:
    """Reviewed module pins plus published definitions. Reviewed values win."""
    return merged_pins(NATIVE_GAME_SHA256, definitions.module_pins())


STEAM_APP_ID = "1422450"
# Mod managers (Deadlock Mod Manager, Grimoire, the manual guide) mount their
# addon folders with ordinary SearchPaths entries; the reviewed editing baseline
# omits them, and mod_policy decides which are carried into a session.
# Grimoire: citadel/grimoire, citadel/addons, citadel/addonsN and
# citadel/deadworks_addons/vpks. DMM: citadel/addons[N][/<profile>] and the
# Deadworks launcher's citadel/deadworks_mods. Hyperline, modlock and
# hand-made folders use other names, so any extra Game mount below citadel/ is
# a candidate; mod_policy refuses render-only packs and anything that resolves
# outside the game folder.
ADDON_MOUNT_KEYS = {
    "game": "Game",
    "mod": "Mod",
    "write": "Write",
    "addonroot": "AddonRoot",
    "officialaddonroot": "OfficialAddonRoot",
}
ADDON_GAME_MOUNTS = re.compile(r"^citadel/[^/].*$", re.IGNORECASE)
ADDON_WRITE_PATHS = {"citadel", "core"}


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def _editing_gameinfo(paths: GamePaths, *, provenance: dict | None = None) -> str:
    """Verify config consumers, stock provenance and the reviewed unlocker pair.

    Competitive gameinfo files can alter much more than ConVars. Never guess
    which entries are stock, or mount an old full gameinfo after a game update.
    This check is separate from, and does not authorize, native injection.
    """
    from .compatibility import CAMERA_MODULE_RELATIVES, hash_file
    from .stock_provenance import CONFIGURATION_MODULES, verify
    try:
        profile = json.loads((EDITING_ROOT / "profile.json").read_text(encoding="utf-8"))
        data = (EDITING_ROOT / "gameinfo.gi").read_bytes()
        if (profile.get("format") != 1
                or set(profile.get("modules", {})) != set(CAMERA_MODULE_RELATIVES)
                or set(profile.get("configuration_modules", {})) != set(CONFIGURATION_MODULES)
                or hashlib.sha256(data).hexdigest() != profile.get("gameinfo_sha256")):
            raise ValueError("Editing configuration failed its integrity check")
        # Published definitions may accept an updated module identity. They are
        # additive: the bundled pin always stays accepted.
        published = definitions.current()
        for relative in CONFIGURATION_MODULES:
            expected = profile["configuration_modules"][relative]
            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise ValueError("Invalid editing configuration consumer fingerprint")
            accepted = {expected, *published.editing_modules.get(relative, ())}
            if hash_file(paths.game_dir / relative) not in accepted:
                raise LaunchError("Dolly's editing configuration has not been reviewed for this game build. "
                                  "Update Dolly before launching. Your gameinfo.gi was not changed.")
        server_hash = profile.get("unlocker_server_sha256")
        if not isinstance(server_hash, str) or re.fullmatch(r"[0-9a-f]{64}", server_hash) is None:
            raise ValueError("Invalid editing unlocker server fingerprint")
        # The bundled cvar unlocker is a patched copy of this exact server
        # binary, so a new server.dll always needs a matching Dolly release.
        # Published definitions must not widen this gate.
        if hash_file(paths.game_dir / "citadel/bin/win64/server.dll") != server_hash:
            raise LaunchError("Dolly's cvar unlocker has not been reviewed for this server build. "
                              "Update Dolly before launching. Your gameinfo.gi was not changed.")
        text = data.decode("utf-8")
        roots = _named(_parse(text), "GameInfo")
        if len(roots) != 1 or roots[0].children is None:
            raise ValueError("Invalid editing GameInfo block")
        receipt = verify(paths, EDITING_ROOT, profile, _steam_roots(),
                         extra_files=published.stock_provenance_files)
        if provenance is not None:
            provenance.update(receipt)
        return text
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise LaunchError(f"Could not verify Dolly's editing configuration: {exc}. "
                          "Reinstall or update Dolly. Your gameinfo.gi was not changed.") from exc


def make_gameinfo(original: str, overlay_name: str, *, isolated_settings: bool = False) -> str:
    """Temporarily mount the plugin in current FileSystem/SearchPaths only.

    The generated overlay name is a restricted, single relative directory.
    All existing relative paths retain their meaning because the file stays in
    its original directory. Other blocks stay exact.
    """
    if not re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay_name):
        raise LaunchError("Invalid temporary plugin directory name.")
    if not isinstance(isolated_settings, bool):
        raise LaunchError("Editing settings isolation must be a boolean.")
    roots = _named(_parse(original), "GameInfo")
    if len(roots) != 1 or roots[0].children is None:
        raise LaunchError("Expected one GameInfo block in the current gameinfo.gi.")
    filesystems = _named(roots[0].children, "FileSystem")
    if len(filesystems) != 1 or filesystems[0].children is None:
        raise LaunchError("Expected one FileSystem block in the current gameinfo.gi.")
    searches = _named(filesystems[0].children, "SearchPaths")
    if len(searches) != 1 or searches[0].children is None:
        raise LaunchError("Expected one FileSystem/SearchPaths block in gameinfo.gi.")
    search = searches[0]
    entries = search.children
    assert search.opening and search.closing
    if any(entry.children is not None for entry in entries):
        raise LaunchError("Nested SearchPaths entries are not supported; original file was not changed.")
    if not any(entry.key.value.casefold() == "game" for entry in entries):
        raise LaunchError("Current gameinfo.gi has no Game search path.")

    edits: list[tuple[int, int, str]] = []
    for entry in entries:
        assert entry.value is not None
        raw = entry.value.value.replace("\\", "/")
        if isolated_settings and entry.key.value.casefold() in {"mod", "write"}:
            edits.append((entry.key.start, entry.end, "// Dolly session-only settings mount replaces this entry."))
            continue
        if entry.key.value.casefold() == "game" and raw.strip("/").casefold() == "citadel/cvar_unlocker":
            # Avoid mounting a user's second unlocker in the development session.
            edits.append((entry.key.start, entry.end, "// Existing unlocker mount temporarily replaced by Dolly."))
            continue

    newline = "\r\n" if "\r\n" in original else "\n"
    first_game = next(entry for entry in entries if entry.key.value.casefold() == "game")
    line_start = original.rfind("\n", 0, first_game.key.start) + 1
    prefix = original[line_start:first_game.key.start]
    indent = prefix if not prefix.strip() else "\t\t\t"
    additions = ["// Dolly temporary development mount; original bytes are backed up in this session's logs."]
    kinds = {entry.key.value.casefold() for entry in entries}
    if isolated_settings:
        # Defaults can explicitly request MOD, so GAME priority alone cannot
        # keep modified default bindings/schema out of the editing session.
        additions.append('Mod\t' + _quote(overlay_name + "/cvar_unlocker"))
        additions.append('Mod\t"citadel"')
        # The engine prefixes Write with its game directory; absolute suffixes
        # are not supported here. Unclassified session files stay in our mount.
        additions.append('Write\t' + _quote(overlay_name + '/session-files'))
    elif "mod" not in kinds:
        additions.append('Mod\t"citadel"')
    if not isolated_settings and "write" not in kinds:
        additions.append('Write\t"citadel"')
    additions.append('Game\t' + _quote(overlay_name + "/cvar_unlocker"))
    insertion = (newline + indent).join(additions) + newline + indent
    edits.append((first_game.key.start, first_game.key.start, insertion))
    result = original
    # Replacement precedes insertion at identical start positions.
    for start, end, value in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        result = result[:start] + value + result[end:]
    _parse(result)
    return result


def _search_paths_entries(entries: list[_Entry]) -> list[_Entry]:
    roots = _named(entries, "GameInfo")
    if len(roots) != 1 or roots[0].children is None:
        raise LaunchError("Expected one GameInfo block in the current gameinfo.gi.")
    filesystems = _named(roots[0].children, "FileSystem")
    if len(filesystems) != 1 or filesystems[0].children is None:
        raise LaunchError("Expected one FileSystem block in the current gameinfo.gi.")
    searches = _named(filesystems[0].children, "SearchPaths")
    if len(searches) != 1 or searches[0].children is None:
        raise LaunchError("Expected one FileSystem/SearchPaths block in gameinfo.gi.")
    if any(entry.children is not None for entry in searches[0].children):
        raise LaunchError("Nested SearchPaths entries are not supported; original file was not changed.")
    return searches[0].children


def mod_search_paths(text: str) -> list[tuple[str, str]]:
    """Addon mounts a mod manager installed into the live gameinfo.gi.

    Deadlock Mod Manager, Grimoire and the manual modding guide append
    ``citadel/addons`` shards, ``citadel/grimoire`` or
    ``citadel/deadworks_addons/vpks`` game mounts along with their Mod/Write
    paths and addon roots. Only these entries are carried into Dolly's session
    file; everything else still comes from the reviewed editing baseline.
    """
    mounts: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in _search_paths_entries(_parse(text)):
        if entry.value is None:
            raise LaunchError("Could not read a game search path from the installed gameinfo.gi.")
        key = entry.key.value.casefold()
        if key not in ADDON_MOUNT_KEYS:
            continue
        path = entry.value.value.replace("\\", "/").strip("/")
        if key == "game" and ADDON_GAME_MOUNTS.fullmatch(path) is None:
            continue
        if key in ("mod", "write") and path.casefold() not in ADDON_WRITE_PATHS:
            continue
        mount = (ADDON_MOUNT_KEYS[key], path)
        if mount not in seen:
            seen.add(mount)
            mounts.append(mount)
    return mounts


def _addon_config_block(text: str) -> str | None:
    roots = _named(_parse(text), "GameInfo")
    if len(roots) != 1 or roots[0].children is None:
        return None
    for entry in roots[0].children:
        if entry.key.value.casefold() == "addonconfig" and entry.children is not None:
            return text[entry.key.start:entry.end]
    return None


def merge_addon_mounts(original: str, editing: str,
                       mounts: tuple[tuple[str, str], ...] | list[tuple[str, str]] | None = None) -> str:
    """Carry the installed gameinfo.gi's addon mounts into Dolly's baseline.

    The reviewed baseline stays byte-identical on disk; only the temporary
    session file receives the user's own mount lines, so their mods keep loading
    while competitive rendering settings still come from the reviewed file.
    ``mounts`` restricts this to the entries a Dolly session may load; by default
    every recognised addon entry is carried (legacy callers).
    """
    explicit = mounts is not None
    mounts = tuple(mounts) if explicit else mod_search_paths(original)
    config = None if explicit and not mounts else _addon_config_block(original)
    if not mounts and config is None:
        return editing
    newline = "\r\n" if "\r\n" in editing else "\n"
    result = editing
    if config is not None:
        children = _named(_parse(result), "GameInfo")[0].children
        assert children is not None
        if not any(entry.key.value.casefold() == "addonconfig" for entry in children):
            filesystem = _named(children, "FileSystem")[0]
            block = config.strip().replace("\r\n", "\n").replace("\n", newline)
            result = result[:filesystem.key.start] + block + newline + result[filesystem.key.start:]
    entries = _search_paths_entries(_parse(result))
    existing = {(entry.key.value.casefold(), entry.value.value.replace("\\", "/").strip("/").casefold())
                for entry in entries if entry.value is not None}
    additions: list[str] = []
    mounted_addons = False
    for key, path in mounts:
        if (key.casefold(), path.casefold()) in existing:
            continue
        existing.add((key.casefold(), path.casefold()))
        mounted_addons = mounted_addons or key.casefold() == "game"
        additions.append(f'{key}\t{_quote(path)}')
    if mounted_addons:
        # Mounting folders ahead of citadel without these write paths reproduces
        # the game's "Unable to read default keybinding configuration" startup
        # failure that all three mod managers avoid by declaring them.
        for key, path in (("Mod", "citadel"), ("Write", "citadel"), ("Mod", "core"), ("Write", "core")):
            if (key.casefold(), path.casefold()) in existing:
                continue
            existing.add((key.casefold(), path.casefold()))
            additions.append(f'{key}\t{_quote(path)}')
    if not additions:
        _parse(result)
        return result
    game_entries = [entry for entry in entries if entry.key.value.casefold() == "game"]
    if not game_entries:
        raise LaunchError("Current gameinfo.gi has no Game search path.")
    first_game = game_entries[0]
    line_start = result.rfind("\n", 0, first_game.key.start) + 1
    prefix = result[line_start:first_game.key.start]
    indent = prefix if not prefix.strip() else "\t\t\t"
    insertion = (newline + indent).join(additions) + newline + indent
    result = result[:first_game.key.start] + insertion + result[first_game.key.start:]
    _parse(result)
    return result


def _game_is_running(processes: set[str]) -> bool:
    return game_processes._game_is_running(processes)


def _already_running_message(details, selected_executable: Path) -> str:
    """Name the process that blocks launch, including an install mismatch.

    The process-name guard is deliberately conservative. Without PID, path and
    session, a hidden or orphaned game looks exactly like Dolly being stuck on
    an old state, so the refusal must carry that evidence.
    """
    selected = os.path.normcase(os.path.abspath(str(selected_executable)))
    parts = []
    different_install = False
    for process in details:
        path = process.path or "path unavailable"
        if process.path and os.path.normcase(os.path.abspath(process.path)) != selected:
            different_install = True
        session = ("session " + str(process.session_id)
                   if process.session_id is not None else "session unavailable")
        window = (f'visible window "{" ".join(str(process.window_title).split())}"'
                  if process.window_title else "no visible window")
        parts.append(f"PID {process.pid}, {path}, {session}, {window}")
    if not parts:
        return ("Deadlock is already running. Exit Deadlock before using Dolly; "
                "Dolly only controls the development session it launches.")
    message = "Deadlock is already running: " + "; ".join(parts) + "."
    if different_install:
        message += f" That process is from a different installation than Dolly's selection ({selected_executable})."
    else:
        message += f" Dolly is configured for {selected_executable}."
    return message + " Exit Deadlock, then retry."


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        import winreg
        for hive, key, value in [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
    for name in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(name):
            roots.append(Path(os.environ[name]) / "Steam")
    return list(dict.fromkeys(roots))


def _library_path(value: str) -> Path:
    """Map a Linux Steam library path onto Wine's Z: drive, which Proton roots at /."""
    if os.name == "nt" and value.startswith("/"):
        return Path("Z:" + value)
    return Path(value)


def discover_game() -> Path | None:
    """Return the installation root from Steam registry/library manifests.

    Each library's manifest is read in turn. Under Proton the prefix's Steam
    folder lists only the Linux Steam root, whose manifest lists the rest.
    """
    libraries = _steam_roots()
    for root in libraries:
        manifest = root / "steamapps" / "libraryfolders.vdf"
        try:
            entries = _parse(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, LaunchError):
            continue
        for section in _named(entries, "libraryfolders"):
            for entry in section.children or []:
                if entry.children:
                    folders = [folder.value.value for folder in _named(entry.children, "path") if folder.value]
                elif entry.key.value.isdigit() and entry.value:
                    folders = [entry.value.value]
                else:
                    folders = []
                for folder in map(_library_path, folders):
                    if folder not in libraries:
                        libraries.append(folder)
    for library in libraries:
        install_name = "Deadlock"
        try:
            app = _parse((library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf").read_text(encoding="utf-8-sig"))
            states = _named(app, "AppState")
            if states:
                names = _named(states[0].children or [], "installdir")
                if names and names[0].value:
                    candidate = names[0].value.value
                    if not any(c in candidate for c in "/\\:") and candidate not in (".", ".."):
                        install_name = candidate
        except (OSError, UnicodeError, LaunchError):
            pass
        try:
            return validate_game(library / "steamapps" / "common" / install_name).root
        except LaunchError:
            continue
    return None


def running_processes() -> set[str]:
    return game_processes.running_processes()


def _check_runtime() -> None:
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise LaunchError("Launching Deadlock requires Windows and 64-bit Python 3.10 or newer.")


def _listener_table_rows(data: bytes, family: int) -> list[tuple[int, int]]:
    """Decode IP Helper listener rows as (localhost-capable port, owner PID)."""
    if len(data) < 4:
        raise LaunchError("Windows returned an incomplete TCP listener table.")
    count = struct.unpack_from("<I", data)[0]
    row_size = 24 if family == 2 else 56
    if count > (len(data) - 4) // row_size:
        raise LaunchError("Windows returned a truncated TCP listener table.")
    listeners = []
    for index in range(count):
        start = 4 + index * row_size
        if family == 2:
            address = data[start + 4:start + 8]
            port = struct.unpack_from("<I", data, start + 8)[0]
            pid = struct.unpack_from("<I", data, start + 20)[0]
            local = address in (b"\0\0\0\0", b"\x7f\0\0\x01")
        else:
            address = data[start:start + 16]
            port = struct.unpack_from("<I", data, start + 20)[0]
            pid = struct.unpack_from("<I", data, start + 52)[0]
            local = address == b"\0" * 16  # An IPv6 wildcard may accept IPv4 too.
        if local:
            listeners.append((socket.ntohs(port & 0xFFFF), pid))
    return listeners


def _tcp_listeners() -> list[tuple[int, int]]:
    _check_runtime()
    from ctypes import wintypes
    iphelper = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphelper.GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    listeners: list[tuple[int, int]] = []
    for family in (2, 23):  # Windows AF_INET and AF_INET6.
        size = wintypes.DWORD()
        result = get_table(None, ctypes.byref(size), False, family, 3, 0)
        if result in (50, 87) and family == 23:  # IPv6 not supported on this system.
            continue
        if result not in (0, 122):
            raise LaunchError(f"Could not verify the console listener's Windows process owner (error {result}).")
        for _attempt in range(3):
            data = ctypes.create_string_buffer(max(size.value, 4))
            result = get_table(data, ctypes.byref(size), False, family, 3, 0)
            if result != 122:
                break
        if result != 0:
            raise LaunchError(f"Could not verify the console listener's Windows process owner (error {result}).")
        listeners.extend(_listener_table_rows(data.raw[:size.value], family))
    return listeners


# Verified on game build 6864 without Dolly loaded: a demo written by the
# ``record`` command plays with every snapshot refused, so nothing in the scene
# moves, and a backward seek ends playback because the file has no full packets.
# A SourceTV recording of the same local game plays and seeks normally.
PLAYER_RECORDED_DEMO_MESSAGE = (
    "This demo was recorded with the in-game \"record\" command. Deadlock cannot play "
    "that kind of demo back correctly: heroes and models stay frozen and seeking stops "
    "the replay. Record it again with SourceTV instead. In the Deadlock console enter "
    "tv_enable 1, load your map again (for example: map dl_midtown), then tv_record "
    "<name> to start and tv_stoprecord to finish. The new .dem opens in Dolly normally.")


def _validate_demo(path: str | os.PathLike[str] | None) -> Path | None:
    if path is None or not str(path).strip():
        return None
    value = Path(path).expanduser().resolve()
    if value.suffix.casefold() != ".dem" or not value.is_file():
        raise LaunchError("Select an existing, extracted .dem replay file.")
    if any(c in str(value) for c in '\r\n\x00";+'):
        raise LaunchError("The replay path contains console separators. Move it to a folder without quotes, semicolons, or plus signs.")
    from .demo_packets import recorded_by_player
    if recorded_by_player(value):
        raise LaunchError(PLAYER_RECORDED_DEMO_MESSAGE)
    return value


def _validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise LaunchError("Console port must be an integer from 1024 through 65535.")
    return port


def loose_ui_source_overrides(paths: GamePaths, mounts: tuple[tuple[str, str], ...] = ()) -> list[Path]:
    """Uncompiled Panorama files that a development launch loads before the VPK.

    Deadlock ships compiled layouts and scripts inside pak01, but development
    mode gives a same-named ``.xml``/``.js`` source precedence. Stale sources
    left behind by a HUD mod or an earlier build can abort startup before the
    console exists, so the launcher can report the real cause instead of only
    the game's exit code. Addon mounts a mod manager installed are scanned too:
    mounting ``citadel/addons`` exposes any loose Panorama files sitting inside.
    """
    roots = [paths.citadel_dir]
    for key, value in mounts:
        if key.casefold() != "game" or "|" in value:
            continue
        try:
            # SearchPaths are relative to the game directory (game/), so
            # ``citadel/addons`` is game/citadel/addons.
            resolved = (paths.game_dir / value).resolve()
            resolved.relative_to(paths.game_dir.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_dir() and all(resolved != root for root in roots):
            roots.append(resolved)
    found: list[Path] = []
    for root in roots:
        for relative in ("panorama/layout", "panorama/scripts"):
            directory = root / relative
            try:
                if directory.is_dir():
                    found.extend(path for path in directory.rglob("*")
                                 if path.is_file() and path.suffix.casefold() in (".xml", ".js"))
            except OSError:
                continue  # Best-effort diagnostic; the launch check follows anyway.
    return sorted(found)


def _refuse_loose_ui_sources(paths: GamePaths, mounts: tuple[tuple[str, str], ...] = ()) -> None:
    overrides = loose_ui_source_overrides(paths, mounts)
    if not overrides:
        return
    names = "\n".join("  " + path.relative_to(paths.citadel_dir).as_posix() for path in overrides[:8])
    if len(overrides) > 8:
        names += f"\n  ... and {len(overrides) - 8} more"
    raise LaunchError(
        "Deadlock's development mode loads uncompiled Panorama files from the game folder "
        "before the packaged UI, and this install still contains stale ones:\n"
        f"{names}\n"
        "The updated game closes while loading these files. Move or rename the "
        "game\\citadel\\panorama folder (for example to panorama.disabled), or validate "
        "Deadlock's files in Steam, then launch again. No game files were changed.")


def build_command(paths: GamePaths, overlay_dir: Path, port: int, demo_path: Path | None = None, protocol: str = "netcon", launch_options: str = "", *, native: bool = False) -> list[str]:
    """Start the development lobby; replay loading waits for unlocker readiness.

    The selected replay is still validated here, but neither it nor an unlocker
    command is executed on the command line. The controller sends cvar_unhide
    after connecting in the pre-lobby/hideout and only then loads the replay.
    """
    _validate_port(port)
    if overlay_dir.parent.resolve() != paths.game_dir.resolve() or not re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay_dir.name):
        raise LaunchError("The temporary plugin directory must be inside this Deadlock installation.")
    if protocol not in ("vconsole", "netcon"):
        raise LaunchError("Console protocol must be vconsole or netcon.")
    args = [str(paths.executable), "-dev", "-insecure", "-console"]
    args.extend(["-vconsole", "-vconport", str(port)] if protocol == "vconsole" else ["-netconport", str(port)])
    # Preserve citadel game identity; alternate game names can reject real demos.
    args.extend(["-game", str(paths.citadel_dir.resolve())])
    _validate_demo(demo_path)
    from .replays import parse_launch_options
    args.extend(parse_launch_options(launch_options))
    # Settings isolation is reviewed against DX11's read/save consumer in both
    # modes. Native recording additionally needs its Present callbacks. Renderer
    # launch options are refused by parse_launch_options; ordinary settings are
    # never rewritten to select this process-scoped renderer.
    args.append("-dx11")
    return args


def _verified_unlocker() -> Path:
    dll = UNLOCKER_ROOT / "bin" / "win64" / "server.dll"
    try:
        data = dll.read_bytes()
    except OSError as exc:
        raise LaunchError("The bundled cvar unlocker is missing. Extract the complete Dolly ZIP again.") from exc
    if hashlib.sha256(data).hexdigest() != UNLOCKER_SHA256:
        raise LaunchError("The bundled cvar unlocker failed its pinned SHA-256 check. Extract a fresh copy of this Dolly release.")
    if data[:2] != b"MZ" or len(data) < 64:
        raise LaunchError("The bundled cvar unlocker is not a Windows DLL.")
    offset = struct.unpack_from("<I", data, 60)[0]
    if data[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664:
        raise LaunchError("The bundled cvar unlocker is not an x64 Windows DLL.")
    return dll


def _verified_native(paths: GamePaths) -> Path:
    """Fail closed on changed game binaries or a missing native release build."""
    try:
        report = scan_game_modules(paths.game_dir, pins=game_pins()).camera_report()
    except CompatibilityError as exc:
        raise LaunchError(str(exc)) from exc
    if report.state == UNSUPPORTED:
        raise LaunchError("Game files were not changed. " + report.describe())
    if report.state != SUPPORTED:
        raise LaunchError(report.describe())
    dll = NATIVE_ROOT / "bin/win64/DollyNative.dll"
    try:
        metadata = json.loads((NATIVE_ROOT / "build_info.json").read_text(encoding="utf-8"))
        data = dll.read_bytes()
    except (OSError, ValueError) as exc:
        raise LaunchError("The native camera build is missing. Extract the complete Windows Dolly release, or choose Console camera mode.") from exc
    if not isinstance(metadata, dict) or type(metadata.get("abi")) is not int or metadata["abi"] != NATIVE_ABI or not isinstance(metadata.get("sha256"), str) or re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"]) is None:
        raise LaunchError("The native camera build manifest is invalid; extract a fresh Windows Dolly release.")
    if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise LaunchError("The native camera DLL failed its SHA-256 check; extract a fresh Windows Dolly release.")
    offset = struct.unpack_from("<I", data, 60)[0] if len(data) >= 64 else len(data)
    if data[:2] != b"MZ" or offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664:
        raise LaunchError("The native camera DLL is not an x64 Windows DLL.")
    return dll


def _verified_confetti_pack() -> Path:
    try:
        data = CONFETTI_PACK.read_bytes()
    except OSError as exc:
        raise LaunchError("The native confetti particle pack is missing. Extract the complete Dolly build again.") from exc
    if hashlib.sha256(data).hexdigest() != CONFETTI_PACK_SHA256:
        raise LaunchError("The native confetti particle pack failed its SHA-256 check.")
    if len(data) < 32 or data[:4] != struct.pack("<I", 0x55AA1234):
        raise LaunchError("The native confetti particle pack is not a Source 2 VPK.")
    return CONFETTI_PACK


def _verified_ui_override_pack() -> Path:
    try:
        data = UI_OVERRIDE_PACK.read_bytes()
    except OSError as exc:
        raise LaunchError("The capture-clean UI override pack is missing. Extract the complete Dolly build again.") from exc
    if hashlib.sha256(data).hexdigest() != UI_OVERRIDE_PACK_SHA256:
        raise LaunchError("The capture-clean UI override pack failed its SHA-256 check.")
    if len(data) < 32 or data[:4] != struct.pack("<I", 0x55AA1234):
        raise LaunchError("The capture-clean UI override pack is not a Source 2 VPK.")
    return UI_OVERRIDE_PACK


def _save_record(session_dir: Path, record: dict[str, Any]) -> None:
    gameinfo_transaction.save_record(session_dir, record, atomic_write=_atomic_write)


def _load_record(session_dir: Path) -> dict[str, Any]:
    return gameinfo_transaction.load_record(session_dir, validate_game=validate_game)


def _restore_record(session_dir: Path) -> bool:
    return gameinfo_transaction.restore_record(
        session_dir, load_record=_load_record, save_record=_save_record,
        atomic_write=_atomic_write)


def resolve_gameinfo_conflict(session_dir: str | os.PathLike[str], *, keep_current: bool) -> list[str]:
    """Apply the user's conflict choice, then finish the remaining recovery."""
    _check_runtime()
    if _game_is_running(running_processes()):
        raise LaunchError("Exit Deadlock before resolving the game configuration.")
    gameinfo_transaction.resolve_conflict(
        Path(session_dir), keep_current=keep_current, load_record=_load_record,
        save_record=_save_record, atomic_write=_atomic_write)
    return recover_pending()


def recover_at_startup() -> list[str]:
    """Finish an interrupted session's recovery when Dolly opens.

    A crash before the unlocker initialized leaves Dolly's gameinfo.gi in
    place; without this, a normal Steam start would still load it. Skipped
    while any Deadlock runs: that game may be Dolly's own, still owned by its
    cleanup helper, and recovery never touches a running game's files.
    """
    if _game_is_running(running_processes()):
        return []
    return recover_pending()


def _restore_session_configs(session_dir: Path) -> None:
    session_recovery.restore_session_configs(
        session_dir, restore_gameinfo=_restore_record,
        game_is_running=lambda: _game_is_running(running_processes()))


def recover_pending(game_path: str | os.PathLike[str] | None = None) -> list[str]:
    """Restore our unfinished transactions only while no Deadlock process runs.

    Conflicting Steam/user changes are never overwritten. Their pending journal
    remains visible so another launch cannot silently bury the recovery issue.
    """
    _check_runtime()
    if _game_is_running(running_processes()):
        raise LaunchError("Exit Deadlock before recovering a previous Dolly game configuration.")
    from .session_cleanup import remove_overlay, recover_orphans
    selected = validate_game(game_path) if game_path is not None else None
    expected = selected.gameinfo if selected is not None else None
    installations = {selected.game_dir: selected} if selected is not None else {}
    recovered: list[str] = []
    for journal in sorted((PACKAGE_ROOT / "logs").glob("*/session.json")):
        try:
            preliminary = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LaunchError(f"Unreadable Dolly session journal: {journal}. Check this file before relaunching: {exc}") from exc
        if not isinstance(preliminary, dict) or preliminary.get("owner") != "Deadlock Dolly" or "patched_sha256" not in preliminary:
            continue
        if expected is not None and Path(preliminary.get("original_gameinfo", "")).resolve() != expected.resolve():
            continue
        from . import graphics_profiles
        pending = preliminary.get("config_state") != "restored" or graphics_profiles.pending(journal.parent)
        if not pending and not Path(preliminary.get("overlay_dir", "")).exists():
            continue
        if not pending and Path(preliminary.get("session_dir", "")).resolve() != journal.parent.resolve():
            # A portable folder may have been moved with its historical logs.
            # Do not rewrite that old journal's identity; discover its marked
            # mount independently and verify the current game search paths.
            paths = validate_game(Path(preliminary["original_gameinfo"]).parent)
            installations[paths.game_dir] = paths
            continue
        record = _load_record(journal.parent)
        paths = validate_game(Path(record["original_gameinfo"]).parent)
        installations[paths.game_dir] = paths
        from .session_recovery import game_config_lock
        with game_config_lock():
            _restore_session_configs(journal.parent)
            overlay = Path(record["overlay_dir"])
            removed = remove_overlay(overlay, paths, journal.parent)
        if pending or removed:
            recovered.append(str(journal.parent))
    if selected is None:
        discovered = discover_game()
        if discovered is not None:
            paths = validate_game(discovered)
            installations[paths.game_dir] = paths
    for paths in installations.values():
        recovered.extend(recover_orphans(paths))
    return list(dict.fromkeys(recovered))


@dataclass
class Session:
    process: subprocess.Popen[Any]
    session_dir: Path
    overlay_dir: Path
    log_path: Path
    command: tuple[str, ...]
    port: int
    protocol: str = "netcon"
    native: NativeBridge | None = field(default=None, repr=False)
    nvidia_dlss: bool = False
    _log_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _restore_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cleanup_handed_off: bool = field(default=False, repr=False)

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def status(self) -> str:
        code = self.process.poll()
        return "running" if code is None else f"exited ({code})"

    def owns_console_port(self) -> bool:
        """True only if this live game's PID owns the chosen local TCP port."""
        if not self.running:
            return False
        owners = {pid for port, pid in _tcp_listeners() if port == self.port}
        return owners == {self.pid}

    def log(self, message: str) -> None:
        with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + str(message) + "\n")

    def read_log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def record_exit(self, code: int) -> None:
        """Keep the exit result in the same durable journal as launch recovery.

        Serialize with gameinfo restoration so an exit notification cannot
        overwrite a newer config_state with an earlier copy of the journal.
        """
        with self._restore_lock:
            record = _load_record(self.session_dir)
            record["exit_code"] = int(code)
            record["exited_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _save_record(self.session_dir, record)

    def restore_gameinfo(self) -> bool:
        """Restore original bytes after the caller confirms unlocker loaded.

        Safe to call during our game session once its plugin has initialized.
        On external modifications, raise with backup location and do not write.
        """
        with self._restore_lock:
            result = _restore_record(self.session_dir)
            self.log("Original gameinfo.gi restored; loaded plugin stays available in this development process.")
            return result

    def close(self) -> bool:
        """Restore config and remove our plugin directory after game exit.

        Never terminate or attach to a game. Returns False while the game runs;
        the UI should call restore_gameinfo explicitly before closing itself.
        """
        if self.running:
            return False
        if self.native is not None:
            self.native.close()
            self.record_native_diagnostics()
        from .session_cleanup import remove_overlay
        # The exit watcher and dead-session recovery can both close. Serialize
        # the whole file cleanup with record_exit so overlay removal cannot
        # race itself and the journal read cannot meet the exit write.
        from .session_recovery import game_config_lock
        with self._restore_lock, game_config_lock():
            _restore_session_configs(self.session_dir)
            self.log("Original session configuration restored after game exit.")
            try:
                record = _load_record(self.session_dir)
                paths = validate_game(Path(record["original_gameinfo"]).parent)
                if remove_overlay(self.overlay_dir, paths, self.session_dir):
                    self.log("Removed the temporary plugin directory after game exit.")
            except (OSError, ValueError, LaunchError) as exc:
                self.log(f"Temporary plugin directory cleanup deferred: {exc}")
        return True

    def record_native_diagnostics(self) -> None:
        from .player_layer import preserve_diagnostics
        preserve_diagnostics(self.overlay_dir / "cvar_unlocker" / "bin" / "win64",
                             self.session_dir)
        if self.native is None:
            return
        try:
            snapshot = self.native.diagnostics()
            if isinstance(snapshot, dict):
                _atomic_write(self.session_dir / "native_diagnostics.json",
                              (json.dumps(snapshot, indent=2) + "\n").encode("utf-8"))
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics must not prevent configuration recovery.

    def start_cleanup_helper(self) -> bool:
        """Start the background helper that cleans up if Dolly is gone at game exit.

        Called as soon as the game starts, so a Dolly that crashes or is killed
        still gets its game files put back when this game exits.
        """
        if self._cleanup_handed_off:
            return True
        from .session_cleanup import start_waiter
        try:
            start_waiter(self)
            self._cleanup_handed_off = True
            message = "Started the background cleanup helper for this game process."
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            message = f"Could not start background cleanup; Dolly will retry when it closes or next opens: {exc}"
        try:
            self.log(message)
        except OSError:
            pass  # A full or locked log must not undo the launch or skip the watcher.
        return self._cleanup_handed_off

    def handoff_cleanup(self) -> bool:
        """Keep game-exit cleanup alive when the desktop editor is closing."""
        if not self.running:
            return self.close()
        self.record_native_diagnostics()
        if self.start_cleanup_helper():
            try:
                self.log("Dolly is closing; the background cleanup helper will remove its temporary plugin files after this game process exits.")
            except OSError:
                pass
            return True
        return False


def launch(game_path: str | os.PathLike[str], demo_path: str | os.PathLike[str] | None = None, port: int = 29090, protocol: str = "netcon", native: bool = False, launch_options: str = "", graphics_profile: str = "", allow_mod_overrides: bool = False, nvidia_dlss: bool = False) -> Session:
    _check_runtime()
    from .replays import parse_launch_options
    parse_launch_options(launch_options)  # Validate before changing game files.
    paths = validate_game(game_path)
    port = _validate_port(port)
    if protocol not in ("vconsole", "netcon"):
        raise LaunchError("Console protocol must be vconsole or netcon.")
    demo = _validate_demo(demo_path)
    processes = running_processes()
    if _game_is_running(processes):
        details = game_processes.running_game_processes()
        raise LaunchError(_already_running_message(details, paths.executable))
    if not isinstance(native, bool):
        raise LaunchError("Native camera selection must be a boolean.")
    if type(allow_mod_overrides) is not bool:
        raise LaunchError("The mixed-mod allowance must be a boolean.")
    if type(nvidia_dlss) is not bool:
        raise LaunchError("NVIDIA DLSS support must be a boolean.")
    native_dll = _verified_native(paths) if native else None
    # Recover any unfinished previous session first, so the original we back up
    # and journal is the user's true file, not a still-mounted Dolly session.
    recover_pending(paths.root)
    # No game is running here, so no older session's log is still open.
    from .game_logging import prune_session_logs
    prune_session_logs(PACKAGE_ROOT / "logs")
    if "steam.exe" not in processes:
        raise LaunchError("Open Steam and sign in before launching Deadlock through Dolly.")
    try:
        original_data = paths.gameinfo.read_bytes()
        original_text = original_data.decode("utf-8")  # Validate before preparing any replacement.
    except (OSError, UnicodeError) as exc:
        raise LaunchError("Could not read the installed UTF-8 gameinfo.gi; no game files were changed.") from exc
    installed_mounts = tuple(mod_search_paths(original_text))
    from . import mod_policy
    carried_mounts, excluded_mounts = mod_policy.split_mounts(
        installed_mounts, paths.game_dir, allow_overrides=allow_mod_overrides)
    carried_pairs = tuple((record["key"], record["path"]) for record in carried_mounts)
    carried_content = sum(record["key"].casefold() in mod_policy.CONTENT_MOUNT_KEYS
                          for record in carried_mounts)
    # Content mods may join the session; render/visual interface overrides do
    # not, because they change what the capture sees or fight Dolly's own UI.
    # The exact original file is still backed up and restored on exit.
    _refuse_loose_ui_sources(paths, carried_pairs)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            # Exclusive bind on Windows prevents claiming another app's listener.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise LaunchError(f"Console port {port} is busy. Close the previous Dolly/game session or choose another port.") from exc
    dll = _verified_unlocker()
    # Keep previous-session recovery above intact. Verify the same-path stock
    # UI inputs before creating a new bridge, session journal or deployment.
    ui_override_pack = _verified_ui_override_pack() if native else None
    if native:
        try:
            verify_stock_ui_assets(paths.citadel_dir)
        except (OSError, ValueError) as exc:
            raise LaunchError(str(exc)) from exc
    process = None
    bridge = None
    ident = time.strftime("%Y%m%d_%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:10]
    overlay = paths.game_dir / ("citadel_dolly_" + ident)
    configuration_provenance: dict = {}
    editing = _editing_gameinfo(paths, provenance=configuration_provenance)
    from . import editing_assets, editing_session
    config_files = editing_assets.load(EDITING_ROOT)
    session_dir = PACKAGE_ROOT / "logs" / ident
    merged_editing = merge_addon_mounts(original_text, editing, carried_pairs)
    patched_data = make_gameinfo(merged_editing, overlay.name, isolated_settings=True).encode("utf-8")
    log_path = session_dir / "launch.log"
    try:
        if native:
            bridge = NativeBridge.create()
        session_dir.mkdir(parents=True, exist_ok=False)
        overlay.mkdir(exist_ok=False)
        (overlay / 'session-files').mkdir(exist_ok=False)
        marker = {"owner": "Deadlock Dolly", "session_dir": str(session_dir), "original_gameinfo": str(paths.gameinfo), "original_sha256": hashlib.sha256(original_data).hexdigest()}
        (overlay / ".dolly-session.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
        target = overlay / "cvar_unlocker" / "bin" / "win64"
        target.mkdir(parents=True)
        editing_assets.deploy(overlay, config_files)
        if native_dll is None:
            shutil.copyfile(dll, target / "server.dll")
        else:
            shutil.copyfile(native_dll, target / "server.dll")
            shutil.copyfile(dll, target / "dolly_cvar_unlocker.dll")
            # This mounted game search path exists before Deadlock starts, so
            # Source 2 resolves the custom particle systems as native assets.
            shutil.copyfile(_verified_confetti_pack(), target.parents[1] / "pak01_dir.vpk")
            # The verified pack also presents the Panorama shader-loading card
            # and hides the development client-status/debug capture overlays.
            shutil.copyfile(ui_override_pack, target.parents[1] / "pak02_dir.vpk")
            (target / "dolly_native.cfg").write_text(
                f"DOLLY_NATIVE_1\n{bridge.token}\n{bridge.editor_pid}\n",
                encoding="ascii", newline="\n")
            # Published build definitions travel beside the deployed DLL. The
            # bridge parses them fail-closed; an empty document leaves the
            # bundled reviewed table active.
            definitions.write_native(target / definitions.NATIVE_NAME)
        command = build_command(paths, overlay, port, demo, protocol, launch_options, native=native)
        # The engine's own log is the only record of console output after its
        # logger starts: DLSS launches have no stdout, and ordinary launches
        # see nothing on stdout past early startup. Keep it in both modes so a
        # game that quits on its own leaves its reason behind.
        from .game_logging import ENGINE_LOG
        command.extend(["-condebug", "-con_logfile", str((session_dir / ENGINE_LOG).resolve())])
        metadata = {**marker, "command": command, "selected_demo": str(demo) if demo is not None else None, "port": port, "protocol": protocol, "dolly_version": DOLLY_VERSION, "overlay_dir": str(overlay), "unlocker_version": _Unlocker.VERSION, "unlocker_sha256": UNLOCKER_SHA256, "validation": "Windows game startup and selected console protocol require a local probe.", "backup_name": "original.gameinfo.gi", "patched_sha256": hashlib.sha256(patched_data).hexdigest(), "original_mode": stat.S_IMODE(paths.gameinfo.stat().st_mode), "config_state": "prepared", "editing_configuration_provenance": configuration_provenance}
        metadata["nvidia_dlss"] = nvidia_dlss
        if nvidia_dlss:
            metadata["game_logging"] = {"stdout": "unavailable", "stderr": "game_stdout.log",
                "engine": "engine_console.log", "console": "console_transcript.log",
                "limitation": "stdout-only output before engine logging starts is unavailable"}
        else:
            metadata["game_logging"] = {"stdout": "game_stdout.log", "stderr": "game_stdout.log",
                "engine": "engine_console.log", "console": "console_transcript.log"}
        metadata["editing_gameinfo_sha256"] = hashlib.sha256(editing.encode("utf-8")).hexdigest()
        metadata["carried_addon_mounts"] = carried_mounts
        metadata["excluded_addon_mounts"] = excluded_mounts
        metadata["allow_mod_overrides"] = allow_mod_overrides
        metadata["configuration_mode"] = "isolated_editing"
        if native:
            metadata["native_camera"] = {"abi": NATIVE_ABI, "game_sha256": game_pins(),
                                         "dll_sha256": hashlib.sha256(native_dll.read_bytes()).hexdigest()}
        # Durably save the original and journal before touching the installed file.
        _atomic_write(session_dir / "original.gameinfo.gi", original_data)
        _save_record(session_dir, metadata)
        log_path.write_text("Deadlock Dolly development launcher\n" + json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        settings = editing_session.prepare(session_dir, paths, graphics_profile=graphics_profile,
                                           editing_root=EDITING_ROOT, launch_options=launch_options)
        # Check again immediately before process creation, after file preparation.
        if _game_is_running(running_processes()):
            raise LaunchError("Deadlock started while Dolly was preparing. Exit it and try again.")
        if hashlib.sha256(paths.gameinfo.read_bytes()).hexdigest() != metadata["original_sha256"]:
            raise LaunchError("Deadlock gameinfo.gi changed while Dolly was preparing. Launch was refused without overwriting the newer file.")
        _atomic_write(paths.gameinfo, patched_data, metadata["original_mode"])
        if hashlib.sha256(paths.gameinfo.read_bytes()).hexdigest() != metadata["patched_sha256"]:
            raise LaunchError("Dolly's gameinfo.gi swap did not read back correctly. Another program may be "
                              "rewriting the file (close mod managers or Steam), or the file is locked; "
                              "the original was backed up in this session's logs.")
        metadata["config_state"] = "mounted"
        _save_record(session_dir, metadata)
        with (session_dir / "game_stdout.log").open("ab") as output:
            with external_program_environment() as environment:
                environment = dict(os.environ if environment is None else environment)
                environment.update(settings["environment"])
                # Dolly owns the one graphics callback for its optional manual
                # ReShade runtime. Never install a second automatic DXGI hook.
                # This environment belongs only to the launched development game.
                if native:
                    from .settings import reshade_config_path
                    environment["RESHADE_DISABLE_GRAPHICS_HOOK"] = "1"
                    environment["RESHADE_BASE_PATH_OVERRIDE"] = str(reshade_config_path().parent)
                    environment["RESHADE_DISABLE_LOADING_CHECK"] = "1"
                options = {} if environment is None else {"env": environment}
                try:
                    if nvidia_dlss:
                        from .game_logging import spawn
                        process = spawn(command, output, cwd=str(paths.game_dir), shell=False, **options)
                    else:
                        process = subprocess.Popen(command, cwd=str(paths.game_dir), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, shell=False, **options)
                except OSError as exc:
                    if getattr(exc, "winerror", None) != 740:
                        raise
                    raise LaunchError(
                        "Windows requires administrator permission to start Deadlock (WinError 740).\n\n"
                        f"In File Explorer, right-click {paths.executable}, open Properties > Compatibility, "
                        "and check 'Run this program as an administrator', including 'Change settings for all users'. "
                        "Turn that option off if it was enabled unnecessarily, then retry Dolly.\n\n"
                        "If administrator access is required on this computer, close Dolly and use "
                        "Run as administrator on Dolly.exe before retrying.\n\n"
                        f"Launch log: {log_path}") from exc
        session = Session(process, session_dir, overlay, log_path, tuple(command), port, protocol, native=bridge, nvidia_dlss=nvidia_dlss)

        def watch() -> None:
            while True:
                try:
                    for action in dismiss_assert_dialogs(process.pid):
                        session.log("Dismissed Deadlock's assertion dialog with '" + action + "'.")
                except Exception:
                    pass  # Dismissal is best-effort and must never end the watch.
                try:
                    code = process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
            try:
                session.record_exit(code)
            except (OSError, LaunchError) as exc:
                try:
                    session.log(f"Could not save process exit result ({code}): {exc}")
                except OSError:
                    pass
            try:
                session.log(f"Deadlock process exited with code {code}.")
            except OSError:
                pass
            try:
                session.close()
            except (OSError, LaunchError) as exc:
                try:
                    session.log(f"Recovery needs attention: {exc}")
                except OSError:
                    pass

        # Start the exit watcher before any attach step. A failure after the
        # game started must still restore the temporary gameinfo when it exits,
        # instead of leaving the process running with Dolly's patched config and
        # no cleanup until the next manual launch.
        threading.Thread(target=watch, name="dolly-game-exit", daemon=True).start()
        session.start_cleanup_helper()
        if bridge is not None:
            bridge.bind_game(process.pid, exited=lambda: process.poll() is not None)
        try:
            session.log(f"Created development process PID {session.pid}; -dev and -insecure are mandatory.")
            session.log("Loaded Dolly's verified stock editing configuration and separate session settings. "
                        + (f"Carried {carried_content} content mod mount(s) and skipped "
                           f"{len(excluded_mounts)} render/visual or unrecognised mount(s). "
                           if installed_mounts else "")
                        + "Normal-play ConVars are excluded; the exact original gameinfo is backed up.")
            session.log("Replay loading deferred until the controller confirms cvar_unhide in the pre-lobby/hideout.")
            with session._restore_lock:
                current_record = _load_record(session_dir)
                current_record["pid"] = session.pid
                _save_record(session_dir, current_record)
        except OSError:
            # A disk failure after process creation must not delete a live mount.
            pass
        return session
    except Exception as exc:
        if bridge is not None:
            bridge.close()
        if process is None:
            if (session_dir / "session.json").is_file():
                try:
                    _restore_session_configs(session_dir)
                except LaunchError as recovery:
                    raise LaunchError(f"{exc}\nConfiguration recovery needs attention: {recovery}") from exc
            if overlay.is_dir() and (overlay / ".dolly-session.json").is_file():
                from .session_cleanup import remove_overlay
                try:
                    remove_overlay(overlay, paths, session_dir)
                except (OSError, LaunchError) as cleanup:
                    raise LaunchError(f"{exc}\nTemporary session files were preserved because cleanup needs attention: {cleanup}") from exc
        if isinstance(exc, LaunchError):
            raise
        if process is not None and process.poll() is None:
            raise LaunchError(
                f"Deadlock started but Dolly could not finish attaching to it: {exc}\n"
                "Close the game normally; the exit watcher restores the original configuration automatically.\n"
                f"Launch log: {log_path}") from exc
        raise LaunchError(f"Could not prepare or start the development game session: {exc}\nExtract Dolly into a writable folder and check {log_path}.") from exc


def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Recover a Deadlock Dolly configuration after an interrupted launch.")
    parser.add_argument("--recover", action="store_true", help="Restore pending original gameinfo backups; Deadlock must be closed.")
    parser.add_argument("--game", default=None, help="Limit recovery to this Deadlock installation.")
    options = parser.parse_args()
    if not options.recover:
        parser.print_help()
        return 0
    try:
        restored = recover_pending(options.game)
        print(f"Recovered or cleaned {len(restored)} Dolly session(s)." if restored else "No pending Dolly recovery or temporary-folder cleanup is needed.")
        for directory in restored:
            print(directory)
        return 0
    except LaunchError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())

