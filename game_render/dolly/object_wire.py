"""Versioned wire block for the Object Picker, separate from camera payload.

Two blocks live at the start of one appended 4 KiB page after the legacy tail
(which ends at 2 MiB + 24448 with the old MAPPING_BYTES 2 MiB + 24576), so every
existing offset is preserved:

- Object config (editor -> native): Open/active flag, selected index, aim
  distance, and a bounded list of placed objects.
- Object status (native -> editor): availability, active count and selected
  index. Placement itself comes back through the editor event ring, so no new
  status channel is needed.

All fields are validated the way ``editor_wire`` blocks are: magic, ABI,
sequence parity, finite numbers, bounded indices. A torn or malformed block is
never accepted. ``native_bridge.MAPPING_BYTES`` and ``dolly_protocol.hpp``
``kMappingBytes`` grow to ``OBJECT_MAPPING_BYTES`` in step with this module.
"""
from __future__ import annotations

import math
import struct

from .object_library import MAX_OBJECTS, OBJECT_IDS
from .native_effects import model_token

CONTROL_BYTES = 2 * 1024 * 1024
LEGACY_TAIL_BYTES = 24576
OBJECT_PAGE_BYTES = 4096

OBJECT_BASE = CONTROL_BYTES + LEGACY_TAIL_BYTES
OBJECT_MAPPING_BYTES = OBJECT_BASE + OBJECT_PAGE_BYTES

OBJECT_CONFIG_MAGIC = b"DLYOBJ01"   # 8 bytes
OBJECT_STATUS_MAGIC = b"DLYOBS01"   # 8 bytes
OBJECT_CONFIG_ABI = 1
OBJECT_STATUS_ABI = 1

POSITION_LIMIT = 1_000_000.0
ANGLE_LIMIT = 100_000.0
SCALE_MIN = 0.05
SCALE_MAX = 20.0
DISTANCE_MIN = 10.0
DISTANCE_MAX = 20000.0
DISTANCE_DEFAULT = 600.0

# magic, sequence, abi, flags, count, selected, reserved (bindable action)
OBJECT_CONFIG_HEADER = struct.Struct("<8s4IiI")
# aim distance in world units
OBJECT_CONFIG_DISTANCE = struct.Struct("<d")
# library index, model token, position[3], angles[3], scale
OBJECT_ROW = struct.Struct("<IQ3f3ff")
OBJECT_CONFIG = struct.Struct(
    "<8s4IiI" + "d" + "IQ3f3ff" * MAX_OBJECTS)

# flags bits: 1 open/active, 2 preview
OBJECT_CONFIG_FLAGS = 0b11
OBJECT_STATUS_FLAGS = 0b111

OBJECT_CONFIG_OFFSET = OBJECT_BASE
# magic, sequence, abi, flags, active_count, selected, error
OBJECT_STATUS = struct.Struct("<8s4IiI")
OBJECT_STATUS_OFFSET = OBJECT_BASE + OBJECT_CONFIG.size

_OBJECT_INDEX = {name: index for index, name in enumerate(OBJECT_IDS)}


def object_payload_bytes() -> int:
    return OBJECT_CONFIG.size + OBJECT_STATUS.size


def pack_config(sequence, objects, *, active=False, preview=False, selected=-1,
                distance=DISTANCE_DEFAULT):
    """Pack the Object Picker config block.

    ``objects`` may be ``PlacedObject`` instances or equivalent dicts; bounds are
    re-checked here so a corrupt caller cannot publish a malformed block even if
    ``object_model.validate`` was skipped.
    """
    sequence = _uint(sequence, "sequence")
    if not isinstance(objects, (list, tuple)) or len(objects) > MAX_OBJECTS:
        raise ValueError(f"At most {MAX_OBJECTS} objects can be placed")
    flags = (int(bool(active)) | (int(bool(preview)) << 1))
    if not -1 <= selected < max(1, len(objects)):
        raise ValueError("Selected object is out of range")
    distance = _finite(distance, DISTANCE_MIN, DISTANCE_MAX, "aim distance")

    data = bytearray(OBJECT_CONFIG.size)
    OBJECT_CONFIG_HEADER.pack_into(data, 0, OBJECT_CONFIG_MAGIC, sequence,
                                   OBJECT_CONFIG_ABI, flags, len(objects), int(selected), 0)
    OBJECT_CONFIG_DISTANCE.pack_into(data, OBJECT_CONFIG_HEADER.size, distance)
    offset = OBJECT_CONFIG_HEADER.size + OBJECT_CONFIG_DISTANCE.size
    for obj in objects:
        library_id = obj.library_id if hasattr(obj, "library_id") else obj["library_id"]
        if library_id not in _OBJECT_INDEX:
            raise ValueError("Object names an entry outside the library")
        model = (obj.model if hasattr(obj, "model") else obj.get("model", "")) or ""
        token = model_token(model) if model else 0
        position = obj.position if hasattr(obj, "position") else obj["position"]
        angles = obj.angles if hasattr(obj, "angles") else obj["angles"]
        scale = obj.scale if hasattr(obj, "scale") else obj["scale"]
        values = _vec(position, "position") + _vec(angles, "angles")
        if any(abs(v) > POSITION_LIMIT for v in values[:3]) or any(abs(v) > ANGLE_LIMIT for v in values[3:]):
            raise ValueError("Object transform is outside the reviewed range")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) \
                or not math.isfinite(scale) or not SCALE_MIN <= scale <= SCALE_MAX:
            raise ValueError("Object scale is outside the reviewed range")
        OBJECT_ROW.pack_into(data, offset, _OBJECT_INDEX[library_id], token, *values, float(scale))
        offset += OBJECT_ROW.size
    return bytes(data)


def unpack_status(data):
    """Parse the native object status row. A zeroed block means not published."""
    if len(data) != OBJECT_STATUS.size:
        raise ValueError("Native object status returned the wrong size")
    if not any(data):
        return None
    magic, sequence, abi, flags, active, selected, error = OBJECT_STATUS.unpack(data)
    if (magic != OBJECT_STATUS_MAGIC or abi != OBJECT_STATUS_ABI or sequence & 1
            or flags & ~OBJECT_STATUS_FLAGS):
        raise ValueError("Native object status does not match this build")
    if active > MAX_OBJECTS or not -1 <= selected < MAX_OBJECTS:
        raise ValueError("Native object status has an out-of-range index")
    return {"sequence": sequence, "available": bool(flags & 1), "active": bool(flags & 2),
            "preview": bool(flags & 4), "active_count": active, "selected": selected,
            "error": error}


def _uint(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"Invalid object {name}")
    return value


def _finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Object {name} is outside the reviewed range")
    return float(value)


def _vec(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"Object {name} must be three numbers")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError(f"Object {name} must be three finite numbers")
        result.append(float(item))
    return result
