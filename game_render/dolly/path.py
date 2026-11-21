"""Validated, deterministic camera paths for the Deadlock Dolly editor.

All timestamps are shot-relative seconds. This module has no game access and
never executes strings from a project. Position uses nonuniform cubic Hermite
interpolation; aspect-ratio zoom and smooth cvar tracks use shape-preserving
cubic Hermite interpolation so values cannot overshoot neighboring keys.
Legacy FOV values remain in saved shots solely for backwards compatibility.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Iterable

from .particles import (PARTICLE_DEFAULT, PARTICLE_IDS, PARTICLE_INTENSITY_DEFAULT,
                        PARTICLE_INTENSITY_MAX, PARTICLE_INTENSITY_MIN)
from .object_model import PlacedObject, validate_objects


FORMAT_NAME = "deadlock-dolly"
FORMAT_VERSION = 2
VECTOR_FORMAT_VERSION = 3
CONFETTI_FORMAT_VERSION = 7
PARTICLE_FORMAT_VERSION = 8
LENS_FORMAT_VERSION = 9
SPLINE_FORMAT_VERSION = 10
OBJECTS_FORMAT_VERSION = 11
TANGENTS_FORMAT_VERSION = 12
RIGID_ATTACH_FORMAT_VERSION = 13
MAX_OBJECTS = 64
CONFETTI_SPAWN_HEIGHT_DEFAULT = 250.0
CONFETTI_SPAWN_HEIGHT_MIN = 100.0
CONFETTI_SPAWN_HEIGHT_MAX = 1500.0
ATTACH_FORMAT_VERSION = 4
ROTATION_FORMAT_VERSION = 5
BLEND_FORMAT_VERSION = 6
MAX_PROJECT_BYTES = 8 * 1024 * 1024
PROJECT_NAME_LIMIT = 256
MAX_KEYS = 100_000
STANDARD_ASPECT = 16.0 / 9.0
# Deliberate editor limits, not a claim about native attach payload bounds.
ATTACH_POINTS = ("eyes", "weapon", "bone")
_BONE_NAME = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_.]*|\$cloth_m[0-9]+p[0-9]+)")
ATTACH_BONE_LIMIT = 64
CURVE_CHANNELS = ("pitch", "yaw", "roll")
POSITION_CHANNELS = ("x", "y", "z")
TANGENT_MODES = ("auto", "flat", "linked", "broken")
# The existing render-view bridge rejects XYZ positions outside this bound.
# Apply it only to newly authored Smooth segments, preserving legacy paths.
AUTHORED_POSITION_LIMIT = 1e8
ATTACH_OFFSET_LIMIT = 10000.0
ATTACH_SMOOTHING_MAX = 5.0
ATTACH_MODEL_LIMIT = 260
# Deliberate editor limits, not a claim about native r_aspectratio bounds.
# Zero is the game's automatic-aspect sentinel and is never a curve value.
ASPECT_MIN = 0.5
ASPECT_MAX = 4.0

# These are names, never commands. Prefixes intentionally limit the generic
# numeric track editor to camera parameters. An allowed name is not a promise
# that the currently installed game exposes that cvar: the adapter checks it.
CAMERA_CVAR_PREFIXES = (
    "r_dof_", "mat_dof_", "dof_", "citadel_camera_", "cam_",
    "r_camera_", "cl_camera_", "fov_", "r_citadel_depthoffield_",
)
CAMERA_CVAR_NAMES = frozenset({
    "r_dof", "r_depth_of_field", "mat_dof_enabled", "fov", "fov_cs_debug", "default_fov",
    "spec_fov", "r_aspectratio", "r_drawviewmodel", "cl_drawhud", "citadel_hud_visible",
    "mat_depth_blur_focal_distance", "mat_depth_blur_strength",
})
# Only verified multi-component camera controls accept numeric arrays.
CVAR_COMPONENTS = {"r_dof_override_ranges": 4}
CvarValue = float | tuple[float, ...]
_CVAR_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\Z", re.ASCII)
_CVAR_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,95}\Z", re.ASCII)


def validate_cvar_name(name: str) -> str:
    """Return an allowed camera cvar identifier or raise ``ValueError``.

    Deliberately rejects whitespace, separators, aliases, mixed case and
    command-like inputs. Numeric values are validated separately. This list
    does not unlock variables or establish whether a cvar exists in the game.
    """
    if not isinstance(name, str) or not _CVAR_IDENTIFIER.fullmatch(name):
        raise ValueError("Camera cvar names must be lowercase identifiers without spaces or punctuation")
    if name not in CAMERA_CVAR_NAMES and not name.startswith(CAMERA_CVAR_PREFIXES):
        raise ValueError(f"Not an allowed camera cvar: {name}")
    return name


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def validate_cvar_value(name: str, value: Any, label: str = "Camera value") -> CvarValue:
    """Validate a scalar or the exact component count for a known vector cvar."""
    validate_cvar_name(name)
    count = CVAR_COMPONENTS.get(name, 1)
    if count == 1:
        return _finite(value, label)
    if not isinstance(value, (tuple, list)) or len(value) != count:
        raise ValueError(f"{name} needs {count} numbers separated by spaces")
    return tuple(_finite(component, f"{label} component {i + 1}")
                 for i, component in enumerate(value))


def parse_cvar_value(name: str, text: str, label: str = "Camera value") -> CvarValue:
    """Parse editor text without accepting console syntax or arbitrary strings."""
    validate_cvar_name(name)
    if not isinstance(text, str):
        raise ValueError(f"{label} must be numeric text")
    parts = text.strip().split()
    count = CVAR_COMPONENTS.get(name, 1)
    if len(parts) != count or any(not _CVAR_NUMBER.fullmatch(part) for part in parts):
        if count > 1:
            raise ValueError(f"{name} needs {count} numbers separated by spaces")
        raise ValueError(f"{label} must be a finite number")
    numbers = tuple(float(part) for part in parts)
    return validate_cvar_value(name, numbers if count > 1 else numbers[0], label)


def format_cvar_value(value: CvarValue) -> str:
    """Format already validated scalar/vector data for display or console use."""
    values = value if isinstance(value, (tuple, list)) else (value,)
    return " ".join(format(_finite(component, "Camera value"), ".9g") for component in values)


def _json_cvar_value(value: CvarValue):
    return list(value) if isinstance(value, (tuple, list)) else value


def _choice(value: Any, options: tuple[str, ...], label: str) -> None:
    if not isinstance(value, str) or value not in options:
        raise ValueError(f"{label} must be one of: {', '.join(options)}")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _members(value: dict[str, Any], allowed: Iterable[str], label: str) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {label} field(s): {', '.join(map(str, sorted(unknown, key=str)))}")


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    if len(value) > MAX_KEYS:
        raise ValueError(f"{label} exceeds the {MAX_KEYS:,} key limit")
    return value


@dataclass
class AttachKey:
    """Live attach target for one camera keyframe (project format version 4).

    ``offset`` is local XYZ plus pitch/yaw/roll applied after the target pose
    resolves, in the target's own frame. ``model`` is the model path captured
    at authoring time and is hashed into the native identity token.
    """

    handle: int = 0
    entity_id: int = 0
    model: str = ""
    point: str = "eyes"
    # Bone name for ``point == "bone"``; empty otherwise. Hashed into the
    # native payload and resolved against the model's bone-name vector.
    bone: str = ""
    offset: tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    smoothing: float = 0.0
    hide_body: bool = True
    clearance_mode: str = "exact"
    # Old projects use component-wise angle offsets. Snap uses a composed
    # target-relative orientation without changing previously authored shots.
    orientation_mode: str = "legacy"
    # Per-camera option: keep bone/weapon yaw but ignore its pitch and roll.
    lock_vertical_aim: bool = False


def _validate_attach(attach: Any, label: str) -> None:
    for name in ("handle", "entity_id"):
        value = getattr(attach, name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"{label}.{name} must be an unsigned 32-bit integer")
    if not isinstance(attach.model, str) or len(attach.model) > ATTACH_MODEL_LIMIT \
            or any(ord(character) < 32 for character in attach.model):
        raise ValueError(f"{label}.model must be readable text no longer than {ATTACH_MODEL_LIMIT} characters")
    if not attach.handle and not attach.model.strip():
        raise ValueError(f"{label} needs a handle or a model identity")
    _choice(attach.point, ATTACH_POINTS, f"{label}.point")
    offset = attach.offset
    if not isinstance(offset, (tuple, list)) or len(offset) != 6:
        raise ValueError(f"{label}.offset needs six numbers (xyz and pitch/yaw/roll)")
    for index, value in enumerate(offset):
        number = _finite(value, f"{label}.offset[{index}]")
        if abs(number) > ATTACH_OFFSET_LIMIT:
            raise ValueError(f"{label}.offset[{index}] is outside the supported range")
    smoothing = _finite(attach.smoothing, f"{label}.smoothing")
    if not 0 <= smoothing <= ATTACH_SMOOTHING_MAX:
        raise ValueError(f"{label}.smoothing must be between 0 and {ATTACH_SMOOTHING_MAX:g} seconds")
    if type(attach.hide_body) is not bool:
        raise ValueError(f"{label}.hide_body must be true or false")
    _choice(attach.clearance_mode, ("exact", "auto"), f"{label}.clearance_mode")
    _choice(attach.orientation_mode, ("legacy", "rigid"), f"{label}.orientation_mode")
    if type(attach.lock_vertical_aim) is not bool:
        raise ValueError(f"{label}.lock_vertical_aim must be true or false")
    if attach.point == "bone":
        name = attach.bone
        if not isinstance(name, str) or not 1 <= len(name) <= ATTACH_BONE_LIMIT \
                or not _BONE_NAME.fullmatch(name):
            raise ValueError(f"{label}.bone must be a bone name no longer than "
                             f"{ATTACH_BONE_LIMIT} characters")
    elif attach.bone:
        raise ValueError(f"{label}.bone is only valid for a bone attach point")


def _attach_dict(attach: AttachKey) -> dict[str, Any]:
    data = {"handle": attach.handle, "entity_id": attach.entity_id, "model": attach.model,
            "point": attach.point, "offset": list(attach.offset),
            "smoothing": attach.smoothing, "hide_body": attach.hide_body,
            "clearance_mode": attach.clearance_mode}
    if attach.bone:
        data["bone"] = attach.bone
    if attach.orientation_mode != "legacy":
        data["orientation_mode"] = attach.orientation_mode
    if attach.lock_vertical_aim:
        data["lock_vertical_aim"] = True
    return data


@dataclass(frozen=True)
class CameraTangent:
    """Optional incoming/outgoing XYZ derivatives, in world units per second.

    Auto follows the shot's existing interpolation. Flat sets both derivatives
    to zero; Linked shares one derivative; Broken stores each side separately.
    Authored values only affect Smooth motion and stay saved in other modes.
    """

    mode: str = "auto"
    incoming: float | None = None
    outgoing: float | None = None


def _validate_camera_tangent(tangent: Any, label: str) -> None:
    if not isinstance(tangent, CameraTangent):
        raise ValueError(f"{label} must be a CameraTangent")
    _choice(tangent.mode, TANGENT_MODES, f"{label}.mode")
    if tangent.mode in ("auto", "flat"):
        if tangent.incoming is not None or tangent.outgoing is not None:
            raise ValueError(f"{label} {tangent.mode} mode cannot contain slopes")
        return
    incoming = _finite(tangent.incoming, f"{label}.incoming")
    outgoing = _finite(tangent.outgoing, f"{label}.outgoing")
    if tangent.mode == "linked" and incoming != outgoing:
        raise ValueError(f"{label} linked slopes must be equal")


def _authored_tangents(key: "Keyframe") -> dict[str, CameraTangent]:
    return {name: tangent for name, tangent in key.tangents.items() if tangent.mode != "auto"}


@dataclass
class Keyframe:
    time: float
    x: float
    y: float
    z: float
    pitch: float
    yaw: float
    roll: float
    fov: float = 90.0
    aspect_ratio: float = STANDARD_ASPECT
    source: str = "free"
    attach: AttachKey | None = None
    # Rotation framing curve overrides: when set, this channel is driven by the
    # authored curve value at this key instead of the camera key's own angle.
    curve_pitch: float | None = None
    curve_yaw: float | None = None
    curve_roll: float | None = None
    source_blend: float = 0.0  # Seconds of eased arrival into this key's source.
    # Captured tan(horizontal FOV / 2) / aspect. Independent of the game's
    # spectator lens, which can change when a replay restarts.
    lens_scale: float | None = None
    tangents: dict[str, CameraTangent] = field(default_factory=dict)


@dataclass
class TrackKey:
    time: float
    value: CvarValue


@dataclass
class CvarTrack:
    name: str
    keys: list[TrackKey] = field(default_factory=list)
    interpolation: str = "linear"
    restore_value: CvarValue | None = None


CAMERA_FIELDS = ("x", "y", "z", "pitch", "yaw", "roll", "fov", "aspect_ratio")
_LEGACY_CAMERA_FIELDS = CAMERA_FIELDS[:-1]


def _keyframe_dict(key: Keyframe) -> dict[str, Any]:
    data: dict[str, Any] = {"time": key.time, **{name: getattr(key, name) for name in CAMERA_FIELDS}}
    if key.lens_scale is not None:
        data["lens_scale"] = key.lens_scale
    if key.source != "free":
        data["source"] = key.source
        data["attach"] = _attach_dict(key.attach)
    if key.source_blend:
        data["source_blend"] = key.source_blend
    for channel in CURVE_CHANNELS:
        override = getattr(key, "curve_" + channel)
        if override is not None:
            data["curve_" + channel] = override
    authored = _authored_tangents(key)
    if authored:
        data["tangents"] = {
            channel: {"mode": tangent.mode, **(
                {"incoming": tangent.incoming, "outgoing": tangent.outgoing}
                if tangent.mode in ("linked", "broken") else {})}
            for channel, tangent in authored.items()
        }
    return data


def channel_value(key: Keyframe, name: str) -> float:
    """Effective authored value: a rotation-curve override replaces the angle."""
    if name == "lens_scale":
        return key.lens_scale if key.lens_scale is not None else .75
    if name in CURVE_CHANNELS:
        override = getattr(key, "curve_" + name)
        if override is not None:
            return override
    return getattr(key, name)


def _validate_times(keys: list[Any], label: str) -> None:
    previous = -math.inf
    for index, key in enumerate(keys):
        timestamp = _finite(key.time, f"{label}[{index}].time")
        if timestamp < 0:
            raise ValueError(f"{label} timestamps must be nonnegative")
        if timestamp <= previous:
            raise ValueError(f"{label} timestamps must be strictly increasing; duplicates are not allowed")
        previous = timestamp


def _unwrap(values: list[float]) -> list[float]:
    if not values:
        return []
    # Reducing first also avoids overflow when a user enters very large but
    # finite angles. Unwrapped mode deliberately skips this operation.
    authored = values
    values = [_wrap(value) for value in authored]
    result = [values[0]]
    for index, value in enumerate(values[1:], start=1):
        delta = (value - result[-1] + 180.0) % 360.0 - 180.0
        # Exactly half a turn follows the sign explicitly chosen by the user.
        if delta == -180.0 and authored[index] > authored[index - 1]:
            delta = 180.0
        result.append(result[-1] + delta)
    return result


def _wrap(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _position_tangents(times: list[float], values: list[float]) -> list[float]:
    slopes = [(values[i + 1] - values[i]) / (times[i + 1] - times[i])
              for i in range(len(times) - 1)]
    if len(times) == 2:
        return [slopes[0], slopes[0]]
    # Central secants weight by the actual time intervals. In particular, a
    # tiny neighboring interval cannot arbitrarily amplify a long-segment
    # tangent, as happens with an unweighted average of interval velocities.
    return [slopes[0]] + [
        (values[i + 1] - values[i - 1]) / (times[i + 1] - times[i - 1])
        for i in range(1, len(times) - 1)
    ] + [slopes[-1]]


def _monotone_tangents(times: list[float], values: list[float]) -> list[float]:
    """Fritsch-Butland/PCHIP derivatives for nonuniform sample times."""
    spacing = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    slopes = [(values[i + 1] - values[i]) / spacing[i] for i in range(len(spacing))]
    if len(times) == 2:
        return [slopes[0], slopes[0]]
    derivatives = [0.0] * len(times)
    for i in range(1, len(times) - 1):
        left, right = slopes[i - 1], slopes[i]
        if left == 0 or right == 0 or (left > 0) != (right > 0):
            continue
        w1 = 2 * spacing[i] + spacing[i - 1]
        w2 = spacing[i] + 2 * spacing[i - 1]
        derivatives[i] = (w1 + w2) / (w1 / left + w2 / right)

    def endpoint(here_h: float, next_h: float, here_s: float, next_s: float) -> float:
        derivative = ((2 * here_h + next_h) * here_s - here_h * next_s) / (here_h + next_h)
        if here_s == 0 or (derivative > 0) != (here_s > 0):
            return 0.0
        if (here_s > 0) != (next_s > 0) and abs(derivative) > abs(3 * here_s):
            return 3 * here_s
        return derivative

    derivatives[0] = endpoint(spacing[0], spacing[1], slopes[0], slopes[1])
    derivatives[-1] = endpoint(spacing[-1], spacing[-2], slopes[-1], slopes[-2])
    return derivatives


def _spline_tangents(times: list[float], values: list[float]) -> list[float]:
    """Natural cubic spline derivatives (C2) for nonuniform sample times."""
    count = len(times)
    if count == 2:
        slope = (values[1] - values[0]) / (times[1] - times[0])
        return [slope, slope]
    spacing = [times[i + 1] - times[i] for i in range(count - 1)]
    slopes = [(values[i + 1] - values[i]) / spacing[i] for i in range(count - 1)]
    # Tridiagonal system for the interior second derivatives, natural ends.
    lower = [0.0] * count
    diagonal = [1.0] * count
    upper = [0.0] * count
    rhs = [0.0] * count
    for i in range(1, count - 1):
        lower[i] = spacing[i - 1]
        diagonal[i] = 2 * (spacing[i - 1] + spacing[i])
        upper[i] = spacing[i]
        rhs[i] = 6 * (slopes[i] - slopes[i - 1])
    for i in range(1, count - 1):
        factor = lower[i] / diagonal[i - 1]
        diagonal[i] -= factor * upper[i - 1]
        rhs[i] -= factor * rhs[i - 1]
    second = [0.0] * count
    for i in range(count - 2, 0, -1):
        second[i] = (rhs[i] - upper[i] * second[i + 1]) / diagonal[i]
    derivatives = [0.0] * count
    derivatives[0] = slopes[0] - spacing[0] * (2 * second[0] + second[1]) / 6
    for i in range(1, count - 1):
        derivatives[i] = slopes[i] - spacing[i] * (2 * second[i] + second[i + 1]) / 6
    derivatives[-1] = slopes[-1] + spacing[-1] * (second[-2] + 2 * second[-1]) / 6
    return derivatives


def _validate_authored_segment(left: float, right: float, span: float,
                               outgoing: float, incoming: float, label: str) -> None:
    """Check the whole Hermite segment against the bridge's position contract."""
    depart, arrive = span * outgoing, span * incoming
    if not math.isfinite(depart) or not math.isfinite(arrive):
        raise ValueError(f"{label} slopes are too large for this camera interval")
    # Scale before forming polynomial coefficients: even finite derivatives
    # can otherwise overflow the derivative quadratic used to locate extrema.
    scale = max(abs(left), abs(right), abs(depart), abs(arrive), 1.0)
    p0, p1, m0, m1 = left / scale, right / scale, depart / scale, arrive / scale
    a, b, c = 2 * p0 - 2 * p1 + m0 + m1, -3 * p0 + 3 * p1 - 2 * m0 - m1, m0
    quadratic, linear = 3 * a, 2 * b
    candidates = [0.0, 1.0]
    if quadratic == 0:
        if linear != 0:
            candidates.append(-c / linear)
    else:
        discriminant = linear * linear - 4 * quadratic * c
        if discriminant >= 0:
            root = math.sqrt(discriminant)
            q = -.5 * (linear + math.copysign(root, linear))
            if q == 0:
                candidates.append(-linear / (2 * quadratic))
            else:
                candidates.extend((q / quadratic, c / q))
    for u in candidates:
        if not 0 <= u <= 1:
            continue
        u2, u3 = u * u, u * u * u
        value = ((2 * u3 - 3 * u2 + 1) * left
                 + (u3 - 2 * u2 + u) * depart
                 + (-2 * u3 + 3 * u2) * right + (u3 - u2) * arrive)
        if not math.isfinite(value) or abs(value) > AUTHORED_POSITION_LIMIT:
            raise ValueError(f"{label} leaves the supported camera position range "
                             f"(±{AUTHORED_POSITION_LIMIT:g} units); reduce the slopes")


