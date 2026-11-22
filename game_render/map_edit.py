"""Hide static map pieces in the running game (the "<session>.mapedit" mapping).

Layout matches native/include/dolly_map_edit.hpp. Two kinds of pieces:

- Aggregate fragments (most static map geometry). The game's world renderer keeps
  the world bounding box of every fragment in one table that its GPU culling
  reads for every view; the native side publishes that table here and moves the
  boxes of the records in the hide list out of the world. Records are indexed in
  the game's own order, fixed for a map build; `records_hash` identifies it.
- Standalone scene objects (lamps, signs, glass...). The scene system skips
  objects whose class has flag 8. `reserve_class` gives an empty class that flag
  (sc_setclassflags) and the native side moves the listed objects into it.
  Objects are found with the engine's own `sc_dumpworld` listing.

Baked lighting (shadows the pieces cast) stays as it was. Boxes and origins are
in Source units.
"""
from __future__ import annotations

import mmap
import re
import struct
import time
from pathlib import Path

import numpy as np

MAGIC = b"DLYMED01"
VERSION = 2
OBJECTS_OFFSET = 1024
OBJECT_CAPACITY = 128
OBJECT = struct.Struct("<Q3fI")
IDS_OFFSET = 4096
ID_CAPACITY = 65536
RECORDS_OFFSET = IDS_OFFSET + 4 * ID_CAPACITY
RECORD_BYTES = 32
MAX_RECORDS = 262144
SIZE = RECORDS_OFFSET + RECORD_BYTES * MAX_RECORDS
HEADER = struct.Struct("<8sIIQIIQIIQQIIQ")
HIDE_SEQ_OFFSET = 16
HIDE_COUNT_OFFSET = 24
STATUS = struct.Struct("<QIIQQI")  # at offset 32
STATUS_OFFSET = 32
HIDE_HASH_OFFSET = 72
OBJECT_SEQ_OFFSET = 80
OBJECT_COUNT_OFFSET = 88           # u32 count, then u32 spare class
OBJECT_STATUS = struct.Struct("<QII")  # at 96: applied seq, objects hidden, class table found
OBJECT_STATUS_OFFSET = 96
CLASS_FLAGS_OFFSET = 112
CLASS_SLOTS = 64
NO_CLASS = 0xFFFFFFFF


def mapping_name(token: str) -> str:
    return "Local\\DeadlockDollyNative_" + token + ".mapedit"


def _bump(view, offset):
    """Start a seqlock write: the sequence becomes odd. Returns the value to publish after."""
    seq = struct.unpack_from("<Q", view, offset)[0]
    seq = seq + 1 if seq % 2 == 0 else seq + 2
    struct.pack_into("<Q", view, offset, seq)
    return seq + 1


