"""Remove only Dolly's generated mounts, after the launched game has exited."""
from __future__ import annotations

import ctypes
import fnmatch
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .launch_errors import LaunchError
# Compatibility exports for existing cleanup callers.
from .file_ops import _plain_path, _plain_ancestors
from .keyvalues import _parse, _named


OWNER = "Deadlock Dolly"
PREFIX = "citadel_dolly_"
_CAPTURE_BIN = "cvar_unlocker/bin/win64/"
# The player layer capture writes bounded diagnostic artifacts beside the
# deployed DLL; they are ours and are removed with the rest of the session.
GENERATED_FILES = frozenset({
    ".dolly-session.json", "cvar_unlocker/bin/win64/server.dll",
    "cvar_unlocker/bin/win64/dolly_cvar_unlocker.dll",
    "cvar_unlocker/bin/win64/dolly_native.cfg",
    "cvar_unlocker/bin/win64/dolly_definitions.txt",
    "cvar_unlocker/bin/win64/dolly_hide_report.txt",
    "cvar_unlocker/bin/win64/dolly_minimap_guard.txt",
    *(_CAPTURE_BIN + name for name in (
        "dolly_owner_start.txt", "dolly_owner_start.tmp", "dolly_owner_status.txt",
        "dolly_owner_events.bin", "dolly_owner_color_frame.bin", "dolly_owner_frame_meta.bin",
        "dolly_owner_matte.rgba", "dolly_owner_mattes.bin", "dolly_owner_colors.bin",
        "dolly_owner_stall.txt", "dolly_owner_timeline.txt", "dolly_owner_draws.txt",
        "dolly_owner_producers.txt", "dolly_owner_records.txt",
        "dolly_owner_frame_stats.txt", "dolly_owner_stop.txt",
        "dolly_owner_pacing.txt", "dolly_owner_pacing.txt.tmp",
        "dolly_owner_captures.txt")),
    "cvar_unlocker/pak01_dir.vpk",
    "cvar_unlocker/pak02_dir.vpk",
})
GENERATED_DIRS = frozenset({"cvar_unlocker", "cvar_unlocker/bin", "cvar_unlocker/bin/win64", "session-files"})
# The game writes its own assertion/crash reports into the addon root it was
# given as a search path. Their names are engine-chosen, so the whole directory
# is treated as session output rather than enumerated file by file.
GENERATED_TREES = frozenset({"cvar_unlocker/rpt"})
_STEAM_CACHE = re.compile(r"session-files/cache_[0-9]{1,20}\.soc")


def _holds_user_files(session_files: Path) -> bool:
    """Whether the session write folder holds anything besides the Steam cache."""
    return any(not (entry.is_file() and _STEAM_CACHE.fullmatch("session-files/" + entry.name))
               for entry in session_files.iterdir())


def _owned_location(overlay: Path, paths) -> bool:
    return (overlay.is_absolute() and overlay.parent.resolve() == paths.game_dir.resolve()
            and re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay.name) is not None
            and _plain_ancestors(overlay))