def _sample(times: list[float], values: list[float], time: float,
            interpolation: str, monotone: bool = True,
            tangents: tuple[float, ...] | None = None,
            incoming_tangents: tuple[float, ...] | None = None,
            outgoing_tangents: tuple[float, ...] | None = None) -> float:
    if time <= times[0] or len(times) == 1:
        return values[0]
    if time >= times[-1]:
        return values[-1]
    left = bisect_right(times, time) - 1
    if interpolation == "step":
        return values[left]
    span = times[left + 1] - times[left]
    fraction = (time - times[left]) / span
    if interpolation == "linear" or (len(times) == 2 and incoming_tangents is None):
        return (1 - fraction) * values[left] + fraction * values[left + 1]
    if tangents is None:
        try:
            if interpolation == "spline":
                tangents = _spline_tangents(times, values)
            else:
                tangents = (_monotone_tangents if monotone else _position_tangents)(times, values)
        except ArithmeticError:
            # Extremely close timestamps can exceed floating-point derivative
            # precision. A linear segment remains finite and honors both keys.
            return (1 - fraction) * values[left] + fraction * values[left + 1]
        if not all(math.isfinite(value) for value in tangents):
            return (1 - fraction) * values[left] + fraction * values[left + 1]
    u2, u3 = fraction * fraction, fraction * fraction * fraction
    incoming = tangents if incoming_tangents is None else incoming_tangents
    outgoing = tangents if outgoing_tangents is None else outgoing_tangents
    value = ((2 * u3 - 3 * u2 + 1) * values[left]
             + (u3 - 2 * u2 + fraction) * span * outgoing[left]
             + (-2 * u3 + 3 * u2) * values[left + 1]
             + (u3 - u2) * span * incoming[left + 1])
    if not math.isfinite(value):
        return (1 - fraction) * values[left] + fraction * values[left + 1]
    if monotone and interpolation != "spline":
        # Contain PCHIP overshoot at segment boundaries. The C2 spline is
        # intentionally unclamped: clamping it would flatten the curve into a
        # wall wherever it passes a key's value.
        low, high = sorted((values[left], values[left + 1]))
        value = max(low, min(high, value))
    return value


