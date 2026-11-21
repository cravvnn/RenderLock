"""Optional Windows capture binding, scoped to the launched game's window.

Polling observes the high bit of GetAsyncKeyState; it never hooks, registers,
consumes or synthesizes game input. ``on_capture`` runs on a worker thread and
must only enqueue work for the editor's main thread, without touching Tk or
waiting for the console. A binding starts disarmed until an up state is seen.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Callable

from .bindings import CaptureBinding, DEFAULT_BINDING

log = logging.getLogger(__name__)
HOTKEY_LABEL = DEFAULT_BINDING.label  # Compatibility with older imports.
_POLL_SECONDS = 0.008
_VK_CONTROL = 0x11
_VK_MENU = 0x12
_VK_SHIFT = 0x10
_VK_LWIN = 0x5B
_VK_RWIN = 0x5C


class _WindowsHotkeyAPI:
    """Small native boundary, replaceable in tests without a Windows desktop."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError(
                "The capture shortcut is available on Windows. "
                "You can still use the Capture here button."
            )
        from ctypes import wintypes

        self._dword = wintypes.DWORD
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user.GetAsyncKeyState.restype = ctypes.c_short
        self._user.GetForegroundWindow.argtypes = []
        self._user.GetForegroundWindow.restype = wintypes.HWND
        self._user.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        self._user.GetWindowThreadProcessId.restype = wintypes.DWORD

    def key_down(self, vk: int) -> bool:
        # Never use the unreliable low "pressed since last query" bit.
        return bool(self._user.GetAsyncKeyState(vk) & 0x8000)

    def foreground_pid(self) -> int | None:
        hwnd = self._user.GetForegroundWindow()
        if not hwnd:
            return None
        pid = self._dword()
        if not self._user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        return int(pid.value) or None


class CaptureHotkey:
    """Observe one binding while enabled; capture only from ``game_pid()``.

    Each fresh main-key down can produce at most one capture, with all required
    Ctrl/Alt/Shift modifiers and no Windows key. Other movement modifiers remain
    usable: a bare Mouse4 binding also works while Ctrl or Shift is held.
    Holding a button while
    enabling or returning from another app does not capture. ``stop`` wakes the
    polling wait immediately. The object can be started again after stopping.
    """

    def __init__(self, on_capture: Callable[[], None],
                 game_pid: Callable[[], int | None],
                 binding: CaptureBinding | None = None) -> None:
        if binding is not None and not isinstance(binding, CaptureBinding):
            raise ValueError("Capture binding must be a CaptureBinding value.")
        self.binding = binding if binding is not None else DEFAULT_BINDING
        self._on_capture = on_capture
        self._game_pid = game_pid
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._api: _WindowsHotkeyAPI | None = None
        self._error: Exception | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and self._ready.is_set() and not self._stop.is_set()
                    and self._error is None)

    @property
    def last_error(self) -> str | None:
        return str(self._error) if self._error is not None else None

    def is_game_focused(self) -> bool:
        """Recheck a queued capture on the UI thread without waiting for input."""
        api = self._api
        if not self.running or api is None:
            return False
        return self._focused_game_pid(api) is not None and not self._stop.is_set()

    def _focused_game_pid(self, api: _WindowsHotkeyAPI) -> int | None:
        """The eligible foreground PID also identifies focus transitions."""
        try:
            pid = self._game_pid()
            if (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
                    and api.foreground_pid() == pid):
                return pid
        except Exception:
            pass
        return None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self.running:
                    return
                raise RuntimeError("The previous capture shortcut worker is still stopping.")
            self._stop.clear()
            self._ready.clear()
            self._error = None
            self._api = None
            self._thread = threading.Thread(
                target=self._run, name="DollyCaptureHotkey", daemon=True,
            )
            try:
                self._thread.start()
            except RuntimeError:
                self._thread = None
                self._stop.set()
                raise
            if not self._ready.wait(2.0):
                self._stop.set()
                raise RuntimeError("Capture shortcut startup timed out. Use Capture here.")
            if self._error is not None:
                self._thread.join(timeout=0.25)
                raise RuntimeError(str(self._error)) from self._error

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            worker = self._thread
            if worker and worker is not threading.current_thread():
                worker.join(timeout=1.0)
                if worker.is_alive():
                    raise RuntimeError("Capture shortcut worker is still stopping. Close Dolly to stop it.")

    def _sample(self, api: _WindowsHotkeyAPI) -> tuple[bool, bool]:
        down = api.key_down(self.binding.vk)
        modifiers = tuple(api.key_down(vk) for vk in (_VK_CONTROL, _VK_MENU, _VK_SHIFT))
        left_win = api.key_down(_VK_LWIN)
        right_win = api.key_down(_VK_RWIN)
        required = (self.binding.ctrl, self.binding.alt, self.binding.shift)
        modifiers_match = all(not needed or held for needed, held in zip(required, modifiers))
        return down, modifiers_match and not left_win and not right_win

    def _run(self) -> None:
        try:
            api = _WindowsHotkeyAPI()
            self._api = api
            # Establish baseline before start returns: held-on-enable is ignored.
            previous_focus = self._focused_game_pid(api)
            was_down, _ = self._sample(api)
            self._ready.set()
            while not self._stop.wait(_POLL_SECONDS):
                focused_pid = self._focused_game_pid(api)
                down, modifiers_match = self._sample(api)
                # Windows may report zero input while another app or desktop has
                # focus. Reset the baseline on focus changes so returning with a
                # held button cannot turn that artificial up state into a press.
                pressed = (focused_pid is not None and focused_pid == previous_focus
                           and down and not was_down)
                previous_focus = focused_pid
                was_down = down
                if not pressed or not modifiers_match or self._stop.is_set():
                    continue
                try:
                    if self._focused_game_pid(api) == focused_pid and not self._stop.is_set():
                        self._on_capture()
                except Exception:
                    # A failed enqueue cannot kill subsequent input handling.
                    log.exception("Capture shortcut callback failed")
        except Exception as exc:
            self._error = exc
            log.exception("Capture shortcut stopped")
        finally:
            self._stop.set()
            self._ready.set()
