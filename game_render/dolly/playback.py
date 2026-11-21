"""A bounded camera clock for integer replay tick observations.

Console queries report whole ticks. ReplayClock keeps a continuous camera phase
between them: a newly acknowledged integer changes the phase target, not the
camera position. Corrections are limited to 20% of playback speed and spread
over time. A small fraction of a tick of headroom absorbs ordinary response
jitter. The estimate can never lead the latest observation by more than one
tick. If observations stop advancing, it reaches that boundary and holds.

This is a small, bounded estimate for console-driven playback. It does not
provide synchronization with the game's renderer or predict arbitrary gaps.
"""

from __future__ import annotations

import math


def _number(value: float, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    if result < 0 or (positive and result == 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{label} must be {qualifier}")
    return result


class ReplayClock:
    """Estimate a fractional tick from acknowledged integer tick samples.

    ``tick_rate`` is replay ticks per game second and ``speed`` is game seconds
    per real second. Supply timestamps from the same monotonic clock to
    :meth:`observe` and :meth:`position`. Zero is accepted as a time origin.

    Repeated observations preserve the phase target. Increasing ticks cannot
    move the estimate backward or snap it forward; it can briefly lag a newly
    acknowledged tick. A decreasing observed tick represents a seek/rewind and
    immediately resets continuity. Larger forward gaps recover at bounded speed
    rather than teleporting; the caller should stop externally initiated seeks.
    """

    MAX_RATE_CORRECTION = 0.2
    PHASE_HEADROOM_TICKS = 0.2

    def __init__(self, tick_rate: float, speed: float) -> None:
        tick_rate = _number(tick_rate, "Tick rate", positive=True)
        speed = _number(speed, "Playback speed", positive=True)
        self._ticks_per_second = tick_rate * speed
        if not math.isfinite(self._ticks_per_second) or self._ticks_per_second == 0:
            raise ValueError("Tick rate times playback speed must be finite and positive")
        if self._ticks_per_second * self.MAX_RATE_CORRECTION == 0:
            raise ValueError("Tick rate times playback speed is too small for a fractional clock")
        self._tick: int | None = None
        self._target_tick = 0.0
        self._target_time = 0.0
        self._last_time = 0.0
        self._last_position = 0.0
        # Four replay-tick periods at slow speed; avoid chasing individual
        # response timestamps when the requested playback speed is higher.
        self._correction_time = max(0.15, 4.0 / self._ticks_per_second)

    @property
    def observed_tick(self) -> int | None:
        return self._tick

    @property
    def lag_ticks(self) -> float:
        """Current lag behind the latest integer acknowledgement, if any."""
        return max(0.0, self._tick - self._last_position) if self._tick is not None else 0.0

    @property
    def phase_error_ticks(self) -> float:
        """Target minus estimated phase at the last clock update."""
        if self._tick is None:
            return 0.0
        # Diagnostics remain finite even after an unusually large timestamp.
        elapsed = max(0.0, self._last_time - self._target_time)
        horizon = min(elapsed, 2.0 / self._ticks_per_second)
        target = min(self._tick + 1.0, self._target_tick + horizon * self._ticks_per_second)
        return target - self._last_position

    def _advance(self, now: float) -> None:
        now = max(now, self._last_time)
        elapsed = now - self._last_time
        if not elapsed:
            return
        upper = self._tick + 1.0
        remaining = max(0.0, upper - self._last_position)
        rate = self._ticks_per_second
        minimum_rate = rate * (1.0 - self.MAX_RATE_CORRECTION)
        # This also avoids overflowing elapsed * rate after a long stall.
        if not remaining or elapsed >= remaining / minimum_rate:
            self._last_position = upper
            self._last_time = now
            return
        target_age = max(0.0, self._last_time - self._target_time)
        # A target cannot require more than the remaining one-tick prediction
        # horizon; bounding it avoids inf/nan for extreme caller timestamps.
        target_age = min(target_age, 2.0 / rate)
        target = min(upper, self._target_tick + target_age * rate)
        error = target - self._last_position
        maximum_correction = rate * self.MAX_RATE_CORRECTION
        threshold = maximum_correction * self._correction_time
        magnitude = abs(error)
        # Exact integration of rate + clamp(phase_error / tau, +/-20% rate).
        # It is continuous across observations and independent of how often
        # position() is called, until the acknowledged upper bound is reached.
        linear_time = min(elapsed, max(0.0, magnitude - threshold) / maximum_correction)
        correction = maximum_correction * linear_time
        remainder = elapsed - linear_time
        if remainder:
            error_after_linear = max(0.0, magnitude - correction)
            correction += error_after_linear * -math.expm1(-remainder / self._correction_time)
        corrected = math.copysign(correction, error)
        self._last_position = min(upper, self._last_position + elapsed * rate + corrected)
        self._last_time = now

    def observe(self, tick: int, received_at: float) -> None:
        """Acknowledge a tick without restarting the camera's fractional phase."""
        if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
            raise ValueError("Replay tick must be a nonnegative integer")
        # A fractional clock must be able to represent its integer input.
        tick_position = _number(tick, "Replay tick")
        received_at = _number(received_at, "Observation timestamp")
        if self._tick is None or tick < self._tick:
            self._last_position = tick_position
            self._last_time = received_at
            self._target_tick = tick_position
            self._target_time = received_at
            self._tick = tick
            return
        self._advance(received_at)
        if self._tick == tick:
            return
        self._tick = tick
        self._target_tick = max(0.0, tick_position - self.PHASE_HEADROOM_TICKS)
        self._target_time = self._last_time

    def position(self, now: float) -> float:
        """Return the estimated tick, at most one above the latest observation.

        A first observation is required. Backward caller timestamps do not
        rewind the camera; only an actual decreasing tick observation does.
        """
        now = _number(now, "Playback timestamp")
        if self._tick is None:
            raise RuntimeError("Observe a replay tick before reading the playback clock")
        self._advance(now)
        return self._last_position
