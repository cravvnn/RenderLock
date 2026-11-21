"""Paths and external-process handling for source and portable EXE launches."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import logging
import os
from pathlib import Path
import sys
import threading

LOG = logging.getLogger("dolly")
_DLL_DIRECTORY_LOCK = threading.Lock()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def wine_version() -> str:
    """The running Wine/Proton version, or "" on real Windows.

    Wine answers most Windows APIs Dolly uses, but not all of them with the
    same results. The preload verification needs another process's module list
    and memory, which Wine does not expose, so callers use this to offer the
    documented manual path instead of failing.
    """
    if os.name != "nt":
        return ""
    try:
        function = ctypes.WinDLL("ntdll").wine_get_version
    except (OSError, AttributeError):
        return ""
    function.restype = ctypes.c_char_p
    try:
        return (function() or b"").decode("ascii", "replace")
    except OSError:
        return ""


def application_root(source_root: Path | None = None) -> Path:
    """Persistent portable data stays beside Dolly.exe, never inside _internal."""
    if is_frozen():
        executable = Path(sys.executable).resolve()
        folder = executable.parent
        if executable.name.lower() == "dollyapp.exe" and folder.name.lower() == "_internal":
            return folder.parent
        return folder
    return Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]


def resource_root(source_root: Path | None = None) -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)
    return application_root(source_root)


def _clean_external_environment() -> dict[str, str]:
    env = dict(os.environ)
    # Some PyInstaller hooks add bundled native libraries to PATH. Those must
    # not take precedence when launching the separately installed game.
    bundle = resource_root().resolve()
    entries = []
    for entry in env.get("PATH", "").split(";"):
        try:
            bundled = bool(entry) and Path(entry.strip('"')).resolve().is_relative_to(bundle)
        except (OSError, ValueError):
            bundled = False
        if not bundled:
            entries.append(entry)
    if "PATH" in env:
        env["PATH"] = ";".join(entries)
    return env


@contextmanager
def external_program_environment():
    """Keep PyInstaller's DLL search directory out of the launched game.

    Restore the original application setting even if process creation fails.
    Source launches keep their existing Popen arguments and environment.
    """
    if not is_frozen() or sys.platform != "win32":
        yield None
        return
    with _DLL_DIRECTORY_LOCK:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        get = api.GetDllDirectoryW
        get.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        get.restype = ctypes.c_uint
        set_directory = api.SetDllDirectoryW
        set_directory.argtypes = [ctypes.c_wchar_p]
        set_directory.restype = ctypes.c_int
        getattr(ctypes, "set_last_error", lambda value: None)(0)
        length = get(0, None)
        if not length and getattr(ctypes, "get_last_error", lambda: 0)():
            raise OSError("Windows could not read Dolly's DLL search directory.")
        if length > 32768:
            raise OSError("The application's DLL search directory could not be read.")
        previous = None
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            copied = get(len(buffer), buffer)
            if not copied or copied >= len(buffer):
                raise OSError("The application's DLL search directory changed during game preparation.")
            previous = buffer.value
        if not set_directory(None):
            raise OSError("Windows could not prepare the game's DLL search path.")
        try:
            yield _clean_external_environment()
        finally:
            if not set_directory(previous):
                LOG.error("Windows could not restore Dolly's DLL search directory after game launch.")