def _marker(overlay: Path, paths, session_dir: Path | None = None) -> dict:
    try:
        if not _owned_location(overlay, paths):
            raise ValueError("unexpected or linked plugin directory")
        marker = overlay / ".dolly-session.json"
        if not _plain_path(marker) or not marker.is_file():
            raise ValueError("linked or missing session marker")
        data = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("owner") != OWNER:
            raise ValueError("unrecognized owner")
        recorded_session = Path(data["session_dir"])
        if (not recorded_session.is_absolute() or recorded_session.parent.name != "logs"
                or recorded_session.name != overlay.name[len(PREFIX):]):
            raise ValueError("unexpected session directory")
        # The same logs/<id> session, also after the Dolly folder was moved.
        if session_dir is not None and (recorded_session.name != session_dir.name
                                        or session_dir.resolve().parent.name != "logs"):
            raise ValueError("session directory mismatch")
        if (not isinstance(data.get("original_sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", data["original_sha256"]) is None
                or Path(data["original_gameinfo"]).resolve() != paths.gameinfo.resolve()):
            raise ValueError("unexpected game configuration")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LaunchError(f"Temporary plugin directory left untouched: {overlay}: {exc}") from exc


def _mount_is_referenced(paths, overlay: Path) -> bool:
    """Read actual SearchPaths, including conditions and wildcard mounts."""
    if not _plain_path(paths.gameinfo):
        raise LaunchError("Linked gameinfo.gi was left untouched during temporary-file cleanup.")
    try:
        text = paths.gameinfo.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise LaunchError("Could not verify game search paths before temporary-file cleanup: "
                          "gameinfo.gi is not UTF-8 text.") from exc
    roots = _named(_parse(text), "GameInfo")
    if len(roots) != 1:
        raise LaunchError("Could not verify game search paths before temporary-file cleanup.")
    filesystems = _named(roots[0].children or [], "FileSystem")
    searches = _named(filesystems[0].children or [], "SearchPaths") if len(filesystems) == 1 else []
    if len(searches) != 1 or searches[0].children is None:
        raise LaunchError("Could not verify game search paths before temporary-file cleanup.")
    name = overlay.name.casefold()
    for entry in searches[0].children:
        if entry.value is None:
            raise LaunchError("Could not verify a nested game search path before cleanup.")
        raw = entry.value.value.replace("\\", "/").casefold()
        if name in raw or any(fnmatch.fnmatchcase(name, part) for part in raw.split("/") if part):
            return True
    return False


def _interrupted_removal(overlay: Path, paths) -> bool:
    """Report an empty, unmounted plugin directory whose marker is already gone.

    The marker is deleted just before the directory itself, so a removal that
    raced another cleanup or found the directory still held open stops here.
    An empty directory holds nothing to protect.
    """
    try:
        return (_owned_location(overlay, paths) and overlay.is_dir() and not any(overlay.iterdir())
                and not _mount_is_referenced(paths, overlay))
    except (OSError, LaunchError):
        return False


def remove_overlay(overlay: Path, paths, session_dir: Path | None = None) -> bool:
    """Caller must first establish game exit. Unknown contents are never deleted."""
    if not overlay.exists() and not overlay.is_symlink():
        return False
    try:
        marker_record = _marker(overlay, paths, session_dir)
    except LaunchError:
        if not overlay.exists():
            return False  # The editor's exit thread may have finished first.
        if not _interrupted_removal(overlay, paths):
            raise
        try:
            overlay.rmdir()
        except FileNotFoundError:
            return False
        return True
    if _mount_is_referenced(paths, overlay):
        raise LaunchError(f"The game still references {overlay.name}; its temporary files were left intact. "
                          "Recover the game configuration before starting Deadlock normally.")
    # Inspect every entry before deleting anything. In particular, rmtree must
    # not follow a replaced cvar_unlocker directory or remove extra user files.
    files: list[Path] = []
    directories: list[Path] = []
    generated_files = set(GENERATED_FILES)
    generated_dirs = set(GENERATED_DIRS)
    generated_trees = set(GENERATED_TREES)
    config_dir = overlay / 'cvar_unlocker' / 'cfg'
    if config_dir.exists() or config_dir.is_symlink():
        from .editing_assets import load
        generated_dirs.add('cvar_unlocker/cfg')
        generated_files.update('cvar_unlocker/cfg/' + name for name in load())
    preserved_artifact: Path | None = None
    preserved_unknown: list[str] = []
    removed_reports: list[str] = []
    pending = [overlay]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except FileNotFoundError:
            continue
        for child in children:
            relative = child.relative_to(overlay).as_posix()
            try:
                plain = _plain_path(child)
            except FileNotFoundError:
                continue
            if not plain:
                raise LaunchError(f"Temporary plugin directory contains a link; left untouched: {child}")
            if any(relative == root or relative.startswith(root + "/") for root in generated_trees):
                # Plain entries below a known report directory are game output
                # from this session; they were never user files. Traversal
                # still happens through the same plain-path check above.
                if child.is_dir():
                    directories.append(child)
                    pending.append(child)
                else:
                    files.append(child)
                    removed_reports.append(relative)
            elif child.is_file() and _STEAM_CACHE.fullmatch(relative):
                # The game's own Steam item cache for this session, rewritten on
                # every start. It is session output, not user data.
                files.append(child)
            elif child.is_dir() and relative == "cvar_unlocker/save" and not any(child.iterdir()):
                directories.append(child)  # Created empty by the game.
            elif ((child.is_dir() and relative == "cvar_unlocker/replays") or
                    (child.is_file() and relative == "cvar_unlocker/panorama_debugger.cfg") or
                    (child.is_dir() and relative == "session-files" and _holds_user_files(child))):
                # Sessions can leave replay/settings data in the temporary mount.
                # It is not ours to delete or traverse. Ownership and actual
                # SearchPaths were verified above. Inspect other entries too.
                preserved_artifact = child
            elif child.is_dir() and relative in generated_dirs:
                directories.append(child)
                pending.append(child)
            elif child.is_file() and relative in generated_files:
                files.append(child)
            elif not child.exists():
                continue
            else:
                # Never delete it, but never hold the next launch hostage
                # either: the mount is unmounted and belongs to a finished
                # session, so Dolly removes its own files, keeps this one, and
                # says so. The ownership marker stays so a later cleanup can
                # still recognise the mount.
                preserved_unknown.append(relative)
    if preserved_artifact is not None:
        logging.getLogger(__name__).warning(
            "Preserved an unmounted Dolly session containing replay/settings data: %s. "
            "No files were deleted; this old session does not block a new launch.", overlay)
        return False
    # Retain the ownership marker until all binaries have been removed so a
    # locked DLL can be retried on the next launch.
    marker = overlay / ".dolly-session.json"
    for child in files:
        if child != marker:
            child.unlink(missing_ok=True)
    for child in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            child.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # A generated parent can still hold a preserved unrecognised entry;
            # that is expected only on the preserve-and-continue path.
            if not preserved_unknown:
                raise
    if preserved_unknown:
        shown = ", ".join(sorted(preserved_unknown)[:5])
        if len(preserved_unknown) > 5:
            shown += f" and {len(preserved_unknown) - 5} more"
        logging.getLogger(__name__).warning(
            "Preserved unrecognised files in an unmounted Dolly session: %s. Dolly removed only its own "
            "files; these were left untouched and do not block a new launch.", shown)
        if session_dir is not None:
            _append_log(session_dir, "Temporary-file cleanup left unrecognised entries untouched: " + shown)
        return False
    marker.unlink(missing_ok=True)
    try:
        overlay.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        # A late file or held-open directory can defeat the final rmdir.
        # Retain ownership evidence so a later preflight can preserve new files.
        if overlay.is_dir() and _plain_ancestors(overlay) and not marker.exists():
            from .file_ops import _atomic_write
            _atomic_write(marker, (json.dumps(marker_record, indent=2) + "\n").encode("utf-8"))
        raise
    if removed_reports and session_dir is not None:
        listed = sorted(removed_reports)
        _append_log(session_dir, "Removed game-written session report files with the temporary plugin directory: "
                    + ", ".join(listed[:10]) + (f" and {len(listed) - 10} more" if len(listed) > 10 else ""))
    return True


def recover_orphans(paths) -> list[str]:
    """Find mounts made by an older/moved copy of Dolly in this installation."""
    from .gameinfo_transaction import load_record as _load_record
    from .session_recovery import restore_session_configs as _restore_session_configs
    recovered = []
    for overlay in sorted(paths.game_dir.glob(PREFIX + "*")):
        try:
            marker = _marker(overlay, paths)
        except LaunchError:
            continue  # A name alone is not proof of ownership.
        session = Path(marker["session_dir"])
        journal = session / "session.json"
        if journal.is_file():
            if not _plain_ancestors(session) or not _plain_path(journal):
                continue
            record = _load_record(session)
            if (Path(record["overlay_dir"]).resolve() != overlay.resolve()
                    or record["original_sha256"] != marker["original_sha256"]):
                continue
            _restore_session_configs(session)
        # Even if its old portable folder was deleted, a marked mount can be
        # removed once the current gameinfo demonstrably does not reference it.
        if remove_overlay(overlay, paths, session):
            recovered.append(str(session))
    return recovered


def _append_log(session_dir: Path, message: str) -> None:
    log = session_dir / "launch.log"
    try:
        # Best effort, and never through a link like the rest of cleanup.
        if not _plain_ancestors(session_dir) or (os.path.lexists(log) and not _plain_path(log)):
            return
        with log.open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + message + "\n")
    except OSError:
        pass


