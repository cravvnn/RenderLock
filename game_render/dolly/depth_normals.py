"""View-space and world-space normals reconstructed from a depth frame.

Offline foundation for the geometric-normals deliverable (SourceMVM roadmap,
Phase 4). Inputs are the depth master's per-pixel positive camera-axis
distances (as recorded for a paired take) and the matching camera bundle
record; the output is one unit normal per pixel plus validity and quality
masks. This module performs no file I/O; ``dolly/depth_exr.py`` reads the
take's single-channel float EXR frames and the capture experiment supplies the
arrays.

Prefer the take's float EXR sequence as the input. The grayscale depth.mov is
delivery data quantized through limited-range 10-bit luma: one code step is
about 9.4 camera-axis units at the 8192-unit scale (measured on a real take),
which would dominate normal detail; the mov is only suitable for previews.

Conventions (matrix conventions live in ``docs/CAMERA_BUNDLE.md``):

- Pixel ``(x, y)`` centers map to ``ndc_x = 2*(x + 0.5)/width - 1`` and
  ``ndc_y = 1 - 2*(y + 0.5)/height``; rows are top-down.
- View space: x right, y up, z backward (toward the viewer). A visible
  surface's normal points toward the viewer (``nz > 0``) unless the surface is
  edge-on.
- The view-space point behind a pixel at distance ``d`` follows the stored
  projection: ``vz = -d``, ``cw = P[3][2]*-d + P[3][3]``,
  ``vx = (ndc_x*cw - P[0][2]*vz - P[0][3])/P[0][0]``,
  ``vy = (ndc_y*cw - P[1][2]*vz - P[1][3])/P[1][1]``. The X/Y projection
  offsets retain the renderer's bounded temporal jitter.
- Gradients are central differences across two pixels
  (``P[col+1] - P[col-1]`` and ``P[row+1] - P[row-1]``); the normal is
  ``cross(gradient_down, gradient_right)`` normalized, which faces the viewer
  for visible surfaces. A neighbor participates only when it is valid and its
  distance is within ``step_absolute + step_relative*center_distance``, so
  foreground edges are never smeared; a pixel with only one usable side falls
  back to a one-sided gradient and is flagged in ``one_sided``.
- Validity: finite distance greater than zero, ``cw > 0``, and a finite unit
  result. NaN, zero/negative and +infinity (sky) samples are masked.
- Optional smoothing passes average a normal with its valid,
  distance-compatible neighbors only, so discontinuities stay intact;
  fixtures measure the noise reduction and the edge behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class NormalFrame:
    """One reconstructed normal frame.

    ``normals`` is (height, width, 3) float32 with unit vectors where ``valid``
    is true and NaN elsewhere. ``one_sided`` marks pixels whose gradient used
    a one-sided fallback (lower quality; borders and depth discontinuities).
    ``space`` is ``"view"`` or ``"world"``; ``smoothing`` is the applied pass
    count.
    """

    normals: np.ndarray
    valid: np.ndarray
    one_sided: np.ndarray
    space: str
    smoothing: int


def _array(distances) -> np.ndarray:
    array = np.asarray(distances, dtype=np.float64)
    if array.ndim != 2 or array.size == 0:
        raise ValueError("distances must be a non-empty two-dimensional array")
    return array


def view_points(record, distances):
    """View-space points for every pixel; invalid samples become NaN.

    Returns ``(points, valid)``: ``points`` is (height, width, 3) float64 and
    ``valid`` is (height, width) bool. A sample is valid when its distance is
    finite and positive, the projection's ``cw`` is positive, and the resulting
    point is finite.
    """
    distances = _array(distances)
    projection = record.projection
    if projection[0] == 0 or projection[5] == 0:
        raise ValueError("camera record has a zero projection scale")
    height, width = distances.shape
    ndc_x = 2.0 * (np.arange(width) + 0.5) / width - 1.0
    ndc_y = 1.0 - 2.0 * (np.arange(height) + 0.5) / height
    with np.errstate(invalid="ignore", divide="ignore"):
        cw = -projection[14] * distances + projection[15]
        vz = -distances
        vx = (ndc_x[None, :] * cw - projection[2] * vz - projection[3]) / projection[0]
        vy = (ndc_y[:, None] * cw - projection[6] * vz - projection[7]) / projection[5]
        points = np.stack([vx, vy, vz], axis=-1)
    valid = (np.isfinite(distances) & (distances > 0) & (cw > 0)
             & np.isfinite(points).all(axis=-1))
    points = np.where(valid[..., None], points, np.nan)
    return points, valid


def _shift(values: np.ndarray, axis: int, direction: int, fill):
    """``out[i] = values[i + direction]`` along ``axis``; out-of-range is fill."""
    index = [slice(None)] * values.ndim
    index[axis] = slice(1, None) if direction == 1 else slice(0, -1)
    part = values[tuple(index)]
    edge_shape = list(values.shape)
    edge_shape[axis] = 1
    edge = np.full(edge_shape, fill, dtype=values.dtype)
    if direction == 1:
        return np.concatenate([part, edge], axis=axis)
    return np.concatenate([edge, part], axis=axis)


def _gradient(points: np.ndarray, valid: np.ndarray, distances: np.ndarray,
              limit: np.ndarray, axis: int):
    """One view-space gradient per pixel along ``axis`` (0 rows, 1 columns)."""
    neighbor_plus = _shift(points, axis, 1, np.nan)
    neighbor_minus = _shift(points, axis, -1, np.nan)
    valid_plus = _shift(valid, axis, 1, False)
    valid_minus = _shift(valid, axis, -1, False)
    distance_plus = _shift(distances, axis, 1, np.inf)
    distance_minus = _shift(distances, axis, -1, np.inf)
    with np.errstate(invalid="ignore"):
        plus_ok = valid & valid_plus & (np.abs(distance_plus - distances) <= limit)
        minus_ok = valid & valid_minus & (np.abs(distance_minus - distances) <= limit)
    gradient = np.full(points.shape, np.nan)
    one_sided = np.zeros(valid.shape, dtype=bool)
    both = plus_ok & minus_ok
    plus_only = plus_ok & ~both
    minus_only = minus_ok & ~both & ~plus_only
    gradient[both] = (neighbor_plus - neighbor_minus)[both]
    gradient[plus_only] = (neighbor_plus - points)[plus_only]
    gradient[minus_only] = (points - neighbor_minus)[minus_only]
    one_sided |= plus_only | minus_only
    return gradient, one_sided


def _smooth(normals: np.ndarray, valid: np.ndarray, distances: np.ndarray,
            limit: np.ndarray, passes: int) -> np.ndarray:
    for _ in range(passes):
        accum = np.where(valid[..., None], normals, 0.0)
        counts = valid.astype(np.float64)
        for axis in (0, 1):
            for direction in (1, -1):
                neighbor = _shift(normals, axis, direction, np.nan)
                neighbor_valid = _shift(valid, axis, direction, False)
                neighbor_distance = _shift(distances, axis, direction, np.inf)
                with np.errstate(invalid="ignore"):
                    use = valid & neighbor_valid & (
                        np.abs(neighbor_distance - distances) <= limit)
                accum = accum + np.where(use[..., None], neighbor, 0.0)
                counts = counts + use
        lengths = np.linalg.norm(accum, axis=-1)
        keep = valid & np.isfinite(lengths) & (lengths > 1e-12)
        smoothed = np.full(normals.shape, np.nan)
        smoothed[keep] = accum[keep] / lengths[keep, None]
        normals = smoothed
    return normals


def world_normals(record, normals):
    """Rotate view-space normals into world space with the record's view axes."""
    normals = np.asarray(normals, dtype=np.float64)
    if normals.shape[-1] != 3:
        raise ValueError("normals must end in exactly three components")
    matrix = record.view.matrix
    basis = np.array([[matrix[0], matrix[4], matrix[8]],
                      [matrix[1], matrix[5], matrix[9]],
                      [matrix[2], matrix[6], matrix[10]]], dtype=np.float64)
    with np.errstate(invalid="ignore"):
        rotated = normals @ basis
    lengths = np.linalg.norm(rotated, axis=-1)
    keep = np.isfinite(lengths) & (lengths > 1e-12)
    world = np.full(rotated.shape, np.nan)
    world[keep] = rotated[keep] / lengths[keep, None]
    return world


