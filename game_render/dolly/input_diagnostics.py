"""Optional, read-only mouse and cursor observations from the native editor."""
from __future__ import annotations

import struct

OFFSET = 2 * 1024 * 1024 + 3584
WIRE = struct.Struct("<8s4I8QI36x")
MAGIC = b"DLYINP01"
COUNTERS = ("raw_mouse_packets", "relative_mouse_packets", "accepted_motion_packets",
            "legacy_mouse_moves", "consumed_motion_frames", "cursor_syncs", "cursor_failures")
FLAGS = (("raw_registration_known", 1), ("raw_mouse_registered", 2),
         ("target_is_game_window", 4), ("ready", 8), ("manual_active", 16),
         ("focused", 32), ("flight_owner", 64), ("cursor_clipped", 128),
         ("no_legacy", 256))


def unpack(data: bytes) -> dict | None:
    if len(data) != WIRE.size:
        raise ValueError("Native input diagnostic snapshot has the wrong size")
    if data == b"\0" * WIRE.size:
        return None  # Older ABI 3 helpers do not publish this optional block.
    values = WIRE.unpack(data)
    magic, sequence, abi, flags, registration_flags = values[:5]
    if magic != MAGIC or sequence & 1 or abi != 1 or flags & ~511:
        raise ValueError("Unrecognized input diagnostic snapshot")
    result = dict(zip(COUNTERS, values[5:12]))
    result.update(sequence=sequence, abi=abi, flags=flags,
                  registration_flags=registration_flags,
                  registration_target=values[12], last_cursor_error=values[13])
    result.update((name, bool(flags & flag)) for name, flag in FLAGS)
    return result
