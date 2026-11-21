"""Reader for the depth sequence's OpenEXR frames (single-channel float Z).

The native depth writer produces a minimal, standard single-part scanline
OpenEXR: exactly one FLOAT channel named ``Z``, no compression, increasing
line order, little-endian, values are positive camera-axis distances. FFmpeg
cannot decode this shape ("Not yet implemented in FFmpeg"), so Dolly reads its
own frames directly instead of guessing at third-party conversions. Only that
exact shape is accepted; anything else fails with ``DepthExrError``.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

_MAGIC = 20000630
_VERSION = 2


class DepthExrError(ValueError):
    """The file is not a supported single-channel depth EXR frame."""


def _attribute(data: bytes, offset: int):
    end = data.find(b"\0", offset)
    if end < 0:
        raise DepthExrError("truncated attribute name")
    name = data[offset:end].decode("ascii", "replace")
    offset = end + 1
    end = data.find(b"\0", offset)
    if end < 0:
        raise DepthExrError("truncated attribute type")
    kind = data[offset:end].decode("ascii", "replace")
    offset = end + 1
    if offset + 4 > len(data):
        raise DepthExrError("truncated attribute size")
    size = struct.unpack_from("<i", data, offset)[0]
    offset += 4
    if size < 0 or offset + size > len(data):
        raise DepthExrError("truncated attribute payload")
    return name, kind, data[offset:offset + size], offset + size


def read_distances(path) -> np.ndarray:
    """Read one depth frame as a (height, width) float32 array, top-down."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DepthExrError(f"depth frame could not be read: {exc}") from exc
    if len(data) < 12 or struct.unpack_from("<i", data, 0)[0] != _MAGIC:
        raise DepthExrError("not an OpenEXR file")
    version = struct.unpack_from("<i", data, 4)[0]
    if version != _VERSION:
        raise DepthExrError(f"unsupported EXR version {version}")
    offset = 8
    channels = None
    data_window = None
    compression = None
    line_order = None
    while True:
        if offset >= len(data):
            raise DepthExrError("truncated header")
        if data[offset] == 0:
            offset += 1
            break
        name, kind, payload, offset = _attribute(data, offset)
        if name == "channels":
            if kind != "chlist":
                raise DepthExrError("unexpected channels attribute")
            channels = payload
        elif name == "dataWindow":
            if kind != "box2i" or len(payload) != 16:
                raise DepthExrError("unexpected dataWindow attribute")
            data_window = struct.unpack("<4i", payload)
        elif name == "compression":
            compression = payload[0] if payload else None
        elif name == "lineOrder":
            line_order = payload[0] if payload else None
    if channels is None or data_window is None:
        raise DepthExrError("missing channels or dataWindow")
    if compression != 0:
        raise DepthExrError("only uncompressed depth frames are supported")
    if line_order != 0:
        raise DepthExrError("only increasing line order is supported")
    if (len(channels) != 19 or channels[:2] != b"Z\0" or channels[-1] != 0
            or struct.unpack_from("<i", channels, 2)[0] != 2
            or struct.unpack_from("<2i", channels, 10) != (1, 1)):
        raise DepthExrError("expected exactly one unsampled FLOAT Z channel")
    x_min, y_min, x_max, y_max = data_window
    width, height = x_max - x_min + 1, y_max - y_min + 1
    if width <= 0 or height <= 0 or width * height > 64 * 1024 * 1024:
        raise DepthExrError("unsupported frame dimensions")
    if offset + 8 * height > len(data):
        raise DepthExrError("truncated scanline table")
    rows = np.empty((height, width), dtype=np.float32)
    for index in range(height):
        row_offset = struct.unpack_from("<Q", data, offset + index * 8)[0]
        if row_offset < offset + 8 * height or row_offset + 8 > len(data):
            raise DepthExrError("scanline offset out of range")
        y, size = struct.unpack_from("<2i", data, row_offset)
        if y != y_min + index or size != width * 4:
            raise DepthExrError("unexpected scanline shape")
        if row_offset + 8 + size > len(data):
            raise DepthExrError("truncated scanline data")
        rows[index] = np.frombuffer(data, dtype="<f4", count=width, offset=row_offset + 8)
    return rows
