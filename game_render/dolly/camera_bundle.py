"""Camera bundle reader, validator and reprojection helpers.

A paired depth take writes ``camera.json`` next to its depth master and EXR
sequence (format ``deadlock-dolly-camera``, version 1). The bundle records, for
every admitted output frame, the per-view camera constants the renderer
actually used for the captured scene sample. See ``docs/CAMERA_BUNDLE.md`` for
the stored conventions; this module loads, validates and reprojects through a
bundle with no third-party dependency.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

FORMAT = "deadlock-dolly-camera"
VERSION = 1
# A generous ceiling: 65,536 records of roughly 1 KiB each is about 64 MiB.
_MAX_FILE_BYTES = 128 * 1024 * 1024
_STRUCTURE_TOLERANCE = 1e-3
_VIEW_TOLERANCE = 1e-3
_AFFINE_TOLERANCE = 0.05
_FLOAT32_EPSILON = 2 ** -23


class CameraBundleError(ValueError):
    """The file is not a readable camera bundle of a supported version."""


@dataclass(frozen=True)
class View:
    """World -> view transform and camera vectors (see the format doc)."""

    matrix: tuple[float, ...]  # 16, row-major
    position: tuple[float, ...]  # 3, world units
    up: tuple[float, ...]  # 3
    forward: tuple[float, ...]  # 3


@dataclass(frozen=True)
class CameraRecord:
    frame: int
    sample: int
    replay_time: float
    capture_pts_100ns: int
    state: str  # "complete" | "projection-only"
    view: View | None
    projection: tuple[float, ...]  # 16, row-major, column-vector application
    inverse_z: tuple[float, float, float, float]
    viewport_min: float
    viewport_max: float
    near: float
    far: float | None  # None means the far plane is at infinity


@dataclass(frozen=True)
class ProjectedPoint:
    """A world point projected through one record."""

    x: float
    y: float
    device_z: float
    distance: float


@dataclass(frozen=True)
class CameraBundle:
    complete: bool
    frames: int
    width: int
    height: int
    color_width: int
    color_height: int
    records: tuple[CameraRecord, ...]
    partial_reason: str | None = None

    def project_point(self, record: CameraRecord, point) -> ProjectedPoint | None:
        """Project a world-space point through one record (None if not projectable)."""
        return project_point(self, record, point)


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CameraBundleError(f"{what} is not a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise CameraBundleError(f"{what} is not finite") from exc
    if not math.isfinite(number):
        raise CameraBundleError(f"{what} is not finite")
    return number


def _integer(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CameraBundleError(f"{what} is not an integer")
    return value


def _vector(value, length: int, what: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise CameraBundleError(f"{what} must be {length} numbers")
    return tuple(_number(item, what) for item in value)


def _required(data: dict, key: str, what: str):
    if key not in data:
        raise CameraBundleError(f"{what} is missing {key}")
    return data[key]


def _record(data, index: int) -> CameraRecord:
    what = f"record {index}"
    if not isinstance(data, dict):
        raise CameraBundleError(f"{what} is not an object")
    frame = _integer(_required(data, "frame", what), f"{what} frame")
    if frame < 0:
        raise CameraBundleError(f"{what} frame is negative")
    sample = _integer(_required(data, "sample", what), f"{what} sample")
    replay_time = _number(_required(data, "replay_time", what), f"{what} replay_time")
    if replay_time < 0:
        raise CameraBundleError(f"{what} replay_time is negative")
    pts = _integer(_required(data, "capture_pts_100ns", what), f"{what} capture_pts_100ns")
    state = data.get("state")
    if state not in ("complete", "projection-only"):
        raise CameraBundleError(f"{what} has an unknown state")
    projection = _vector(_required(data, "projection", what), 16, f"{what} projection")
    inverse_z_raw = _vector(_required(data, "inverse_z", what), 4, f"{what} inverse_z")
    inverse_z = (inverse_z_raw[0], inverse_z_raw[1], inverse_z_raw[2], inverse_z_raw[3])
    viewport = _vector(_required(data, "viewport", what), 2, f"{what} viewport")
    viewport_min, viewport_max = viewport
    if not (0.0 <= viewport_min < viewport_max <= 1.0):
        raise CameraBundleError(f"{what} has an invalid viewport")
    near = _number(_required(data, "near", what), f"{what} near")
    far_raw = _required(data, "far", what)
    far = None if far_raw is None else _number(far_raw, f"{what} far")
    if near < 0 or (far is not None and far <= near):
        raise CameraBundleError(f"{what} has an invalid clip range")
    view = None
    if state == "complete":
        view = View(
            matrix=_vector(_required(data, "view", what), 16, f"{what} view"),
            position=_vector(_required(data, "position", what), 3, f"{what} position"),
            up=_vector(_required(data, "up", what), 3, f"{what} up"),
            forward=_vector(_required(data, "forward", what), 3, f"{what} forward"),
        )
    elif any(key in data for key in ("view", "position", "up", "forward")):
        raise CameraBundleError(f"{what} is projection-only but carries view fields")
    return CameraRecord(
        frame=frame,
        sample=sample,
        replay_time=replay_time,
        capture_pts_100ns=pts,
        state=state,
        view=view,
        projection=projection,
        inverse_z=inverse_z,
        viewport_min=viewport_min,
        viewport_max=viewport_max,
        near=near,
        far=far,
    )


def parse_bundle(data) -> CameraBundle:
    """Build a bundle from already-decoded JSON; raises CameraBundleError."""
    if not isinstance(data, dict):
        raise CameraBundleError("top level is not an object")
    if data.get("format") != FORMAT:
        raise CameraBundleError("not a Dolly camera bundle")
    if type(data.get("version")) is not int or data.get("version") != VERSION:
        raise CameraBundleError(f"unsupported camera bundle version {data.get('version')!r}")
    complete = data.get("complete")
    if not isinstance(complete, bool):
        raise CameraBundleError("complete must be a boolean")
    frames = _integer(_required(data, "frames", "header"), "frames")
    width = _integer(_required(data, "width", "header"), "width")
    height = _integer(_required(data, "height", "header"), "height")
    color_width = _integer(_required(data, "color_width", "header"), "color_width")
    color_height = _integer(_required(data, "color_height", "header"), "color_height")
    if frames < 0 or width <= 0 or height <= 0 or color_width < 0 or color_height < 0:
        raise CameraBundleError("negative or empty dimensions")
    reason = data.get("partial_reason")
    if reason is not None and not isinstance(reason, str):
        raise CameraBundleError("partial_reason must be a string")
    records_data = _required(data, "records", "header")
    if not isinstance(records_data, list):
        raise CameraBundleError("records must be an array")
    records = tuple(_record(item, index) for index, item in enumerate(records_data))
    return CameraBundle(
        complete=complete,
        frames=frames,
        width=width,
        height=height,
        color_width=color_width,
        color_height=color_height,
        records=records,
        partial_reason=reason,
    )


def load_bundle(path) -> CameraBundle:
    """Read and parse a camera bundle file; raises CameraBundleError."""
    path = Path(path)
    try:
        if not path.is_file():
            raise CameraBundleError("camera bundle is missing or oversized")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_FILE_BYTES + 1)
        if not 0 < len(raw) <= _MAX_FILE_BYTES:
            raise CameraBundleError("camera bundle is missing or oversized")
        data = json.loads(raw.decode("utf-8"))
    except CameraBundleError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise CameraBundleError(f"camera bundle could not be read: {exc}") from exc
    return parse_bundle(data)


def linearize(inverse_z, viewport_min: float, viewport_max: float,
              device_z: float) -> float | None:
    """Device depth -> positive camera-axis distance; None when invalid.

    Mirrors the native depth conversion: a zero denominator is the far endpoint
    of an infinite projection (positive infinity), and out-of-range or
    non-positive results are invalid.
    """
    if not math.isfinite(device_z):
        return None
    if device_z < viewport_min or device_z > viewport_max:
        return None
    a, b, c, d = inverse_z
    span = viewport_max - viewport_min
    if span <= 0:
        return None
    z = (device_z - viewport_min) / span
    denominator = c * z + d
    if denominator == 0:
        return math.inf
    value = -(a * z + b) / denominator
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _close(a: float, b: float, tolerance: float) -> bool:
    return (math.isfinite(a) and math.isfinite(b)
            and abs(a - b) <= tolerance * max(1.0, abs(a), abs(b)))


def _check_projection(record: CameraRecord, what: str, issues: list[str], *,
                      width: int, height: int) -> None:
    p = record.projection
    for index in (1, 3, 4, 7, 8, 9, 12, 13):
        if not _close(p[index], 0.0, _STRUCTURE_TOLERANCE):
            issues.append(f"{what}: projection is oblique or malformed")
            return
    # Temporal AA shifts clip X/Y by a bounded fraction of the depth viewport.
    # Keep the sampled values and normalize by clip W's homogeneous scale,
    # matching native admission including binary32 rounding at one pixel.
    limit = 2 * abs(p[14]) * (1 + 4 * _FLOAT32_EPSILON)
    if not (abs(p[2]) * width <= limit and abs(p[6]) * height <= limit):
        issues.append(f"{what}: projection jitter exceeds one depth pixel")
        return
    if p[0] == 0 or p[5] == 0:
        issues.append(f"{what}: projection scale is zero")
        return
    z0, z1, z2, z3 = record.inverse_z
    a, b = p[10], p[11]
    c, d = p[14], p[15]
    products = (a * z0 + b * z2, a * z1 + b * z3, c * z0 + d * z2, c * z1 + d * z3)
    for value, expected in zip(products, (1.0, 0.0, 0.0, 1.0)):
        if not _close(value, expected, _STRUCTURE_TOLERANCE):
            issues.append(f"{what}: inverse_z does not invert the projection")
            return


def _check_view(record: CameraRecord, what: str, issues: list[str]) -> None:
    view = record.view
    assert view is not None
    m = view.matrix
    if not (_close(m[3], 0.0, _VIEW_TOLERANCE) and _close(m[7], 0.0, _VIEW_TOLERANCE)
            and _close(m[11], 0.0, _VIEW_TOLERANCE) and _close(m[15], 1.0, _VIEW_TOLERANCE)):
        issues.append(f"{what}: view is not an affine matrix")
        return
    columns = ((m[0], m[4], m[8]), (m[1], m[5], m[9]), (m[2], m[6], m[10]))
    for first in range(3):
        for second in range(3):
            dot = sum(columns[first][k] * columns[second][k] for k in range(3))
            if not _close(dot, 1.0 if first == second else 0.0, _VIEW_TOLERANCE):
                issues.append(f"{what}: view axes are not orthonormal")
                return
    up, forward, position = view.up, view.forward, view.position
    for axis in range(3):
        if (not _close(up[axis], columns[1][axis], _VIEW_TOLERANCE)
                or not _close(forward[axis], -columns[2][axis], _VIEW_TOLERANCE)):
            issues.append(f"{what}: up/forward disagree with the view axes")
            return
        offset = m[12 + axis] + sum(columns[axis][component] * position[component]
                                    for component in range(3))
        if abs(offset) > _AFFINE_TOLERANCE:
            issues.append(f"{what}: view translation does not match the position")
            return


def validate_bundle(bundle: CameraBundle, *, require_complete: bool = False) -> list[str]:
    """Return semantic problems; an empty list means the bundle is consistent."""
    issues: list[str] = []
    count = len(bundle.records)
    if bundle.complete:
        if count == 0 or bundle.frames != count:
            issues.append("complete bundle does not match its record count")
        if bundle.partial_reason:
            issues.append("complete bundle carries a partial_reason")
    else:
        if count > bundle.frames:
            issues.append("more records than frames")
        if not bundle.partial_reason:
            issues.append("incomplete bundle has no partial_reason")
    expected_frame = 0
    previous_sample = None
    partial_states = 0
    for record in bundle.records:
        what = f"record {record.frame}"
        if record.frame != expected_frame:
            issues.append(f"{what}: frame index is not sequential")
        expected_frame = record.frame + 1
        if previous_sample is not None and record.sample <= previous_sample:
            issues.append(f"{what}: sample is not strictly increasing")
        previous_sample = record.sample
        if record.state != "complete":
            partial_states += 1
        _check_projection(record, what, issues, width=bundle.width, height=bundle.height)
        if record.view is not None:
            _check_view(record, what, issues)
    if bundle.complete and partial_states:
        issues.append(f"{partial_states} projection-only record(s) in a complete bundle")
    if require_complete and not bundle.complete:
        issues.append("bundle is not complete")
    return issues


def validate_against_manifest(bundle: CameraBundle, manifest) -> list[str]:
    """Cross-check a bundle against the depth manifest of the same take."""
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    issues = []
    frames = manifest.get("frames")
    if isinstance(frames, int) and not isinstance(frames, bool):
        if bundle.frames != frames:
            issues.append(f"frame count differs from the manifest ({bundle.frames} vs {frames})")
    if manifest.get("complete") is True and not bundle.complete:
        issues.append("bundle is not complete for a completed take")
    for key in ("width", "height"):
        value = manifest.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value:
            if getattr(bundle, key) != value:
                issues.append(f"{key} differs from the manifest")
    return issues


def project_point(bundle: CameraBundle, record: CameraRecord, point) -> ProjectedPoint | None:
    """Project a world-space point through one record.

    Returns None when the record cannot project the point (no view, behind the
    camera or an invalid calibration result). The pixel mapping is documented
    in ``docs/CAMERA_BUNDLE.md``; the depth master's own size is used.
    """
    view = record.view
    if view is None:
        return None
    if not isinstance(point, (list, tuple)) or len(point) != 3:
        raise CameraBundleError("point must be three numbers")
    x, y, z = (_number(value, "point") for value in point)
    m = view.matrix
    vx = x * m[0] + y * m[4] + z * m[8] + m[12]
    vy = x * m[1] + y * m[5] + z * m[9] + m[13]
    vz = x * m[2] + y * m[6] + z * m[10] + m[14]
    vw = x * m[3] + y * m[7] + z * m[11] + m[15]
    if not all(math.isfinite(value) for value in (vx, vy, vz, vw)) or abs(vw) < 1e-12:
        return None
    vx, vy, vz = vx / vw, vy / vw, vz / vw
    p = record.projection
    cx = p[0] * vx + p[1] * vy + p[2] * vz + p[3]
    cy = p[4] * vx + p[5] * vy + p[6] * vz + p[7]
    cz = p[8] * vx + p[9] * vy + p[10] * vz + p[11]
    cw = p[12] * vx + p[13] * vy + p[14] * vz + p[15]
    if not all(math.isfinite(value) for value in (cx, cy, cz, cw)):
        return None
    if cw <= 0:
        return None
    nx, ny, nz = cx / cw, cy / cw, cz / cw
    device_z = record.viewport_min + nz * (record.viewport_max - record.viewport_min)
    distance = linearize(record.inverse_z, record.viewport_min, record.viewport_max, device_z)
    if distance is None:
        return None
    pixel_x = (nx + 1.0) * 0.5 * bundle.width
    pixel_y = (1.0 - ny) * 0.5 * bundle.height
    if not math.isfinite(pixel_x) or not math.isfinite(pixel_y):
        return None
    return ProjectedPoint(
        x=pixel_x,
        y=pixel_y,
        device_z=device_z,
        distance=distance,
    )
