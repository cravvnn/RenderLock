"""Shared timeline math for the camera curve graphs.

The curve workspace draws position, rotation and aspect on one horizontal
timeline; these helpers map times and values to the canvas with the same
sampler as shot playback.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .path import Project


# Position edits replace existing camera coordinates; they are not curve_* overrides.
POSITION_CHANNELS = ("x", "y", "z")
POSITION_LIMIT = 1e8  # Existing native path visualization validity limit.


@dataclass(frozen=True)
class PlotBounds:
    left: float
    top: float
    right: float
    bottom: float
    start: float
    end: float
    low: float
    high: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def point(self, time: float, value: float) -> tuple[float, float]:
        span = self.end - self.start if self.end > self.start else 1.0
        return (self.left + (time - self.start) / span * (self.right - self.left),
                self.bottom - (value - self.low) / (self.high - self.low)
                * (self.bottom - self.top))

    def time_at(self, x: float) -> float:
        width = self.right - self.left if self.right > self.left else 1.0
        return self.start + (x - self.left) / width * (self.end - self.start)

    def value_at(self, y: float) -> float:
        return self.low + (self.bottom - y) / (self.bottom - self.top) * (self.high - self.low)


@dataclass
class TimelineView:
    """Shared horizontal zoom for the framing graphs (video-timeline style).

    ``end <= start`` means the full shot is shown; every other value is an
    explicit window that both graphs render so their time axes stay aligned.
    """

    start: float = 0.0
    end: float = 0.0

    def window(self, duration: float) -> tuple[float, float]:
        full = max(float(duration), 0.001)
        if self.end <= self.start:
            return 0.0, full
        start = min(max(0.0, self.start), full)
        end = min(max(start + 0.001, self.end), full)
        return start, end

    def zoom(self, factor: float, anchor: float, duration: float) -> None:
        full = max(float(duration), 0.001)
        start, end = self.window(full)
        span = end - start
        new_span = min(max(span * factor, full / 500.0), full)
        anchor = min(max(anchor, start), end)
        ratio = (anchor - start) / span if span > 0 else 0.0
        start = min(max(0.0, anchor - ratio * new_span), full - new_span)
        self.start, self.end = start, start + new_span

    def pan(self, delta: float, duration: float) -> None:
        full = max(float(duration), 0.001)
        start, end = self.window(full)
        span = end - start
        start = min(max(0.0, start + delta), full - span)
        self.start, self.end = start, start + span

    def reset(self) -> None:
        self.start = self.end = 0.0


def sample_channel(project: Project, name: str, start: float, end: float,
                   samples: int = 160) -> list[tuple[float, float]]:
    """Sample one evaluated channel, preserving step jumps and authored keys."""
    start, end = float(start), float(end)
    if end <= start:
        end = start + 1.0
    if not project.keyframes:
        value = float(project.standard_aspect) if name == "aspect_ratio" else 0.0
        return [(start, value), (end, value)]
    samples = max(2, min(256, samples))
    interpolation = project.lens_interpolation if name == "aspect_ratio" else project.interpolation
    times = {start + (end - start) * index / (samples - 1) for index in range(samples)}
    for key in project.keyframes:
        key_time = float(key.time)
        if start <= key_time <= end:
            times.add(key_time)
            if interpolation == "step" and key_time > start:
                times.add(math.nextafter(key_time, -math.inf))
    channel = project.camera_channel_samples(name)
    return [(time, channel.evaluate(time)) for time in sorted(times)]


def hit_key(points: list[tuple[float, float]], x: float, y: float, radius: float = 12.0) -> int | None:
    """Find the nearest key within a useful pointer target, including overlapped keys."""
    if not points:
        return None
    distances = [(px - x) ** 2 + (py - y) ** 2 for px, py in points]
    index = min(range(len(points)), key=distances.__getitem__)
    return index if distances[index] <= radius * radius else None


@dataclass(frozen=True)
class PositionBounds(PlotBounds):
    """World-unit mapping that also handles very large finite saved coordinates."""

    def point(self, time: float, value: float) -> tuple[float, float]:
        scale = max(abs(self.low), abs(self.high), 1.0)
        fraction = (value / scale - self.low / scale) / (self.high / scale - self.low / scale)
        span = self.end - self.start if self.end > self.start else 1.0
        return (self.left + (time - self.start) / span * (self.right - self.left),
                self.bottom - fraction * (self.bottom - self.top))

    def value_at(self, y: float) -> float:
        scale = max(abs(self.low), abs(self.high), 1.0)
        fraction = (self.bottom - y) / (self.bottom - self.top)
        return scale * (self.low / scale + fraction * (self.high / scale - self.low / scale))
