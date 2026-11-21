"""Read-only process-name enumeration and Deadlock presence checks."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os

from .game_installation import GAME_EXECUTABLE_NAMES
from .launch_errors import LaunchError


@dataclass(frozen=True)
class GameProcess:
    """One live process that could conflict with a Dolly launch."""

    pid: int
    name: str
    path: str | None
    session_id: int | None
    window_title: str | None


def _game_is_running(processes: set[str]) -> bool:
    return any(name.casefold() in GAME_EXECUTABLE_NAMES for name in processes)


def _snapshot() -> list[tuple[int, str]]:
    """Enumerate process IDs and image names without localized tasklist parsing."""
    if os.name != "nt":
        raise LaunchError("Deadlock Dolly's game launcher requires 64-bit Windows Python.")
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        getattr(kernel, name).restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise LaunchError("Could not inspect running processes; launch was refused.")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise LaunchError("Could not inspect running processes; launch was refused.")
        entries = []
        while True:
            entries.append((int(entry.th32ProcessID), entry.szExeFile.casefold()))
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() not in (0, 18):  # ERROR_NO_MORE_FILES
                    raise LaunchError("Process enumeration was incomplete; launch was refused.")
                break
        return entries
    finally:
        kernel.CloseHandle(snapshot)


def _process_path(pid: int) -> str | None:
    """Full image path when Windows permits a limited query, otherwise None."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value or None
    finally:
        kernel.CloseHandle(handle)


def _session_id(pid: int) -> int | None:
    """Windows session that owns the process, when it can be read."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
        return None
    return int(session.value)


def _window_titles() -> dict[int, str]:
    """Visible top-level window titles keyed by process ID; best-effort."""
    from ctypes import wintypes
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return {}
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    titles: dict[int, str] = {}

    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value in titles:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        if user32.GetWindowTextW(hwnd, title, length + 1):
            text = title.value.strip()
            if text:
                titles[int(pid.value)] = text
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(collect)
    try:
        user32.EnumWindows(callback, 0)
    except OSError:
        return {}
    return titles


def running_processes() -> set[str]:
    """Enumerate process image names without localized tasklist parsing."""
    return {name for _pid, name in _snapshot()}


def running_game_processes() -> list[GameProcess]:
    """Describe matching game processes, including path, session and window.

    Best-effort: this is diagnostic context for a refusal, never a second
    reason to block a launch. If the extra queries fail, the caller still gets
    the name-only refusal.
    """
    try:
        entries = [(pid, name) for pid, name in _snapshot()
                   if name.casefold() in GAME_EXECUTABLE_NAMES]
    except (LaunchError, OSError):
        return []
    if not entries:
        return []
    try:
        titles = _window_titles()
        return [
            GameProcess(pid, name, _process_path(pid), _session_id(pid), titles.get(pid))
            for pid, name in entries
        ]
    except (OSError, AttributeError, ValueError):
        # The extra context is diagnostic only; never replace the refusal with
        # a ctypes failure on an older or restricted Windows host.
        return []