def start_waiter(session) -> None:
    """Hand exact, wait-only process handles to a child that survives Dolly.

    The child receives the launched game's handle and Dolly's own. It cleans
    up only once the game has exited and Dolly is gone (closed, crashed or
    killed); while Dolly is open, Dolly's own exit watcher does the cleanup.
    """
    from .runtime import is_frozen
    if sys.platform != "win32":
        raise OSError("Detached game cleanup is available on Windows only.")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.argtypes = []
    api.GetCurrentProcess.restype = ctypes.c_void_p
    api.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    api.DuplicateHandle.restype = ctypes.c_int
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    duplicate = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    own_process = api.GetCurrentProcess()
    # Popen owns this actual process object; a PID lookup could instead select
    # an unrelated process after Windows reused an exited game's PID.
    if not api.DuplicateHandle(own_process, int(session.process._handle), own_process,
                               ctypes.byref(duplicate), 0x00100000, True, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.DuplicateHandle(own_process, own_process, own_process,
                               ctypes.byref(owner), 0x00100000, True, 0):
        error = ctypes.WinError(ctypes.get_last_error())
        api.CloseHandle(duplicate)
        raise error
    try:
        command = [sys.executable]
        if is_frozen():
            command += ["--cleanup-session"]
        else:
            command += ["-m", "dolly.session_cleanup"]
        command += [str(session.session_dir.resolve()), str(duplicate.value), str(owner.value)]
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [duplicate.value, owner.value]}
        environment = dict(os.environ)
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        with (session.session_dir / "launch.log").open("ab") as output:
            subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]),
                             stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                             close_fds=True, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
                             env=environment)
    finally:
        api.CloseHandle(duplicate)
        api.CloseHandle(owner)