@dataclass(frozen=True)
class CameraChannelSamples:
    """Immutable camera-only evaluator for a validated batch of graph samples.

    Playback's sampler still owns the interpolation arithmetic. Preparing its
    inputs once avoids validating the whole shot, evaluating unrelated cvars,
    and rebuilding the same derivatives at every plotted timestamp.
    """
    times: tuple[float, ...]
    values: tuple[float, ...]
    interpolation: str
    monotone: bool
    tangents: tuple[float, ...] | None = None
    incoming_tangents: tuple[float, ...] | None = None
    outgoing_tangents: tuple[float, ...] | None = None

    def evaluate(self, time: float) -> float:
        return _sample(self.times, self.values, _finite(time, "Evaluation time"),
                       self.interpolation, self.monotone, self.tangents,
                       self.incoming_tangents, self.outgoing_tangents)


# OneDrive and antivirus scanners can hold a just-saved shot open for a moment;
# Windows then refuses the replace with a sharing or access error. Retry that
# case for about a second; every other error fails at once.
_SAVE_LOCK_RETRY_DELAYS = (0.1, 0.2, 0.3, 0.4)


def _replace_retrying_lock(source: str, destination: Path) -> None:
    import os

    for delay in (*_SAVE_LOCK_RETRY_DELAYS, None):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


