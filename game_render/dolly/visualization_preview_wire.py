"""Bounded render-only draft envelope around an unchanged DLYVIS4 packet."""
from __future__ import annotations

import math
import struct

from . import visualization_wire as viewer
from .native_path import MAX_CAMERA_KEYS

MAGIC = b"DLYPRV01"
ABI = 1
HEADER = struct.Struct("<8s6IQ2Id2I")
HEADER_BYTES = HEADER.size
SEQUENCE_OFFSET = 8
MAPPING_BYTES = viewer.MAPPING_BYTES
DESKTOP = 1
NATIVE = 2


def _uint(value, label, low=0, high=0xFFFFFFFF):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def build_preview(packet=None, *, sequence=2, source=0, token=0, revision=0,
                  session_id=0, selected_camera=0, channel=0, key_time=0.0):
    """Wrap compiled bytes; heartbeat publications reuse those exact bytes.

    An identity-bearing disabled envelope retires a terminal gesture. An empty
    disabled envelope hides an invalid candidate without retiring its token,
    and also clears transport on disconnect. This never carries controls.
    """
    sequence = _uint(sequence, "Preview sequence", 2, 0xFFFFFFFE)
    if sequence & 1:
        raise ValueError("Preview sequence must be even")
    source = _uint(source, "Preview source", 0, NATIVE)
    token = _uint(token, "Preview token")
    revision = _uint(revision, "Preview revision")
    session_id = _uint(session_id, "Preview session", 0, 0xFFFFFFFFFFFFFFFF)
    selected_camera = _uint(selected_camera, "Preview camera", 0, MAX_CAMERA_KEYS-1)
    channel = _uint(channel, "Preview channel", 0, 6)
    if (isinstance(key_time, bool) or not isinstance(key_time, (int, float))
            or not math.isfinite(key_time) or key_time < 0):
        raise ValueError("Preview key time must be finite and nonnegative")
    empty = not any((source, token, revision, session_id, selected_camera, channel, key_time))
    if not empty and (source not in (DESKTOP, NATIVE) or not token or not revision or not session_id):
        raise ValueError("Preview requires a complete gesture identity")
    enabled = packet is not None
    if enabled:
        if empty or not isinstance(packet, bytes) or len(packet) < viewer.HEADER_BYTES:
            raise ValueError("Preview requires a compiled viewer packet")
        h = viewer.HEADER.unpack_from(packet)
        count = h[5]
        if (h[0] != viewer.MAGIC or h[1] < 2 or h[1] & 1 or h[2] != 4 or h[3] != 1
                or h[4] != selected_camera or not 0 < count <= MAX_CAMERA_KEYS
                or selected_camera >= count or h[9] != revision or h[10] != session_id
                or h[11] != count*viewer.KEY_METADATA.size or h[12] & ~3 or h[13]
                or len(packet) != viewer.HEADER_BYTES+count*8+h[11]+h[6]
                or len(packet)+HEADER_BYTES > MAPPING_BYTES):
            raise ValueError("Preview viewer packet does not match its gesture identity or bounds")
        if struct.unpack_from("<d", packet, viewer.HEADER_BYTES+selected_camera*8)[0] != key_time:
            raise ValueError("Preview viewer packet changed the selected key time")
    payload = packet if enabled else b""
    return HEADER.pack(MAGIC, sequence, ABI, int(enabled), source, token, revision,
                       session_id, selected_camera, channel, key_time, len(payload), 0)+payload
