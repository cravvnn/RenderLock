"""Compile authored camera channels once for the native render-view bridge.

The little-endian, version-1 format has a 160-byte header followed by N
296-byte segments (at most 4095; 4096 authored camera keys):

  header: 8s magic, 4I version/segment_count/channel_count/reserved,
          3d duration/first_camera_time/last_camera_time, 7d first, 7d last
  segment: 2d begin/end, followed by seven channel records
  channel: 2I kind/flags, 4d left/right/left_derivative/right_derivative

Magic is b"DLYPATH\\0". Kinds are step=0, linear=1, cubic Hermite=2.
Flag bit 0 clamps the result to its two endpoints (the editor's PCHIP rule).
Hermite derivatives use shot seconds; evaluation uses normalized segment
position and the segment duration. Unknown bits, versions and trailing bytes
are rejected by the native parser. No console command or cvar name is stored.

Channel order is X, Y, Z, pitch, yaw, roll, aspect ratio. Captured lenses use
version 2: an eighth lens-scale channel, 176-byte header and 336-byte segments.
The old FOV field remains legacy metadata. native_effects wraps the camera blob with supported DOF
curves; their final timestamp extends the overall native shot duration.
"""

from __future__ import annotations

import struct

from .path import Project


CHANNELS = ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
MAGIC = b"DLYPATH\0"
VERSION = 1
MAX_CAMERA_KEYS = 4096
HEADER = struct.Struct("<8sIIII17d")
LENS_HEADER = struct.Struct("<8sIIII19d")
SEGMENT_TIMES = struct.Struct("<2d")
CHANNEL_RECORD = struct.Struct("<II4d")
HEADER_BYTES = HEADER.size
SEGMENT_BYTES = SEGMENT_TIMES.size + len(CHANNELS) * CHANNEL_RECORD.size


def compile_project(project: Project) -> bytes:
    """Return one immutable path blob without changing the saved project.

The current Project evaluator remains the definition of camera motion:
nonuniform position Hermite, bounded angular/zoom PCHIP, shortest yaw/roll
unwrapping, two-key linear motion, and linear fallback for invalid tangents.
The callback consumes these coefficients rather than a sampled pose stream.
"""
    if not isinstance(project, Project):
        raise ValueError("Native camera compilation requires a Project")
    project.validate()
    keys = project.keyframes
    if not keys:
        raise ValueError("Add a camera keyframe before compiling the shot")
    if len(keys) > MAX_CAMERA_KEYS:
        raise ValueError(f"Native camera paths support at most {MAX_CAMERA_KEYS:,} camera keys")
    times = [float(key.time) for key in keys]
    channels: list[tuple[tuple[float, ...], int, int, tuple[float, ...], tuple[float, ...]]] = []
    names = CHANNELS + (("lens_scale",) if any(k.lens_scale is not None for k in keys) else ())
    for name in names:
        samples = project._camera_channel_samples(name)
        values, interpolation = samples.values, samples.interpolation
        # The native clamp bit follows the evaluator: the spline mode is
        # intentionally unclamped for every channel.
        requested_interpolation = (project.lens_interpolation
                                   if name in ("aspect_ratio", "lens_scale") else project.interpolation)
        flags = int(name not in ("x", "y", "z") and requested_interpolation != "spline")
        kind = 0 if interpolation == "step" else 1
        incoming = outgoing = (0.0,) * len(keys)
        if samples.tangents is not None:
            incoming = samples.incoming_tangents or samples.tangents
            outgoing = samples.outgoing_tangents or samples.tangents
            kind = 2
        channels.append((values, kind, flags, incoming, outgoing))
    result = bytearray((LENS_HEADER if len(names) == 8 else HEADER).pack(
        MAGIC, 2 if len(names) == 8 else VERSION, len(keys) - 1, len(names), 0,
        project.duration, times[0], times[-1],
        *(channel[0][0] for channel in channels),
        *(channel[0][-1] for channel in channels),
    ))
    for index in range(len(keys) - 1):
        result.extend(SEGMENT_TIMES.pack(times[index], times[index + 1]))
        for values, kind, flags, incoming, outgoing in channels:
            result.extend(CHANNEL_RECORD.pack(
                kind, flags, values[index], values[index + 1],
                outgoing[index], incoming[index + 1],
            ))
    return bytes(result)
