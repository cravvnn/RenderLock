"""The "<session>.mapedit" protocol from the controller side, with the native side simulated."""
import mmap
import struct
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "game_render"))
import map_edit  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named mappings are Windows-only")


def test_round_trip():
    token = uuid.uuid4().hex
    editor = map_edit.MapEdit(token)
    native = mmap.mmap(-1, map_edit.SIZE, tagname=map_edit.mapping_name(token), access=mmap.ACCESS_WRITE)
    try:
        assert editor.records() is None
        table = np.zeros((3, 8), np.float32)
        table[:, 0:3] = [[0, 0, 0], [10, 10, 10], [0, 0, 0]]
        table[:, 4:7] = [[5, 5, 5], [20, 20, 20], [100, 100, 100]]
        native[map_edit.RECORDS_OFFSET:map_edit.RECORDS_OFFSET + table.nbytes] = table.tobytes()
        struct.pack_into("<I", native, 40, 3)          # record_count
        struct.pack_into("<Q", native, 56, 0xABCDEF)   # records_hash
        struct.pack_into("<Q", native, 48, 1)          # records_serial
        lo, hi = editor.records()
        assert lo.shape == (3, 3) and hi[1].tolist() == [20, 20, 20]
        assert editor.status()["hash"] == "0000000000abcdef"

        seq = editor.set_hidden([2, 0, 2])
        assert seq % 2 == 0
        count = struct.unpack_from("<I", native, map_edit.HIDE_COUNT_OFFSET)[0]
        ids = np.frombuffer(native, np.uint32, count, map_edit.IDS_OFFSET).tolist()
        assert ids == [0, 2]
        assert not editor.applied(seq)
        struct.pack_into("<Q", native, 32, seq)         # native applied it
        assert editor.applied(seq)
        assert editor.set_hidden([]) == seq + 2

        assert map_edit.records_inside(lo, hi, [-1, -1, -1], [30, 30, 30]).tolist() == [0, 1]
        assert map_edit.records_containing(lo, hi, [2, 2, 2]).tolist() == [0, 2]
        assert map_edit.records_containing(lo, hi, [2, 2, 2], exclude=[0]).tolist() == [2]
        assert map_edit.records_matching(lo, hi, [10, 10, 10], [20, 20, 20]).tolist() == [1]
    finally:
        native.close()
        editor.close()
