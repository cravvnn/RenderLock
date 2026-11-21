"""Optional, read-only DX11 observations; never used to control the camera."""
from __future__ import annotations

import re
import struct

OFFSET = 2 * 1024 * 1024 + 1024
WIRE = struct.Struct("<8s4I9Q10I65s7x192s11Q24x512s")
MAGIC = b"DLYGFX01"
OVERLAY_FIELDS = (
    "overlay_draw_frames", "guide_frames", "overlay_last_us", "overlay_max_us",
    "present_last_us", "present_max_us", "overlay_lock_skips",
    "overlay_active_since_ms", "present_active_since_ms", "guide_lines", "guide_labels",
)
STATES = ("waiting", "supported", "unsupported", "unreadable", "racing")
COUNTERS = ("sample", "uptime_ms", "main_view_frames", "present_calls",
            "panel_frames", "init_attempts", "init_successes", "release_calls", "resize_calls")
RENDERER_FIELDS = ("pending_count", "capacity", "allocated_slots", "head_index",
                   "tail_index", "retirement_frame", "execution_frame",
                   "head_buffer_frame", "tail_buffer_frame", "image_size")


def unpack(data: bytes) -> dict | None:
    if data == b"\0" * WIRE.size:
        return None  # Older ABI 3 helpers do not publish this optional block.
    values = WIRE.unpack(data)
    magic, sequence, abi, state, flags = values[:5]
    if magic != MAGIC or sequence & 1 or abi not in (1, 2, 3) or state >= len(STATES) or flags & ~127:
        raise ValueError("Unrecognized graphics diagnostic snapshot")
    digest = values[24].split(b"\0", 1)[0].decode("ascii")
    if digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Invalid renderer diagnostic fingerprint")
    result = dict(zip(COUNTERS, values[5:14]))
    result.update(zip(RENDERER_FIELDS, values[14:24]))
    result.update(state=STATES[state], flags=flags, renderer_sha256=digest,
                  header_consistent=bool(flags & 2),
                  message=values[25].split(b"\0", 1)[0].decode("utf-8", errors="replace"))
    result.update(zip(OVERLAY_FIELDS, values[26:37] if abi >= 2 else (None,) * len(OVERLAY_FIELDS)))
    result["layers"] = (values[37].split(b"\0", 1)[0].decode("utf-8", errors="replace")
                        if abi >= 3 else "")
    for key in RENDERER_FIELDS[:5]:
        if not flags & 1:
            result[key] = None
    for key, flag in (("retirement_frame", 4), ("execution_frame", 8),
                      ("head_buffer_frame", 16), ("tail_buffer_frame", 32),
                      ("main_view_frames", 64)):
        if not flags & flag:
            result[key] = None
    return result