def _already_clean(session_dir: Path) -> bool:
    """Whether the Dolly that launched this session finished its own cleanup."""
    from .gameinfo_transaction import load_record as _load_record
    try:
        record = _load_record(session_dir)
        return record.get("config_state") == "restored" and not Path(record["overlay_dir"]).exists()
    except (OSError, ValueError, KeyError, LaunchError):
        return False  # Mid-write or unreadable: keep waiting for Dolly.


def wait_and_cleanup(session_dir: Path, handle: int, owner: int = 0) -> int:
    """Internal helper entry; no injection, console connection, or GUI startup.

    Waits for the game. With ``owner`` (Dolly's own process handle) it then
    leaves the cleanup to that Dolly while it is alive, and takes over only
    when it is gone.
    """
    from .gameinfo_transaction import load_record as _load_record
    from .session_recovery import restore_session_configs as _restore_session_configs
    from .game_installation import validate_game
    from .game_processes import running_processes, _game_is_running
    try:
        if sys.platform != "win32" or handle <= 0:
            raise LaunchError("Invalid detached cleanup process handle.")
        _load_record(session_dir)  # Validate before opening a log or waiting.
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        api.WaitForSingleObject.restype = ctypes.c_uint
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle.restype = ctypes.c_int
        try:
            result = api.WaitForSingleObject(handle, 0xFFFFFFFF)
            if result == 0 and owner > 0:
                # Two cleaners at once could lose Dolly's saved exit result and
                # its diagnostics, so only one runs: Dolly while it is alive.
                while (waited := api.WaitForSingleObject(owner, 1000)) == 0x102:
                    if _already_clean(session_dir):
                        return 0
                if waited != 0:
                    result = waited
        finally:
            api.CloseHandle(handle)
            if owner > 0:
                api.CloseHandle(owner)
        if result != 0:
            raise LaunchError("Could not wait for the launched game; cleanup will retry at next launch.")
        from .session_recovery import game_config_lock
        with game_config_lock():
            # Re-check inside the lock: another Dolly may have finished this
            # session, or started a new game, while the helper waited.
            if _already_clean(session_dir):
                return 0
            if _game_is_running(running_processes()):
                _append_log(session_dir, "Another Deadlock process is running; temporary-file cleanup deferred until next launch.")
                return 0
            _restore_session_configs(session_dir)
            record = _load_record(session_dir)
            paths = validate_game(Path(record["original_gameinfo"]).parent)
            overlay = Path(record["overlay_dir"])
            # Windows may release loaded DLL files just after the process signal.
            for attempt in range(6):
                try:
                    if remove_overlay(overlay, paths, session_dir):
                        _append_log(session_dir, "Removed the temporary plugin directory after Dolly closed and the game exited.")
                    return 0
                except OSError:
                    if attempt == 5:
                        raise
                    time.sleep(0.5)
        return 0
    except (OSError, ValueError, LaunchError) as exc:
        _append_log(session_dir, f"Temporary-file cleanup deferred: {exc}")
        return 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Wait for a Dolly editing session to exit and remove its temporary files.")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("handle", type=int)
    parser.add_argument("owner", type=int, nargs="?", default=0)
    options = parser.parse_args()
    raise SystemExit(wait_and_cleanup(options.session_dir, options.handle, options.owner))