@dataclass
class Project:
    name: str = "Untitled"
    keyframes: list[Keyframe] = field(default_factory=list)
    tracks: list[CvarTrack] = field(default_factory=list)
    interpolation: str = "smooth"
    rotation_mode: str = "shortest"
    start_tick: int = 0
    tick_rate: float = 64.0
    setup_values: dict[str, CvarValue] = field(default_factory=dict)
    standard_aspect: float = STANDARD_ASPECT
    lens_interpolation: str = "smooth"
    confetti_enabled: bool = False
    confetti_spawn_height: float = CONFETTI_SPAWN_HEIGHT_DEFAULT
    confetti_despawn_on_ground: bool = False
    particles_preset: str = PARTICLE_DEFAULT
    particles_intensity: float = PARTICLE_INTENSITY_DEFAULT
    objects: list[PlacedObject] = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.name, str) or len(self.name) > PROJECT_NAME_LIMIT:
            raise ValueError(f"Project name must be text no longer than {PROJECT_NAME_LIMIT} characters")
        if any("\ud800" <= character <= "\udfff" for character in self.name):
            # Unpaired surrogates cannot be written as UTF-8, so the shot could
            # be opened but never saved again.
            raise ValueError("Project name contains an unpaired surrogate character; "
                             "retype the name without it")
        _choice(self.interpolation, ("linear", "smooth", "spline"), "Camera interpolation")
        _choice(self.rotation_mode, ("shortest", "unwrapped"), "Rotation mode")
        _choice(self.lens_interpolation, ("linear", "smooth", "step"), "Zoom interpolation")
        if not isinstance(self.confetti_enabled, bool):
            raise ValueError("Confetti enabled must be true or false")
        spawn_height = _finite(self.confetti_spawn_height, "Confetti spawn height")
        if not CONFETTI_SPAWN_HEIGHT_MIN <= spawn_height <= CONFETTI_SPAWN_HEIGHT_MAX:
            raise ValueError(
                f"Confetti spawn height must be between {CONFETTI_SPAWN_HEIGHT_MIN:g} "
                f"and {CONFETTI_SPAWN_HEIGHT_MAX:g} units")
        if not isinstance(self.confetti_despawn_on_ground, bool):
            raise ValueError("Confetti despawn on ground must be true or false")
        if self.particles_preset not in PARTICLE_IDS:
            raise ValueError("Choose a particle preset from the library")
        intensity = _finite(self.particles_intensity, "Particle intensity")
        if not PARTICLE_INTENSITY_MIN <= intensity <= PARTICLE_INTENSITY_MAX:
            raise ValueError(
                f"Particle intensity must be between {PARTICLE_INTENSITY_MIN:g} "
                f"and {PARTICLE_INTENSITY_MAX:g}")
        standard_aspect = _finite(self.standard_aspect, "Standard aspect ratio")
        if not ASPECT_MIN <= standard_aspect <= ASPECT_MAX:
            raise ValueError(f"Standard aspect ratio must be between {ASPECT_MIN:g} and {ASPECT_MAX:g}")
        if isinstance(self.start_tick, bool) or not isinstance(self.start_tick, int) or self.start_tick < 0:
            raise ValueError("Start tick must be a nonnegative integer")
        if _finite(self.tick_rate, "Tick rate") <= 0:
            raise ValueError("Tick rate must be positive")
        _array(self.keyframes, "Camera keyframes")
        for i, key in enumerate(self.keyframes):
            if not isinstance(key, Keyframe):
                raise ValueError(f"Camera keyframe {i} must be a Keyframe")
            for name in CAMERA_FIELDS:
                _finite(getattr(key, name), f"Camera keyframe {i}.{name}")
            if not 1.0 <= key.fov <= 179.0:
                raise ValueError(f"Camera keyframe {i}.fov must be between 1 and 179 degrees")
            if key.lens_scale is not None and not .001 <= _finite(key.lens_scale, "Camera lens scale") <= 100:
                raise ValueError("Camera lens scale must be between 0.001 and 100")
            if not ASPECT_MIN <= key.aspect_ratio <= ASPECT_MAX:
                raise ValueError(
                    f"Camera keyframe {i}.aspect_ratio must be between {ASPECT_MIN:g} and {ASPECT_MAX:g}; "
                    "use the shot's standard aspect ratio instead of automatic 0")
            _choice(key.source, ("free", "attach"), f"Camera keyframe {i} source")
            _finite(key.source_blend, f"Camera keyframe {i} source blend")
            if not 0 <= key.source_blend <= 10:
                raise ValueError("Source blend must be between 0 and 10 seconds")
            if key.source == "free":
                if key.attach is not None:
                    raise ValueError(f"Camera keyframe {i} has attach data but a free source")
            else:
                if not isinstance(key.attach, AttachKey):
                    raise ValueError(f"Camera keyframe {i} needs an attach target")
                _validate_attach(key.attach, f"Camera keyframe {i}")
            for channel in CURVE_CHANNELS:
                override = getattr(key, "curve_" + channel)
                if override is not None:
                    _finite(override, f"Camera keyframe {i}.curve_{channel}")
            _object(key.tangents, f"Camera keyframe {i} tangents")
            _members(key.tangents, POSITION_CHANNELS, "camera tangent channel")
            for channel, tangent in key.tangents.items():
                _validate_camera_tangent(tangent, f"Camera keyframe {i}.{channel} tangent")
        _validate_times(self.keyframes, "Camera keyframes")
        if self.interpolation == "smooth" and len(self.keyframes) > 1:
            for channel in POSITION_CHANNELS:
                authored = [key.tangents.get(channel, CameraTangent()).mode != "auto"
                            for key in self.keyframes]
                if not any(authored):
                    continue
                samples = self._camera_channel_samples(channel)
                if samples.incoming_tangents is None:
                    raise ValueError(f"Authored {channel.upper()} slopes cannot be represented at these camera times")
                for index in range(len(self.keyframes) - 1):
                    if authored[index] or authored[index + 1]:
                        _validate_authored_segment(
                            samples.values[index], samples.values[index + 1],
                            samples.times[index + 1] - samples.times[index],
                            samples.outgoing_tangents[index], samples.incoming_tangents[index + 1],
                            f"Authored {channel.upper()} segment {index + 1}",
                        )

        _array(self.tracks, "Cvar tracks")
        seen: set[str] = set()
        for i, track in enumerate(self.tracks):
            if not isinstance(track, CvarTrack):
                raise ValueError(f"Cvar track {i} must be a CvarTrack")
            validate_cvar_name(track.name)
            if track.name in seen:
                raise ValueError(f"Duplicate cvar track: {track.name}")
            seen.add(track.name)
            _choice(track.interpolation, ("linear", "smooth", "step"), f"{track.name} interpolation")
            _array(track.keys, f"{track.name} keys")
            for index, key in enumerate(track.keys):
                if not isinstance(key, TrackKey):
                    raise ValueError(f"{track.name} key {index} must be a TrackKey")
                validate_cvar_value(track.name, key.value, f"{track.name} key {index}.value")
            _validate_times(track.keys, track.name)
            if track.restore_value is not None:
                validate_cvar_value(track.name, track.restore_value, f"{track.name} restore value")

        _object(self.setup_values, "Setup values")
        for name, value in self.setup_values.items():
            validate_cvar_name(name)
            validate_cvar_value(name, value, f"{name} setup value")

        if not isinstance(self.objects, list) or len(self.objects) > MAX_OBJECTS:
            raise ValueError(f"A shot can hold at most {MAX_OBJECTS} placed objects")
        validate_objects(self.objects, "Placed objects")

    @property
    def duration(self) -> float:
        """Last authored timestamp, including independent cvar tracks."""
        ends = [self.keyframes[-1].time] if self.keyframes else [0.0]
        ends.extend(track.keys[-1].time for track in self.tracks if track.keys)
        return float(max(ends))

    def retime(self, tick_rate: float) -> None:
        """Reinterpret every authored time at a corrected replay tick rate.

        Replay-timed captures are authored as ``(replay tick - start tick) /
        rate``. When the loaded replay uses a different rate (its own
        CDemoFileInfo is authoritative), stored times are scaled by
        ``old_rate / new_rate`` and the project adopts the new rate. Camera keys
        and cvar tracks share one shot timeline, so all times and source-arrival
        durations move together. Attach smoothing uses rendered time instead.
        """
        rate = _finite(tick_rate, "Tick rate")
        if rate <= 0:
            raise ValueError("Tick rate must be positive")
        factor = _finite(self.tick_rate, "Tick rate") / rate
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("This shot's current tick rate is invalid; set Ticks/second manually.")
        self.validate()
        keys = []
        for index, key in enumerate(self.keyframes):
            if key.source_blend * factor > 10:
                raise ValueError(
                    f"Retiming to {rate:g} ticks/second would stretch camera {index + 1:02d}'s source blend "
                    f"to {key.source_blend * factor:.1f} s, past the 10 s limit. Shorten that blend first.")
            # Derivatives use seconds, including while authored handles are
            # dormant in Linear or Spline. Preserve their replay-time shape.
            tangents = {
                channel: (CameraTangent(tangent.mode,
                                        _finite(tangent.incoming / factor, "Incoming camera slope"),
                                        _finite(tangent.outgoing / factor, "Outgoing camera slope"))
                          if tangent.mode in ("linked", "broken") else tangent)
                for channel, tangent in key.tangents.items()
            }
            keys.append(replace(key, time=key.time * factor, tangents=tangents,
                                source_blend=key.source_blend * factor))
        tracks = [replace(track, keys=[replace(key, time=key.time * factor) for key in track.keys])
                  for track in self.tracks]
        replace(self, keyframes=keys, tracks=tracks, tick_rate=rate).validate()
        for key, retimed in zip(self.keyframes, keys):
            key.time, key.tangents, key.source_blend = retimed.time, retimed.tangents, retimed.source_blend
        for track, retimed in zip(self.tracks, tracks):
            for key, retimed_key in zip(track.keys, retimed.keys):
                key.time = retimed_key.time
        self.tick_rate = rate

    def camera_channel_samples(self, name: str) -> CameraChannelSamples:
        """Prepare an immutable graph evaluator without changing the project."""
        self.validate()
        if not self.keyframes:
            raise ValueError("Add a camera keyframe before evaluating the shot")
        return self._camera_channel_samples(name)

    def _camera_channel_samples(self, name: str) -> CameraChannelSamples:
        """Prepare a channel after the caller has validated this project."""
        fields = CAMERA_FIELDS + (("lens_scale",) if any(k.lens_scale is not None for k in self.keyframes) else ())
        if name not in fields:
            raise KeyError(name)
        times = tuple(float(key.time) for key in self.keyframes)
        values = tuple(float(channel_value(key, name)) for key in self.keyframes)
        if self.rotation_mode == "shortest" and name in ("yaw", "roll"):
            values = tuple(_unwrap(values))
        interpolation = self.lens_interpolation if name in ("aspect_ratio", "lens_scale") else self.interpolation
        monotone = name not in ("x", "y", "z")
        tangents = None
        authored = (interpolation == "smooth" and name in POSITION_CHANNELS
                    and any(name in _authored_tangents(key) for key in self.keyframes))
        if interpolation not in ("step", "linear") and (len(times) > 2 or (authored and len(times) > 1)):
            try:
                derivatives = (_spline_tangents(times, values) if interpolation == "spline" else
                               (_monotone_tangents if monotone else _position_tangents)(times, values))
            except ArithmeticError:
                derivatives = None
            if derivatives is None or not all(math.isfinite(value) for value in derivatives):
                # Match _sample's finite linear fallback for close timestamps.
                interpolation = "linear"
            else:
                tangents = tuple(derivatives)
        incoming = outgoing = None
        if authored and tangents is not None:
            incoming_values, outgoing_values = list(tangents), list(tangents)
            for index, key in enumerate(self.keyframes):
                tangent = key.tangents.get(name, CameraTangent())
                if tangent.mode == "flat":
                    incoming_values[index] = outgoing_values[index] = 0.0
                elif tangent.mode in ("linked", "broken"):
                    incoming_values[index] = float(tangent.incoming)
                    outgoing_values[index] = float(tangent.outgoing)
            incoming, outgoing = tuple(incoming_values), tuple(outgoing_values)
        return CameraChannelSamples(times, values, interpolation, monotone, tangents, incoming, outgoing)

    def _camera_tangent_key(self, index: int, channel: str) -> Keyframe:
        _choice(channel, POSITION_CHANNELS, "Camera tangent channel")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.keyframes):
            raise ValueError("Choose a camera keyframe for its tangent")
        return self.keyframes[index]

    def camera_tangent(self, index: int, channel: str) -> CameraTangent:
        """Return the saved intent; an absent entry is automatic motion."""
        return self._camera_tangent_key(index, channel).tangents.get(channel, CameraTangent())

    def camera_tangent_slopes(self, index: int, channel: str) -> tuple[float, float]:
        """Return effective incoming/outgoing world-units/second derivatives.

        Dormant authored handles do not change the result in Linear or Spline.
        An unrepresentable automatic derivative reports zero for the editor;
        channel evaluation retains its established finite linear fallback.
        """
        self._camera_tangent_key(index, channel)
        samples = self.camera_channel_samples(channel)
        if samples.incoming_tangents is not None:
            return samples.incoming_tangents[index], samples.outgoing_tangents[index]
        if samples.tangents is not None:
            return samples.tangents[index], samples.tangents[index]
        if len(samples.times) == 1:
            return 0.0, 0.0

        def slope(left: int) -> float:
            value = ((samples.values[left + 1] - samples.values[left])
                     / (samples.times[left + 1] - samples.times[left]))
            return value if math.isfinite(value) else 0.0

        return slope(max(0, index - 1)), slope(min(index, len(samples.times) - 2))

    def set_camera_tangent(self, index: int, channel: str, mode: str, *,
                           incoming: float | None = None, outgoing: float | None = None) -> bool:
        """Set saved intent without switching interpolation; return whether it changed.

        Validation completes before any mutation. Selecting Auto removes the
        optional metadata so an otherwise unedited shot keeps its old format.
        """
        key = self._camera_tangent_key(index, channel)
        tangent = CameraTangent(mode, incoming, outgoing)
        _validate_camera_tangent(tangent, "Camera tangent")
        tangents = dict(key.tangents)
        if mode == "auto":
            if channel not in tangents:
                return False
            del tangents[channel]
        elif self.camera_tangent(index, channel) == tangent:
            return False
        else:
            tangents[channel] = tangent
        keys = list(self.keyframes)
        keys[index] = replace(key, tangents=tangents)
        replace(self, keyframes=keys).validate()
        key.tangents = tangents
        return True

    def evaluate(self, time: float) -> dict[str, Any]:
        """Evaluate without state: forward playback and rewinds are identical.

        Camera and cvar channels hold their endpoint values outside their
        individual ranges. Setup values form the cvar baseline; a nonempty
        animated track overrides that baseline, including before its first key.
        ``restore_value`` is metadata for the runtime and is never animated.
        Pitch is interpolated directly in both modes. Only yaw and roll wrap
        between authored keys in shortest mode; unwrapped mode preserves
        authored full turns. Evaluated yaw/roll remain continuously unwrapped
        so crossing 180 degrees does not introduce a 360-degree command jump.
        Those equivalent angles do not alter authored keys or serialization.
        """
        self.validate()
        time = _finite(time, "Evaluation time")
        if not self.keyframes:
            raise ValueError("Add a camera keyframe before evaluating the shot")
        result: dict[str, Any] = {}
        fields = CAMERA_FIELDS + (("lens_scale",) if any(k.lens_scale is not None for k in self.keyframes) else ())
        for name in fields:
            result[name] = self._camera_channel_samples(name).evaluate(time)
        result["cvars"] = self._evaluate_cvars(time)
        return result

    def _evaluate_cvars(self, time: float) -> dict[str, CvarValue]:
        """Evaluate the cvar channels for an already validated shot and time."""
        cvars = {name: validate_cvar_value(name, value) for name, value in self.setup_values.items()}
        for track in self.tracks:
            if track.keys:
                key_times = [float(key.time) for key in track.keys]
                values = [validate_cvar_value(track.name, key.value) for key in track.keys]
                count = CVAR_COMPONENTS.get(track.name, 1)
                if count == 1:
                    cvars[track.name] = _sample(key_times, values, time, track.interpolation)
                else:
                    cvars[track.name] = tuple(_sample(
                        key_times, [value[i] for value in values], time, track.interpolation,
                    ) for i in range(count))
        return cvars

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        if any(key.source_blend for key in self.keyframes):
            version = BLEND_FORMAT_VERSION
        elif any(getattr(key, "curve_" + channel) is not None
               for key in self.keyframes for channel in CURVE_CHANNELS) or any(
                   key.attach is not None and key.attach.bone for key in self.keyframes):
            version = ROTATION_FORMAT_VERSION
        elif any(key.source == "attach" for key in self.keyframes):
            version = ATTACH_FORMAT_VERSION
        elif (any(name in CVAR_COMPONENTS for name in self.setup_values)
              or any(track.name in CVAR_COMPONENTS for track in self.tracks)):
            version = VECTOR_FORMAT_VERSION
        else:
            version = FORMAT_VERSION
        particles_used = (self.confetti_enabled or
                          self.confetti_spawn_height != CONFETTI_SPAWN_HEIGHT_DEFAULT or
                          self.confetti_despawn_on_ground or
                          self.particles_preset != PARTICLE_DEFAULT or
                          self.particles_intensity != PARTICLE_INTENSITY_DEFAULT)
        if particles_used:
            version = PARTICLE_FORMAT_VERSION
        if any(k.lens_scale is not None for k in self.keyframes):
            version = LENS_FORMAT_VERSION
            particles_used = True  # New-format files include the existing optional-setting schema.
        if self.interpolation == "spline":
            version = SPLINE_FORMAT_VERSION
            particles_used = True  # New-format files include the existing optional-setting schema.
        if self.objects:
            version = OBJECTS_FORMAT_VERSION
            particles_used = True  # New-format files include the existing optional-setting schema.
        if any(_authored_tangents(key) for key in self.keyframes):
            version = TANGENTS_FORMAT_VERSION
            particles_used = True
        if any(key.attach is not None and (key.attach.orientation_mode == "rigid" or
                                           key.attach.lock_vertical_aim)
               for key in self.keyframes):
            version = RIGID_ATTACH_FORMAT_VERSION
            particles_used = True
        return {
            "format": FORMAT_NAME,
            "version": version,
            "name": self.name,
            "interpolation": self.interpolation,
            "rotation_mode": self.rotation_mode,
            "standard_aspect": self.standard_aspect,
            "lens_interpolation": self.lens_interpolation,
            **({"confetti_enabled": self.confetti_enabled,
                "confetti_spawn_height": self.confetti_spawn_height,
                "confetti_despawn_on_ground": self.confetti_despawn_on_ground,
                "particles_preset": self.particles_preset,
                "particles_intensity": self.particles_intensity}
               if particles_used else {}),
            **({"objects": [obj.to_dict() for obj in self.objects]} if self.objects else {}),
            "start_tick": self.start_tick,
            "tick_rate": self.tick_rate,
            "setup_values": {name: _json_cvar_value(value) for name, value in self.setup_values.items()},
            "keyframes": [_keyframe_dict(key) for key in self.keyframes],
            "tracks": [{"name": track.name, "interpolation": track.interpolation,
                        "restore_value": _json_cvar_value(track.restore_value),
                        "keys": [{"time": key.time, "value": _json_cvar_value(key.value)} for key in track.keys]}
                       for track in self.tracks],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Project":
        obj = _object(value, "Project")
        if obj.get("format", FORMAT_NAME) != FORMAT_NAME:
            raise ValueError("This is not a Deadlock Dolly project")
        version = obj.get("version")
        if isinstance(version, bool) or not isinstance(version, int) \
                or version not in (1, FORMAT_VERSION, VECTOR_FORMAT_VERSION, ATTACH_FORMAT_VERSION,
                                   ROTATION_FORMAT_VERSION, BLEND_FORMAT_VERSION,
                                   CONFETTI_FORMAT_VERSION, PARTICLE_FORMAT_VERSION,
                                   LENS_FORMAT_VERSION, SPLINE_FORMAT_VERSION,
                                   OBJECTS_FORMAT_VERSION, TANGENTS_FORMAT_VERSION,
                                   RIGID_ATTACH_FORMAT_VERSION):
            raise ValueError(f"Unsupported project version: {version!r}; expected 1 through "
                             f"{RIGID_ATTACH_FORMAT_VERSION}")
        allowed = ("format", "version", "name", "interpolation", "rotation_mode",
                   "start_tick", "tick_rate", "setup_values", "keyframes", "tracks")
        if version >= FORMAT_VERSION:
            allowed += ("standard_aspect", "lens_interpolation")
            for required in ("standard_aspect", "lens_interpolation"):
                if required not in obj:
                    raise ValueError(f"Project version {version} is missing required field: {required}")
        if version >= CONFETTI_FORMAT_VERSION:
            allowed += ("confetti_enabled", "confetti_spawn_height",
                        "confetti_despawn_on_ground")
            for required in ("confetti_enabled", "confetti_spawn_height",
                             "confetti_despawn_on_ground"):
                if required not in obj:
                    raise ValueError(f"Project version {version} is missing required field: {required}")
        if version >= PARTICLE_FORMAT_VERSION:
            allowed += ("particles_preset", "particles_intensity")
            for required in ("particles_preset", "particles_intensity"):
                if required not in obj:
                    raise ValueError(f"Project version {version} is missing required field: {required}")
        if version >= OBJECTS_FORMAT_VERSION:
            allowed += ("objects",)
        _members(obj, allowed, "project")
        keyframes: list[Keyframe] = []
        for i, raw in enumerate(_array(obj.get("keyframes", []), "Camera keyframes")):
            key = _object(raw, f"Camera keyframe {i}")
            fields = CAMERA_FIELDS if version >= FORMAT_VERSION else _LEGACY_CAMERA_FIELDS
            allowed = ("time", *fields)
            if version >= ATTACH_FORMAT_VERSION:
                allowed += ("source", "attach")
            if version >= ROTATION_FORMAT_VERSION:
                allowed += tuple("curve_" + channel for channel in CURVE_CHANNELS)
            if version >= BLEND_FORMAT_VERSION:
                allowed += ("source_blend",)
            if version >= LENS_FORMAT_VERSION:
                allowed += ("lens_scale",)
            if version >= TANGENTS_FORMAT_VERSION:
                allowed += ("tangents",)
            _members(key, allowed, "camera keyframe")
            # A corrupt v2 shot must never silently lose its authored zoom.
            if version >= FORMAT_VERSION and "aspect_ratio" not in key:
                raise ValueError(f"Camera keyframe {i} is missing required field: aspect_ratio")
            if version == 1 and "fov" not in key:
                raise ValueError(f"Camera keyframe {i} is missing required field: fov")
            source = key.get("source", "free")
            attach = None
            raw_attach = key.get("attach")
            if raw_attach is not None:
                target = _object(raw_attach, f"Camera keyframe {i} attach")
                attach_members = ("handle", "entity_id", "model", "point", "bone", "offset",
                                  "smoothing", "hide_body", "clearance_mode")
                if version >= RIGID_ATTACH_FORMAT_VERSION:
                    attach_members += ("orientation_mode", "lock_vertical_aim")
                _members(target, attach_members, "attach key")
                offset = target.get("offset")
                if isinstance(offset, list):
                    offset = tuple(offset)
                try:
                    attach = AttachKey(**{**target, "offset": offset})
                except TypeError as exc:
                    raise ValueError(f"Camera keyframe {i} attach has invalid fields") from exc
            try:
                # No FOV-to-aspect conversion is valid for the unreliable
                # legacy free-camera FOV controls. Keep the old value as
                # metadata and start migrated zoom keys at standard aspect.
                pose = {name: key[name] for name in fields if name in key}
                if "source_blend" in key:
                    pose["source_blend"] = key["source_blend"]
                if "lens_scale" in key:
                    pose["lens_scale"] = key["lens_scale"]
                if "tangents" in key:
                    raw_tangents = _object(key["tangents"], f"Camera keyframe {i} tangents")
                    _members(raw_tangents, POSITION_CHANNELS, "camera tangent channel")
                    tangents = {}
                    for channel, raw_tangent in raw_tangents.items():
                        tangent_data = _object(raw_tangent, f"Camera keyframe {i}.{channel} tangent")
                        _members(tangent_data, ("mode", "incoming", "outgoing"), "camera tangent")
                        if "mode" not in tangent_data:
                            raise ValueError("Camera tangent is missing required field: mode")
                        tangent = CameraTangent(**tangent_data)
                        _validate_camera_tangent(tangent, f"Camera keyframe {i}.{channel} tangent")
                        if tangent.mode != "auto":
                            tangents[channel] = tangent
                    pose["tangents"] = tangents
                for channel in CURVE_CHANNELS:
                    field_name = "curve_" + channel
                    if field_name in key:
                        pose[field_name] = key[field_name]
                keyframes.append(Keyframe(time=key["time"], source=source, attach=attach, **pose))
            except TypeError as exc:
                raise ValueError(f"Camera keyframe {i} is missing required fields") from exc
        tracks: list[CvarTrack] = []
        for i, raw in enumerate(_array(obj.get("tracks", []), "Cvar tracks")):
            track = _object(raw, f"Cvar track {i}")
            _members(track, ("name", "keys", "interpolation", "restore_value"), "cvar track")
            keys: list[TrackKey] = []
            for j, raw_key in enumerate(_array(track.get("keys", []), f"Cvar track {i} keys")):
                key = _object(raw_key, f"Cvar track {i} key {j}")
                _members(key, ("time", "value"), "cvar key")
                try:
                    keys.append(TrackKey(**key))
                except TypeError as exc:
                    raise ValueError(f"Cvar track {i} key {j} is missing required fields") from exc
            if "name" not in track:
                raise ValueError(f"Cvar track {i} is missing its name")
            tracks.append(CvarTrack(name=track["name"], keys=keys,
                                    interpolation=track.get("interpolation", "linear"),
                                    restore_value=track.get("restore_value")))
        objects: list[PlacedObject] = []
        for i, raw in enumerate(_array(obj.get("objects", []), "Placed objects")):
            try:
                objects.append(PlacedObject.from_dict(raw))
            except ValueError as exc:
                raise ValueError(f"Placed object {i} is invalid: {exc}") from exc
        project = cls(name=obj.get("name", "Untitled"), keyframes=keyframes, tracks=tracks,
                      objects=objects,
                      interpolation=obj.get("interpolation", "smooth"),
                      rotation_mode=obj.get("rotation_mode", "shortest"),
                      start_tick=obj.get("start_tick", 0), tick_rate=obj.get("tick_rate", 64.0),
                      setup_values=obj.get("setup_values", {}),
                      standard_aspect=obj.get("standard_aspect", STANDARD_ASPECT),
                      lens_interpolation=obj.get("lens_interpolation", "smooth"),
                      confetti_enabled=obj.get("confetti_enabled", False),
                      confetti_spawn_height=obj.get("confetti_spawn_height",
                                                    CONFETTI_SPAWN_HEIGHT_DEFAULT),
                      confetti_despawn_on_ground=obj.get("confetti_despawn_on_ground", False),
                      particles_preset=obj.get("particles_preset", PARTICLE_DEFAULT),
                      particles_intensity=obj.get("particles_intensity",
                                                  PARTICLE_INTENSITY_DEFAULT))
        project.validate()
        if version < VECTOR_FORMAT_VERSION and (
                any(name in CVAR_COMPONENTS for name in project.setup_values)
                or any(track.name in CVAR_COMPONENTS for track in project.tracks)):
            raise ValueError("Multi-component camera variables require project version 3")
        return project

    def save(self, path: str | Path) -> None:
        """Write validated JSON atomically beside the destination."""
        import os
        import tempfile

        destination = Path(path)
        encoded = (json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_PROJECT_BYTES:
            raise ValueError("Project exceeds the 8 MiB file limit")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.",
                                             suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_retrying_lock(temporary, destination)
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass  # Retain a private leftover without hiding the save result.

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.load_versioned(path)[0]

    @classmethod
    def load_versioned(cls, path: str | Path) -> tuple["Project", int]:
        """Return a validated shot and its version from one bounded JSON snapshot."""
        def reject_constant(value: str) -> None:
            raise ValueError(f"Non-finite JSON number is not permitted: {value}")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError(f"Duplicate JSON field: {name}")
                result[name] = value
            return result

        with Path(path).open("rb") as handle:
            data = handle.read(MAX_PROJECT_BYTES + 1)
        if len(data) > MAX_PROJECT_BYTES:
            raise ValueError("Project exceeds the 8 MiB file limit")
        try:
            obj = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant,
                             object_pairs_hook=unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"Invalid project JSON: {exc}") from exc
        return cls.from_dict(obj), obj["version"]
