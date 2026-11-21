"""Camera movement measured in wall time, independent of replay time.

This module has no game or input access. It only advances the supplied camera
pose; callers keep the replay paused and decide when to send that pose.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any


DEFAULT_MOVE_SPEED = 240.0
DEFAULT_TURN_SPEED = 60.0
MAX_MOVE_SPEED = 10_000.0
MAX_TURN_SPEED = 720.0
MAX_FRAME_SECONDS = 0.1
BOOST_MULTIPLIER = 4.0
PITCH_LIMIT = 89.9


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


@dataclass(frozen=True)
class CameraMotion:
    """Normalized controls: forward, right, world up, left turn, look down."""

    forward: float = 0.0
    right: float = 0.0
    up: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    boost: bool = False
    stop: bool = False

    def __post_init__(self) -> None:
        for name in ("forward", "right", "up", "yaw", "pitch"):
            value = _finite(getattr(self, name), name)
            if not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between -1 and 1")
            object.__setattr__(self, name, value)
        for name in ("boost", "stop"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")


def move_camera(
    frame: dict[str, Any],
    motion: CameraMotion,
    seconds: float,
    move_speed: float = DEFAULT_MOVE_SPEED,
    turn_speed: float = DEFAULT_TURN_SPEED,
) -> dict[str, Any]:
    """Return an independently owned pose without changing shot/replay time.

    Source 2 uses Z for height and positive pitch looks down. Rotation is
    applied before translation, so movement follows the new viewing direction.
    Yaw remains unwrapped. Flight uses world-up elevation, regardless of roll.
    A stalled caller can move at most 0.1 seconds per update; it never catches
    up with a large teleport. Boost affects translation, not rotation.
    """
    if not isinstance(frame, dict):
        raise ValueError("Camera frame must be an object")
    if not isinstance(motion, CameraMotion):
        raise ValueError("Camera motion must be a CameraMotion")
    pose: dict[str, float] = {}
    for name in ("x", "y", "z", "pitch", "yaw"):
        if name not in frame:
            raise ValueError(f"Camera frame is missing {name}")
        pose[name] = _finite(frame[name], f"Camera {name}")
    elapsed = _finite(seconds, "Elapsed seconds")
    if elapsed < 0:
        raise ValueError("Elapsed seconds must be nonnegative")
    speed = _finite(move_speed, "Move speed")
    turn = _finite(turn_speed, "Turn speed")
    if not 0 < speed <= MAX_MOVE_SPEED:
        raise ValueError(f"Move speed must be greater than 0 and at most {MAX_MOVE_SPEED:g}")
    if not 0 < turn <= MAX_TURN_SPEED:
        raise ValueError(f"Turn speed must be greater than 0 and at most {MAX_TURN_SPEED:g}")

    result = deepcopy(frame)
    if motion.stop or elapsed == 0:
        return result
    elapsed = min(elapsed, MAX_FRAME_SECONDS)
    pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, pose["pitch"] + motion.pitch * turn * elapsed))
    yaw = pose["yaw"] + motion.yaw * turn * elapsed
    # Reduce yaw only for trigonometry; keep the authored continuous angle.
    pitch_rad = math.radians(pitch)
    yaw_rad = math.radians(yaw % 360.0)
    sin_yaw, cos_yaw = math.sin(yaw_rad), math.cos(yaw_rad)
    cos_pitch = math.cos(pitch_rad)
    dx = motion.forward * cos_pitch * cos_yaw + motion.right * sin_yaw
    dy = motion.forward * cos_pitch * sin_yaw - motion.right * cos_yaw
    dz = -motion.forward * math.sin(pitch_rad) + motion.up
    magnitude = math.sqrt(dx * dx + dy * dy + dz * dz)
    if magnitude > 1.0:
        dx, dy, dz = dx / magnitude, dy / magnitude, dz / magnitude
    distance = speed * elapsed * (BOOST_MULTIPLIER if motion.boost else 1.0)
    result.update(
        x=pose["x"] + dx * distance,
        y=pose["y"] + dy * distance,
        z=pose["z"] + dz * distance,
        pitch=pitch,
        yaw=yaw,
    )
    return result
