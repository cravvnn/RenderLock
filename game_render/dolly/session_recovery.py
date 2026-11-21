"""Coordinate independent configuration restorations in their original order."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from pathlib import Path
import sys
from typing import Callable

from . import gameinfo_transaction, game_processes
from .launch_errors import LaunchError

LOCK_NAME = "Local\\DeadlockDollyGameConfig"


@contextmanager
def game_config_lock(timeout_ms: int = 60_000):
    """Serialize every cleaner of Dolly's game files across Dolly processes.

    The detached helper, recovery (startup, launch, File > Recover) and the
    exit watcher can otherwise restore the same session at once, and one of
    them can read the next session's gameinfo.gi as an outside change
    (GameinfoCleaners.tla). A named mutex is released by Windows if its holder
    dies, and the same thread may take it again.
    """
    if sys.platform != "win32":
        yield
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    api.CreateMutexW.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api.WaitForSingleObject.restype = ctypes.c_uint
    api.ReleaseMutex.argtypes = [ctypes.c_void_p]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.CreateMutexW(None, False, LOCK_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = api.WaitForSingleObject(handle, timeout_ms)
        if result not in (0, 0x80):  # Acquired, or abandoned by a holder that exited.
            raise LaunchError("Another Dolly window is still restoring the game configuration. "
                              "Wait a moment and try again.")
        try:
            yield
        finally:
            api.ReleaseMutex(handle)
    finally:
        api.CloseHandle(handle)


def restore_session_configs(
    session_dir: Path, *, restore_gameinfo: Callable[[Path], bool] | None = None,
    game_is_running: Callable[[], bool] | None = None,
) -> None:
    """Attempt both independent restorations, even if either has a conflict."""
    from . import graphics_profiles
    if restore_gameinfo is None:
        restore_gameinfo = gameinfo_transaction.restore_record
    if game_is_running is None:
        game_is_running = lambda: game_processes._game_is_running(game_processes.running_processes())
    errors = []
    conflict = None
    with game_config_lock():
        _restore_each(session_dir, restore_gameinfo, game_is_running, errors)
    conflict = next((item for item in errors if isinstance(item, gameinfo_transaction.GameinfoConflict)), None)
    errors = [str(item) for item in errors]
    if conflict is not None:
        # Keep the type so the desktop can offer the user's two choices.
        raise gameinfo_transaction.GameinfoConflict(
            "\n".join(errors), session_dir=conflict.session_dir, target=conflict.target,
            backup=conflict.backup)
    if errors:
        raise LaunchError("\n".join(errors))


def _restore_each(session_dir, restore_gameinfo, game_is_running, errors) -> None:
    from . import graphics_profiles
    for restore in (restore_gameinfo, graphics_profiles.restore):
        try:
            if restore is graphics_profiles.restore and graphics_profiles.pending(session_dir):
                if game_is_running():
                    raise LaunchError("Exit Deadlock before restoring this session's graphics settings.")
            restore(session_dir)
        except (OSError, LaunchError) as exc:  # GameinfoConflict is a LaunchError.
            errors.append(exc)
