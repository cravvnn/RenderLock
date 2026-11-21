"""Read-only paused-memory watchdog for the owned game process.

The engine's paused-replay memory grew at roughly 75-110 MB/s in the first
user's diagnostics (analysis/crash-user-20260924), on both camera backends and
with no Dolly draw involved, until Windows ran out of commit. This module turns
that growth into an early warning so a shot can be saved and the session
restarted instead of losing the game to an out-of-memory fatal.

It only reads commit counters for the PID Dolly launched and owns. No game
memory is written, scanned, or copied.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time

_QUERY_LIMITED_INFORMATION = 0x1000
_GIB = 1024 ** 3
_DEFAULT_THRESHOLD_BYTES = 2 * _GIB
_DEFAULT_WINDOW_SECONDS = 60.0
_DEFAULT_MIN_SAMPLES = 3
# The UI poll runs several times a second; each sample opens a process handle.
# Growth is judged over a 60s window, so sampling every few seconds is enough
# and removes a per-poll handle open from the idle path.
_DEFAULT_SAMPLE_INTERVAL_SECONDS = 3.0


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def private_bytes(pid) -> int | None:
    """Committed private bytes of the owned process, or None when unreadable."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(_ProcessMemoryCounters),
                                               wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.PagefileUsage)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def available_commit() -> int | None:
    """System-wide available commit (physical + pagefile), or None."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPageFile)
    except (AttributeError, OSError, ValueError):
        return None


class PausedMemoryWatch:
    """Warn once per paused episode when the owned process keeps growing.

    History is dropped whenever playback is not paused, the tick changes, or
    the PID changes, so ordinary loading and moving replays never warn.
    """

    def __init__(self, *, sample=private_bytes, clock=time.monotonic,
                 threshold_bytes: float = _DEFAULT_THRESHOLD_BYTES,
                 window_seconds: float = _DEFAULT_WINDOW_SECONDS,
                 min_samples: int = _DEFAULT_MIN_SAMPLES,
                 sample_interval_seconds: float = _DEFAULT_SAMPLE_INTERVAL_SECONDS) -> None:
        for value, name in ((threshold_bytes, "threshold"), (window_seconds, "window"),
                            (min_samples, "minimum samples"),
                            (sample_interval_seconds, "sample interval")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"Paused-memory {name} must be positive")
        self._sample = sample
        self._clock = clock
        self._threshold = float(threshold_bytes)
        self._window = float(window_seconds)
        self._min_samples = int(min_samples)
        self._sample_interval = float(sample_interval_seconds)
        self._pid: int | None = None
        self._tick = None
        self._samples: list[tuple[float, int]] = []
        self._warned = False
        self._last_sample_at: float | None = None

    def _reset(self, pid: int | None = None, tick=None) -> None:
        self._pid, self._tick = pid, tick
        self._samples.clear()
        self._warned = False
        self._last_sample_at = None

    def observe(self, pid, *, paused: bool, tick=None) -> str | None:
        """Return a one-time warning string for this paused episode, or None."""
        if not paused or not isinstance(pid, int) or pid <= 0:
            self._reset()
            return None
        if self._pid != pid or (tick is not None and self._tick is not None and tick != self._tick):
            self._reset(pid, tick)
        if tick is not None:
            self._tick = tick
        if self._warned:
            return None
        now = self._clock()
        # The paused/replayed-episode gate and the warning depend on this sample;
        # always take the first sample of an episode, then throttle the rest.
        if self._last_sample_at is not None \
                and now - self._last_sample_at < self._sample_interval:
            return None
        value = self._sample(pid)
        if value is None:
            # An unreadable process is a failed observation, not a throttling
            # anchor: retry promptly rather than suppressing the next read.
            return None
        self._last_sample_at = now
        self._samples.append((now, int(value)))
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        if len(self._samples) < self._min_samples:
            return None
        baseline = min(item[1] for item in self._samples)
        growth = self._samples[-1][1] - baseline
        if growth < self._threshold:
            return None
        self._warned = True
        span = max(0.0, self._samples[-1][0] - self._samples[0][0])
        rate = growth / span if span > 0 else 0.0
        current = self._samples[-1][1]
        return (f"Paused replay memory grew {growth / _GIB:.1f} GiB in {span:.0f}s "
                f"({rate / 1e6:.0f} MB/s; now {current / _GIB:.1f} GiB committed). "
                "Save the shot and restart the game session; the engine can run out of memory while paused.")
