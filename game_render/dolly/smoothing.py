"""Optional, finite-delay smoothing of a shot's shared playback time.

This averages the continuous, piecewise-linear phase observed over a real-time
window. Evaluate every camera and lens channel at the returned time: filtering
individual axes would distort the authored path. Constant motion is delayed by
half the window. A held endpoint is reached exactly after one full window.

The filter cannot interpolate frames inside the game renderer or repair a
missed console delivery. It is an explicit experimental timing filter.
"""

from __future__ import annotations

from collections import deque
import math


SMOOTHING_WINDOWS = {"off": 0.0, "light": 0.08, "balanced": 0.16, "strong": 0.28}
MAX_HISTORY_SAMPLES = 4096


def smoothing_window(mode: str) -> float:
    """Resolve a named mode, rejecting invalid saved/UI settings explicitly."""
    if not isinstance(mode, str) or mode not in SMOOTHING_WINDOWS:
        raise ValueError("Motion smoothing must be off, light, balanced, or strong")
    return SMOOTHING_WINDOWS[mode]


def _nonnegative(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite nonnegative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return value


class PhaseSmoother:
    """Causal box-window average with constant padding before initialization.

    Timestamps must come from the same monotonic clock. Input phase must be
    nonnegative; an actual decrease starts a new history, so a replay rewind
    never blends in the previous shot. The controller retains its seek guards.
    Equal timestamps are supported as zero-duration changes, with no immediate
    jump in filtered output. Zero window gives exact pass-through behavior.
    """

    def __init__(self, window_seconds: float, initial_time: float,
                 initial_value: float) -> None:
        self._window = _nonnegative(window_seconds, "Smoothing window")
        self._resets = 0
        self._initialize(initial_time, initial_value)

    def _initialize(self, now: float, value: float) -> None:
        now = _nonnegative(now, "Smoothing timestamp")
        value = _nonnegative(value, "Playback phase")
        self._samples = deque([(now, value)])
        self._last_time = now
        self._last_raw = self._last_filtered = value

    @property
    def window_seconds(self) -> float:
        return self._window

    @property
    def lag(self) -> float:
        """Input minus output, measured in the input phase's units."""
        return self._last_raw - self._last_filtered

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def resets(self) -> int:
        return self._resets

    def reset(self, now: float, value: float) -> None:
        """Start a new shot/seek without retaining phase from the old one."""
        self._initialize(now, value)
        self._resets += 1

    def update(self, now: float, value: float) -> float:
        now = _nonnegative(now, "Smoothing timestamp")
        value = _nonnegative(value, "Playback phase")
        if now < self._last_time:
            raise ValueError("Smoothing timestamps must not move backwards")
        if value < self._last_raw:
            self.reset(now, value)
            return value
        if self._window == 0:
            self._initialize(now, value)
            return value

        # Retain one point on/before the left boundary for interpolation.
        # Subtract timestamps from 'now' instead of constructing now-window:
        # doing so keeps short windows accurate at a large QPC epoch.
        while len(self._samples) > 1 and now - self._samples[1][0] >= self._window:
            self._samples.popleft()
        if now == self._last_time and value == self._last_raw:
            return self._last_filtered
        if (len(self._samples) >= 2 and
                self._samples[-1][0] == self._samples[-2][0] == now):
            # Keep both the old left limit and the latest right limit. Further
            # observations at the same timestamp add no area or memory.
            self._samples[-1] = (now, value)
        else:
            if len(self._samples) >= MAX_HISTORY_SAMPLES:
                raise ValueError("Motion smoothing received too many samples in one window")
            self._samples.append((now, value))

        # Integrate deviations from the latest phase. Small differences remain
        # accurate when the replay tick/time itself is large. Nonnegative,
        # monotonic phase also keeps these differences finite at extreme input.
        terms = []
        oldest_time, oldest_value = self._samples[0]
        padding = max(0.0, self._window - (now - oldest_time))
        if padding:
            terms.append((oldest_value - value) * (padding / self._window))
        previous = self._samples[0]
        for current in list(self._samples)[1:]:
            a_time, a_value = previous
            b_time, b_value = current
            previous = current
            span = b_time - a_time
            if span <= 0:
                continue
            a_age, b_age = now - a_time, now - b_time
            older, newer = min(self._window, a_age), max(0.0, b_age)
            if older <= newer:
                continue
            left_fraction = max(0.0, min(1.0, (a_age - older) / span))
            right_fraction = max(0.0, min(1.0, (a_age - newer) / span))
            left = a_value + (b_value - a_value) * left_fraction
            right = a_value + (b_value - a_value) * right_fraction
            deviation = (left - value) * 0.5 + (right - value) * 0.5
            terms.append(deviation * ((older - newer) / self._window))
        filtered = value + math.fsum(terms)
        # Rounding cannot move a monotone shot backwards or invent future time.
        filtered = min(value, max(self._last_filtered, filtered))
        self._last_time, self._last_raw, self._last_filtered = now, value, filtered
        return filtered