class MapEdit:
    def __init__(self, token: str):
        self._map = mmap.mmap(-1, SIZE, tagname=mapping_name(token), access=mmap.ACCESS_WRITE)
        self._map[:IDS_OFFSET] = b"\0" * IDS_OFFSET
        self._map[:HEADER.size] = HEADER.pack(MAGIC, VERSION, ID_CAPACITY, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        struct.pack_into("<II", self._map, OBJECT_COUNT_OFFSET, 0, NO_CLASS)
        self.hidden: list[int] = []
        self.objects: list[tuple[int, tuple]] = []
        self.spare_class = None
        self.synced = False      # the scene's saved list was sent (Blender side)
        self._records = None
        self._records_serial = 0

    def close(self):
        if self._map is not None:
            self._map.close()
            self._map = None

    # ---- aggregate fragments
    def status(self) -> dict:
        applied, count, hidden, serial, digest, capacity = STATUS.unpack_from(self._map, STATUS_OFFSET)
        seq = struct.unpack_from("<Q", self._map, HIDE_SEQ_OFFSET)[0]
        return {"records": count, "serial": serial, "hash": f"{digest:016x}", "hide_seq": seq,
                "applied_seq": applied, "applied_hidden": hidden, "capacity": capacity}

    def records(self):
        """The game's table as (lo, hi) float arrays of shape (n, 3) in Source units, or None."""
        state = self.status()
        if not state["records"]:
            return None
        if self._records is None or self._records_serial != state["serial"]:
            raw = np.frombuffer(self._map, dtype=np.float32, count=state["records"] * 8,
                                offset=RECORDS_OFFSET).reshape(-1, 8).copy()
            self._records = (raw[:, 0:3], raw[:, 4:7])
            self._records_serial = state["serial"]
        return self._records

    def set_hidden(self, ids, table_hash=None):
        """Replace the fragment hide list (record indices); an empty list shows them all.

        `table_hash` (hex, as status()["hash"]) names the table the indices belong to;
        it defaults to the table the game shows now. The game ignores a list made
        for another table."""
        ids = sorted({int(i) for i in ids if 0 <= int(i) < 2**32})[:ID_CAPACITY]
        publish = _bump(self._map, HIDE_SEQ_OFFSET)
        if ids:
            self._map[IDS_OFFSET:IDS_OFFSET + 4 * len(ids)] = np.asarray(ids, dtype=np.uint32).tobytes()
        struct.pack_into("<I", self._map, HIDE_COUNT_OFFSET, len(ids))
        digest = table_hash if table_hash is not None else self.status()["hash"]
        struct.pack_into("<Q", self._map, HIDE_HASH_OFFSET, int(digest, 16))
        struct.pack_into("<Q", self._map, HIDE_SEQ_OFFSET, publish)
        self.hidden = ids
        return publish

    def applied(self, seq) -> bool:
        return self.status()["applied_seq"] == seq

    # ---- standalone scene objects
    def object_status(self) -> dict:
        applied, hidden, found = OBJECT_STATUS.unpack_from(self._map, OBJECT_STATUS_OFFSET)
        seq = struct.unpack_from("<Q", self._map, OBJECT_SEQ_OFFSET)[0]
        return {"object_seq": seq, "applied_seq": applied, "hidden": hidden, "class_table": bool(found)}

    def class_flags(self) -> bytes:
        return bytes(self._map[CLASS_FLAGS_OFFSET:CLASS_FLAGS_OFFSET + CLASS_SLOTS])

    def set_objects(self, objects, spare_class=None):
        """Hide these scene objects, [(address, (x, y, z) origin)]; an empty list shows them all.

        The game checks each object's origin before touching it."""
        objects = [(int(a), tuple(float(v) for v in o)) for a, o in list(objects)[:OBJECT_CAPACITY]]
        spare = self.spare_class if spare_class is None else spare_class
        publish = _bump(self._map, OBJECT_SEQ_OFFSET)
        for i, (address, origin) in enumerate(objects):
            OBJECT.pack_into(self._map, OBJECTS_OFFSET + i * OBJECT.size, address, *origin, 0)
        struct.pack_into("<II", self._map, OBJECT_COUNT_OFFSET, len(objects),
                         NO_CLASS if spare is None else int(spare))
        struct.pack_into("<Q", self._map, OBJECT_SEQ_OFFSET, publish)
        self.objects = objects
        return publish

    def objects_applied(self, seq) -> bool:
        return self.object_status()["applied_seq"] == seq

    def reserve_class(self, request, name="InstancedMesh", timeout=3.0):
        """Give an empty scene object class the 'skip' flag; returns its id, or None.

        `request(command)` sends a console command to the game. The id is read from
        the change the command makes to the class flag table the game publishes."""
        if self.spare_class is not None:
            return self.spare_class
        if not self.object_status()["class_table"]:
            return None
        before = self.class_flags()
        request(f"sc_setclassflags {name} 8")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            after = self.class_flags()
            changed = [i for i in range(CLASS_SLOTS) if (after[i] & 8) and not (before[i] & 8)]
            if len(changed) == 1:
                self.spare_class = changed[0]
                return changed[0]
            time.sleep(0.05)
        return None


OBJECT_LINE = re.compile(
    r"Object\s+(\d+): (0x[0-9A-Fa-f]+), class=\s*(\S+), "
    r"origin=\(\s*([-\d.e+]+),\s*([-\d.e+]+),\s*([-\d.e+]+)\), "
    r"bounds=\(\s*([-\d.e+]+),\s*([-\d.e+]+),\s*([-\d.e+]+)\):\(\s*([-\d.e+]+),\s*([-\d.e+]+),\s*([-\d.e+]+)\), "
    r"desc=(0x[0-9A-Fa-f]+), flags=(0x[0-9A-Fa-f]+)")
WORLD_LINE = re.compile(r"Scene World\s+(\d+): (0x[0-9A-Fa-f]+)\s+'([^']*)' \[\s*(\d+) objects\]")


def parse_objects(text):
    """Scene objects from `sc_dumpworld` output, as dicts (Source units)."""
    found = []
    for m in OBJECT_LINE.finditer(text):
        v = m.groups()
        found.append({"index": int(v[0]), "address": int(v[1], 16), "class": v[2],
                      "origin": tuple(float(x) for x in v[3:6]),
                      "lo": tuple(float(x) for x in v[6:9]), "hi": tuple(float(x) for x in v[9:12]),
                      "flags": int(v[13], 16)})
    return found


def dump_scene_objects(request, engine_log, world_name="Engine RenderingWorldSession", timeout=15.0):
    """The game world's scene objects via `sc_listworlds` / `sc_dumpworld`; the full
    listing is read back from the engine's console log file."""
    worlds = [m.groups() for m in WORLD_LINE.finditer(request("sc_listworlds"))]
    match = [w for w in worlds if w[2] == world_name]
    if not match:
        return []
    index, _address, _name, count = match[0]
    log = Path(engine_log)
    start = log.stat().st_size if log.is_file() else 0
    request(f"sc_dumpworld {index}")
    deadline = time.monotonic() + timeout
    objects = []
    while time.monotonic() < deadline:
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(start)
                objects = parse_objects(stream.read().decode("utf-8", "replace"))
            if len(objects) >= int(count):
                break
        time.sleep(0.2)
    return objects


def objects_inside(objects, box_lo, box_hi, classes=("Default",), margin=1.0):
    """Scene objects whose bounds lie inside [box_lo, box_hi]."""
    lo = np.asarray(box_lo, dtype=float) - margin
    hi = np.asarray(box_hi, dtype=float) + margin
    return [o for o in objects if o["class"] in classes
            and np.all(np.asarray(o["lo"]) >= lo) and np.all(np.asarray(o["hi"]) <= hi)]


def objects_containing(objects, point, classes=("Default",), margin=1.0):
    """Scene objects whose bounds contain `point`, smallest first."""
    p = np.asarray(point, dtype=float)
    hits = [o for o in objects if o["class"] in classes
            and np.all(np.asarray(o["lo"]) - margin <= p) and np.all(np.asarray(o["hi"]) + margin >= p)]
    return sorted(hits, key=lambda o: float(np.prod(np.maximum(np.subtract(o["hi"], o["lo"]), 1.0))))


def records_inside(lo, hi, box_lo, box_hi, margin=1.0):
    """Indices of records whose box lies entirely inside [box_lo, box_hi] (Source units)."""
    box_lo = np.asarray(box_lo, dtype=np.float32) - margin
    box_hi = np.asarray(box_hi, dtype=np.float32) + margin
    return np.nonzero(np.all(lo >= box_lo, axis=1) & np.all(hi <= box_hi, axis=1))[0]


def records_matching(lo, hi, box_lo, box_hi, tolerance=2.0):
    """Indices of records whose box equals [box_lo, box_hi] within `tolerance` (Source units)."""
    err = np.maximum(np.abs(lo - np.asarray(box_lo)).max(axis=1), np.abs(hi - np.asarray(box_hi)).max(axis=1))
    return np.nonzero(err <= tolerance)[0]


def records_containing(lo, hi, point, exclude=(), margin=1.0):
    """Indices of records whose box contains `point`, smallest box first."""
    p = np.asarray(point, dtype=np.float32)
    inside = np.all(lo - margin <= p, axis=1) & np.all(hi + margin >= p, axis=1)
    if len(exclude):
        inside[np.asarray([i for i in exclude if 0 <= i < len(lo)], dtype=np.int64)] = False
    found = np.nonzero(inside)[0]
    volume = np.prod(np.maximum(hi[found] - lo[found], 1.0), axis=1)
    return found[np.argsort(volume, kind="stable")]