def reconstruct_normals(record, distances, *, step_relative: float = 0.05,
                        step_absolute: float = 0.25, smoothing: int = 0,
                        world: bool = False) -> NormalFrame:
    """Reconstruct per-pixel normals from a depth frame and its camera record.

    ``step_relative``/``step_absolute`` set the per-pixel distance
    compatibility window; ``smoothing`` applies that many smoothing passes;
    ``world`` converts the result with :func:`world_normals`.
    """
    distances = _array(distances)
    if (isinstance(step_relative, bool) or isinstance(step_absolute, bool)
            or not isinstance(step_relative, (int, float)) or not isinstance(step_absolute, (int, float))
            or not 0 <= step_relative < float("inf") or not 0 <= step_absolute < float("inf")):
        raise ValueError("step tolerances must not be negative")
    if type(smoothing) is not int or smoothing < 0:
        raise ValueError("smoothing must not be negative")
    points, valid = view_points(record, distances)
    limit = step_absolute + step_relative * distances
    gradient_down, one_sided_down = _gradient(points, valid, distances, limit, axis=0)
    gradient_right, one_sided_right = _gradient(points, valid, distances, limit, axis=1)
    one_sided = one_sided_down | one_sided_right
    tangents = np.cross(gradient_down, gradient_right)
    lengths = np.linalg.norm(tangents, axis=-1)
    keep = valid & np.isfinite(lengths) & (lengths > 1e-12)
    normals = np.full(tangents.shape, np.nan)
    normals[keep] = tangents[keep] / lengths[keep, None]
    if smoothing:
        normals = _smooth(normals, keep, distances, limit, int(smoothing))
    space = "view"
    if world:
        normals = world_normals(record, normals)
        space = "world"
    return NormalFrame(normals=normals.astype(np.float32),
                       valid=keep,
                       one_sided=one_sided & keep,
                       space=space,
                       smoothing=int(smoothing))
