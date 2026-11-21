"""Cancellable frame pacing without changing the system timer resolution.

Windows 10 version 1803 and later can provide a dedicated high-resolution
waitable timer. Older systems and timer failures fall back to Event.wait.
This improves wake-up granularity; it does not synchronize with game rendering.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
import threading
import time


_MAX_WAIT_SECONDS = 0.020
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


def _windows_error(operation: str) -> OSError:
    code = getattr(ctypes, "get_last_error", lambda: 0)()
    return OSError(code, f"{operation} failed (Windows error {code})")


class _WindowsTimer:
    """One thread's unnamed, non-inherited, one-shot Windows timer."""

    def __init__(self) -> None:
        self._handle = None
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api.CreateWaitableTimerExW.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ]
        self._api.CreateWaitableTimerExW.restype = wintypes.HANDLE
        self._api.SetWaitableTimer.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong), wintypes.LONG,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
        ]
        self._api.SetWaitableTimer.restype = wintypes.BOOL
        self._api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._api.WaitForSingleObject.restype = wintypes.DWORD
        self._api.CloseHandle.argtypes = [wintypes.HANDLE]
        self._api.CloseHandle.restype = wintypes.BOOL
        # CREATE_WAITABLE_TIMER_HIGH_RESOLUTION; TIMER_MODIFY_STATE | SYNCHRONIZE.
        self._handle = self._api.CreateWaitableTimerExW(None, None, 0x2, 0x100002)
        if not self._handle:
            raise _windows_error("CreateWaitableTimerExW")

    def wait(self, seconds: float) -> None:
        if self._handle is None:
            raise OSError("The frame timer is closed")
        # Negative values mean relative time; units are 100 ns. Never arm an
        # absolute (zero) time, and never request a periodic timer or APC.
        due = ctypes.c_longlong(-max(1, math.ceil(seconds * 10_000_000)))
        if not self._api.SetWaitableTimer(
            self._handle, ctypes.byref(due), 0, None, None, False,
        ):
            raise _windows_error("SetWaitableTimer")
        # A hard upper bound keeps cancellation responsive even if a timer
        # signal arrives late. The caller checks the monotonic deadline again.
        result = self._api.WaitForSingleObject(self._handle, 20)
        if result not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
            raise _windows_error("WaitForSingleObject")

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None and not self._api.CloseHandle(handle):
            raise _windows_error("CloseHandle")


class FrameWait:
    """Reusable wait owned by a single playback worker.

    ``wait(seconds, stop_event)`` returns whether cancellation was requested.
    Call ``close()`` in the worker's finally block. ``backend`` and ``error``
    describe the active mechanism and any nonfatal native-timer failure.

    Custom Event implementations keep their own wait behavior, which may use
    a virtual clock or other cancellation semantics.
    """

    def __init__(self) -> None:
        self.backend = "event_wait"
        self.error: str | None = None
        self._timer: _WindowsTimer | None = None
        if os.name == "nt":
            try:
                self._timer = _WindowsTimer()
                self.backend = "windows_high_resolution_timer"
            except (OSError, AttributeError) as exc:
                self.error = str(exc)

    def _disable_timer(self, error: Exception | None = None) -> None:
        timer, self._timer = self._timer, None
        self.backend = "event_wait"
        if error is not None:
            self.error = str(error)
        if timer is not None:
            try:
                timer.close()
            except OSError as exc:
                if self.error is None:
                    self.error = str(exc)

    def wait(self, seconds: float, stop_event: threading.Event) -> bool:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError("Frame wait must be a finite nonnegative number")
        try:
            seconds = float(seconds)
        except OverflowError as exc:
            raise ValueError("Frame wait must be a finite nonnegative number") from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Frame wait must be a finite nonnegative number")

        if self._timer is not None and not isinstance(stop_event, threading.Event):
            self._disable_timer()
        if self._timer is None:
            # Keep exactly one call, including zero, for alternate Event clocks.
            return bool(stop_event.wait(seconds))

        deadline = time.perf_counter() + seconds
        while not stop_event.is_set():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return stop_event.is_set()
            try:
                self._timer.wait(min(remaining, _MAX_WAIT_SECONDS))
            except OSError as exc:
                self._disable_timer(exc)
                return bool(stop_event.wait(max(0.0, deadline - time.perf_counter())))
        return True

    def close(self) -> None:
        self._disable_timer()
