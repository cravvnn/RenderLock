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

DUMP = (
    "11/22 00:04:35 [SceneSystem] Object  301: 0x00000220E22A4410, class=             Default, "
    "origin=(         -904,         1648,          560), bounds=(         -918,         1613,          521):"
    "(         -890,         1648,          599), desc=0x00007FF98ADF7CC0, flags=0x2010609100400001\n"
    "11/22 00:04:35 [SceneSystem] Object 1746: 0x00000221288F3000, class=    LightProbeVolume, "
    "origin=(         -904,         1304,          576), bounds=(        -1105,         1024,          384):"
    "(         -704,         1584,          768), desc=0x00007FF98ADF9818, flags=0x2000000000400000\n")


@pytest.fixture
def pair():
    token = uuid.uuid4().hex
    editor = map_edit.MapEdit(token)
    native = mmap.mmap(-1, map_edit.SIZE, tagname=map_edit.mapping_name(token), access=mmap.ACCESS_WRITE)
    yield editor, native
    native.close()
    editor.close()


def test_fragments(pair):
    editor, native = pair
    assert struct.unpack_from("<I", native, 8)[0] == map_edit.VERSION
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
    assert np.frombuffer(native, np.uint32, count, map_edit.IDS_OFFSET).tolist() == [0, 2]
    assert struct.unpack_from("<Q", native, map_edit.HIDE_HASH_OFFSET)[0] == 0xABCDEF
    assert not editor.applied(seq)
    struct.pack_into("<Q", native, 32, seq)         # native applied it
    assert editor.applied(seq)
    assert editor.set_hidden([]) == seq + 2

    assert map_edit.records_inside(lo, hi, [-1, -1, -1], [30, 30, 30]).tolist() == [0, 1]
    assert map_edit.records_containing(lo, hi, [2, 2, 2]).tolist() == [0, 2]
    assert map_edit.records_containing(lo, hi, [2, 2, 2], exclude=[0]).tolist() == [2]
    assert map_edit.records_matching(lo, hi, [10, 10, 10], [20, 20, 20]).tolist() == [1]


def test_objects(pair):
    editor, native = pair
    assert struct.unpack_from("<I", native, 92)[0] == map_edit.NO_CLASS
    # No class table yet: nothing can be reserved.
    assert editor.reserve_class(lambda command: "") is None

    struct.pack_into("<I", native, 108, 1)          # class table found
    sent = []

    def request(command):
        sent.append(command)
        native[map_edit.CLASS_FLAGS_OFFSET + 11] = 8  # the game flags class 11
        return ""

    assert editor.reserve_class(request) == 11
    assert sent == ["sc_setclassflags InstancedMesh 8"]
    assert editor.reserve_class(request) == 11 and len(sent) == 1

    seq = editor.set_objects([(0x220E22A4410, (-904, 1648, 560))])
    assert seq % 2 == 0
    count, spare = struct.unpack_from("<II", native, 88)
    assert (count, spare) == (1, 11)
    address, x, y, z, _ = map_edit.OBJECT.unpack_from(native, map_edit.OBJECTS_OFFSET)
    assert address == 0x220E22A4410 and (x, y, z) == (-904, 1648, 560)
    struct.pack_into("<QII", native, 96, seq, 1, 1)
    assert editor.objects_applied(seq) and editor.object_status()["hidden"] == 1


def test_scene_dump_parsing():
    objects = map_edit.parse_objects(DUMP)
    assert [o["index"] for o in objects] == [301, 1746]
    lamp = objects[0]
    assert lamp["address"] == 0x220E22A4410 and lamp["class"] == "Default"
    assert lamp["origin"] == (-904, 1648, 560) and lamp["hi"] == (-890, 1648, 599)
    assert map_edit.objects_inside(objects, (-1000, 1500, 500), (-800, 1700, 700)) == [lamp]
    assert map_edit.objects_containing(objects, (-904, 1630, 560)) == [lamp]
    worlds = map_edit.WORLD_LINE.findall(
        "Scene World   8: 0x0000041411174980           'Engine RenderingWorldSession' [6771 objects] (dump)")
    assert worlds == [("8", "0x0000041411174980", "Engine RenderingWorldSession", "6771")]


def test_class_already_flagged(pair):
    editor, native = pair
    struct.pack_into("<I", native, 108, 1)
    native[map_edit.CLASS_FLAGS_OFFSET + 5] = 8      # left flagged by an earlier session
    sent = []

    def request(command):
        sent.append(command)
        native[map_edit.CLASS_FLAGS_OFFSET + 5] = 8 if command.endswith(" 8") else 0
        return ""

    assert editor.reserve_class(request, timeout=0.3) == 5
    assert sent == ["sc_setclassflags InstancedMesh 8", "sc_setclassflags InstancedMesh 0",
                    "sc_setclassflags InstancedMesh 8"]


def test_object_identity():
    lamp, world = map_edit.parse_objects(DUMP)[0], dict(map_edit.parse_objects(DUMP)[0])
    world.update(origin=(0.0, 0.0, 0.0), lo=(-5000.0, -5000.0, -100.0), hi=(5000.0, 5000.0, 900.0))
    assert map_edit.identifies(lamp) and not map_edit.identifies(world)
    assert map_edit.objects_containing([lamp, world], (-904, 1630, 560)) == [lamp]
    key = {"origin": [-904.0, 1648.0, 560.0], "lo": [-918.0, 1613.0, 521.0], "hi": [-890.0, 1648.0, 599.0]}
    assert map_edit.same_object(lamp, key)
    assert not map_edit.same_object(lamp, dict(key, hi=[-890.0, 1648.0, 640.0]))
