"""Hide static map pieces in the running game (the "<session>.mapedit" mapping).

Layout matches native/include/dolly_map_edit.hpp. The game's world renderer keeps
the world bounding box of every aggregate fragment (static props and merged map
geometry) in one table that its GPU culling reads for every view; the native
side publishes that table here and moves the boxes of the records listed in
the hide list out of the world, so the game stops drawing those pieces. Baked
lighting (shadows the pieces cast) stays as it was.

Records are indexed in the game's own order, which is fixed for a map build;
`records_hash` identifies the build. Boxes are in Source units.
"""
from __future__ import annotations

import mmap
import struct

import numpy as np

MAGIC = b"DLYMED01"
VERSION = 1
IDS_OFFSET = 4096
ID_CAPACITY = 65536
RECORDS_OFFSET = IDS_OFFSET + 4 * ID_CAPACITY
RECORD_BYTES = 32
MAX_RECORDS = 262144
SIZE = RECORDS_OFFSET + RECORD_BYTES * MAX_RECORDS
HEADER = struct.Struct("<8sIIQIIQIIQQIIQ")
HIDE_HASH_OFFSET = 72
HIDE_SEQ_OFFSET = 16
HIDE_COUNT_OFFSET = 24
STATUS = struct.Struct("<QIIQQI")  # at offset 32
STATUS_OFFSET = 32


def mapping_name(token: str) -> str:
    return "Local\\DeadlockDollyNative_" + token + ".mapedit"


class MapEdit:
    def __init__(self, token: str):
        self._map = mmap.mmap(-1, SIZE, tagname=mapping_name(token), access=mmap.ACCESS_WRITE)
        self._map[:IDS_OFFSET] = b"\0" * IDS_OFFSET
        self._map[:HEADER.size] = HEADER.pack(MAGIC, VERSION, ID_CAPACITY, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.hidden: list[int] = []
        self.synced = False      # the scene's saved list was sent (Blender side)
        self._records = None
        self._records_serial = 0

    def close(self):
        if self._map is not None:
            self._map.close()
            self._map = None

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
        """Replace the hide list (record indices); an empty list shows the stock map.

        `table_hash` (hex, as status()["hash"]) names the table the indices belong to;
        it defaults to the table the game shows now. The game ignores a list made
        for another table."""
        ids = sorted({int(i) for i in ids if 0 <= int(i) < 2**32})[:ID_CAPACITY]
        seq = struct.unpack_from("<Q", self._map, HIDE_SEQ_OFFSET)[0]
        seq = seq + 1 if seq % 2 == 0 else seq + 2          # odd: writing
        struct.pack_into("<Q", self._map, HIDE_SEQ_OFFSET, seq)
        if ids:
            self._map[IDS_OFFSET:IDS_OFFSET + 4 * len(ids)] = np.asarray(ids, dtype=np.uint32).tobytes()
        struct.pack_into("<I", self._map, HIDE_COUNT_OFFSET, len(ids))
        digest = table_hash if table_hash is not None else self.status()["hash"]
        struct.pack_into("<Q", self._map, HIDE_HASH_OFFSET, int(digest, 16))
        struct.pack_into("<Q", self._map, HIDE_SEQ_OFFSET, seq + 1)
        self.hidden = ids
        return seq + 1

    def applied(self, seq) -> bool:
        return self.status()["applied_seq"] == seq


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
