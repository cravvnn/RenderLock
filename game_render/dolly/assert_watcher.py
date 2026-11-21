"""Dismiss Deadlock's modal development assertion dialogs.

Deadlock's ``-dev`` build shows a modal Windows dialog for known engine/content
assertions (for example the leaked ``Gameplay.Pause.Lp`` audio loop when a
replay is paused). The dialog freezes the game, so Dolly watches only the game
process it launched and presses the dialog's own ignore action, exactly as a
user would. The assertion still reaches the game console and session stdout.
"""
from __future__ import annotations

import ctypes
import os
from typing import Iterable

_TITLE = "assertion failed"
# The rich tier0 dialog offers these actions; the compact Windows fallback
# message box offers Continue/Try Again. Prefer a quiet ignore, then the
# single-occurrence ones, and never choose a debugger or exit action.
_ACTIONS = ("Ignore For 24 Hours", "Ignore", "Ignore This File", "Always Ignore",
            "Ignore All Asserts", "Continue", "Try Again")
_BM_CLICK = 0x00F5


def choose_assert_action(buttons: Iterable[str]) -> str | None:
    """Return the dialog's preferred ignore action, or None when unknown."""
    available = {name.strip().casefold(): name.strip()
                 for name in buttons if name and name.strip()}
    for action in _ACTIONS:
        if action.casefold() in available:
            return available[action.casefold()]
    return None


if os.name == "nt":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumChildWindows.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL

    def _window_text(hwnd) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _child_buttons(hwnd) -> list[tuple[int, str]]:
        buttons: list[tuple[int, str]] = []

        @_WNDENUMPROC
        def collect(child, _param):
            if _user32.IsWindowVisible(child):
                text = _window_text(child).strip()
                if text:
                    buttons.append((child, text))
            return True

        _user32.EnumChildWindows(hwnd, collect, 0)
        return buttons


def dismiss_assert_dialogs(pid: int) -> list[str]:
    """Click the ignore action on every assertion dialog owned by ``pid``.

    Only visible top-level windows whose title is the assertion dialog and that
    belong to the exact launched game process are touched, and only when a
    known ignore action is present. Returns the clicked actions for the log.
    """
    if os.name != "nt" or not pid:
        return []
    clicked: list[str] = []

    @_WNDENUMPROC
    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not _user32.IsWindowVisible(hwnd):
            return True
        if _window_text(hwnd).strip().casefold() != _TITLE:
            return True
        buttons = _child_buttons(hwnd)
        action = choose_assert_action(text for _child, text in buttons)
        if action is None:
            return True
        for child, text in buttons:
            if text.casefold() == action.casefold():
                _user32.PostMessageW(child, _BM_CLICK, 0, 0)
                clicked.append(action)
                break
        return True

    _user32.EnumWindows(visit, 0)
    return clicked
