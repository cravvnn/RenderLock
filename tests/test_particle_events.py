import json
import struct
from pathlib import Path

import pytest

from agr.particle_events import (ParticleEventError, compile_particle_timeline,
                                 decode_particle_message, extract_particle_events,
                                 particle_resource_lookup, resolve_particle_instance_resources)
from agr.vendor.resource_hash import resource_id


PREFIX = "GAME_PARTICLE_MANAGER_EVENT_"
PATH = "particles/test/unit_effect.vpcf"
HANDLE = (333 << 14) | 84
ENTITIES = [{"id": "84:333", "entity_index": 84, "entity_serial": 333}]


def vi(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number, value, wire=0):
    if wire == 0:
        value = vi(value)
    elif wire == 2:
        value = vi(len(value)) + value
    return vi(number * 8 + wire) + value


def create(index=4, handle=HANDLE):
    args = (field(1, struct.pack("<Q", resource_id(PATH)), 1)
            + field(2, 5) + field(3, handle))
    return field(1, 0) + field(2, index) + field(4, args, 2)


def record(tick, kind, index=4, **args):
    return {"demo_tick": tick, "data": {"type": PREFIX + kind, "index": index, **args}}


def creation(tick, index=4, handle=HANDLE):
    return record(tick, "CREATE", index, create_particle={
        "particle_name_index": str(resource_id(PATH)), "entity_handle": handle})


def compile_rows(rows, start=10, end=20, entities=ENTITIES):
    return compile_particle_timeline(rows, [PATH + "_c"], start, end,
                                     tick_rate=64, entities=entities)


def packet(messages):
    # Source UBitVar(145): low four bits, selector, then eight high bits.
    bits = []
    def write(value, count):
        bits.extend((value >> i) & 1 for i in range(count))
    for message in messages:
        write((145 & 15) | 32, 6)
        write(145 >> 4, 8)
        for byte in vi(len(message)) + message:
            write(byte, 8)
    packed = bytes(sum((bits[i + j] if i + j < len(bits) else 0) << j for j in range(8))
                   for i in range(0, len(bits), 8))
    return field(3, packed, 2)


def command(kind, tick, data):
    return vi(kind) + vi(tick & 0xFFFFFFFF) + vi(len(data)) + data


def test_wire_decodes_full_resource_handle_and_serial():
    actual = decode_particle_message(create())
    assert actual["type"] == PREFIX + "CREATE"
    assert actual["create_particle"]["particle_name_index"] == str(resource_id(PATH))
    assert actual["create_particle"]["entity_handle"] == HANDLE


def test_wire_decodes_transform_orientation_and_offsets():
    vector = b"".join(field(i, struct.pack("<f", v), 5) for i, v in enumerate((12.5, -20.0, 9.0), 1))
    quaternion = field(4, struct.pack("<f", 1), 5)
    payload = field(1, 27) + field(2, 4) + field(30, field(1, 0) + field(2, vector, 2) + field(3, quaternion, 2), 2)
    value = decode_particle_message(payload)["update_particle_transform"]
    assert value["position"] == {"x": 12.5, "y": -20.0, "z": 9.0}
    assert value["orientation"] == {"w": 1.0}


def test_wire_unknown_event_retained_and_invalid_type_rejected():
    assert decode_particle_message(field(1, 80) + field(99, 42))["_unknown_fields"] == {"99": [42]}
    with pytest.raises(ParticleEventError, match="float32"):
        decode_particle_message(field(30, field(4, 1), 2))
    with pytest.raises(ParticleEventError, match="Duplicate"):
        decode_particle_message(field(2, 1) + field(2, 2))


def test_release_and_same_tick_index_reuse_keep_two_visible_candidates():
    rows = [creation(8), record(10, "RELEASE", release_particle_index={}), creation(10),
            record(11, "UPDATE_ENT", update_particle_ent={"control_point": 0, "entity_handle": HANDLE,
                                                         "attachment": 7, "attach_type": 5})]
    first, second = compile_rows(rows)["instances"]
    assert first["id"] != second["id"]
    assert first["destroy_tick"] is None and first["release_ticks"] == [10]
    assert first["created_frame"] == -1
    assert first["events"][-1]["type"] == "RELEASE"
    assert second["events"][-1]["data"]["update_particle_ent"]["attachment"] == 7


def test_same_entry_different_serial_never_attaches():
    result = compile_rows([creation(10, handle=HANDLE + (1 << 14))])
    assert result["instances"] == []


def test_matching_control_point_selects_otherwise_unowned_effect():
    rows = [creation(5, handle=0xFFFFFF), record(7, "UPDATE_ENT", update_particle_ent={
        "control_point": 0, "entity_handle": HANDLE, "attach_type": 5, "attachment": 3})]
    item = compile_rows(rows)["instances"][0]
    assert item["entity_id"] == "84:333" and item["involved_entity_handles"] == [HANDLE]
    assert item["created_tick"] == 5


def test_immediate_destroy_culls_old_effect_but_natural_tail_is_retained():
    rows = [creation(2), record(3, "DESTROY", destroy_particle={"destroy_immediately": True}),
            creation(4), record(5, "DESTROY", destroy_particle={"destroy_immediately": False})]
    items = compile_rows(rows)["instances"]
    assert len(items) == 1 and items[0]["created_tick"] == 4
    assert items[0]["pre_range_tail_candidate"] is True


def test_destroy_involving_and_named_match_full_handles_and_resource():
    rows = [creation(10), record(11, "DESTROY_INVOLVING", 0, destroy_particle_involving={
        "entity_handle": HANDLE + (1 << 14), "destroy_immediately": True}),
        record(12, "DESTROY_NAMED", 0, destroy_particle_named={
            "entity_handle": HANDLE, "particle_name_index": str(resource_id(PATH)), "destroy_immediately": True})]
    item = compile_rows(rows)["instances"][0]
    assert item["destroy_tick"] == 12
    assert [e["type"] for e in item["events"]] == ["CREATE", "DESTROY_NAMED"]


def test_preserves_update_order_and_rejects_out_of_order_input():
    rows = [creation(10), record(10, "UPDATE_ENT", update_particle_ent={"entity_handle": HANDLE, "attachment": 2}),
            record(10, "UPDATE_ENT", update_particle_ent={"entity_handle": HANDLE, "attachment": 7})]
    assert [e["order"] for e in compile_rows(rows)["instances"][0]["events"]] == [0, 1, 2]
    with pytest.raises(ParticleEventError, match="sequential"):
        compile_rows([creation(11), creation(10)])


def test_inventory_hash_lookup_ignores_nonparticle_paths():
    assert particle_resource_lookup([PATH.upper() + "_c", "models/test.vmdl_c"]) == {str(resource_id(PATH)): PATH}


def test_mounted_vpk_inventory_resolves_recorded_ids_in_production_shape(tmp_path):
    """Regression: the installed provider is a path-to-VPKEntry mapping, not a list."""
    from agr.game_assets import GameAssetProvider, VPKEntry
    from test_game_assets import installation, compiled

    root = installation(tmp_path, {PATH + "_c": compiled(), "models/test.vmdl_c": compiled()})
    provider = GameAssetProvider(root, tmp_path / "cache")
    assert isinstance(provider.resources[PATH + "_c"], VPKEntry)
    timeline = compile_particle_timeline([creation(10)], provider.resources, 10, 20,
                                         tick_rate=64, entities=ENTITIES)
    assert timeline["instances"][0]["resource"] == PATH
    assert timeline["summary"]["resources"] == {PATH: 1}
    assert particle_resource_lookup(provider.resources) == particle_resource_lookup(list(provider.resources))


def test_explicit_resource_hash_maps_are_validated_and_normalized():
    name_hash = resource_id(PATH)
    assert particle_resource_lookup({name_hash: PATH.upper() + "_c"}) == {str(name_hash): PATH}
    assert particle_resource_lookup({str(name_hash): PATH}) == {str(name_hash): PATH}
    with pytest.raises(ParticleEventError, match="does not match"):
        particle_resource_lookup({name_hash + 1: PATH})
    with pytest.raises(ParticleEventError, match="must map"):
        particle_resource_lookup({name_hash: object()})


def test_unresolved_saved_instances_recover_without_losing_original_events():
    event = creation(10)
    source = [{"id": "4:1", "resource": None, "resource_hash": str(resource_id(PATH)),
               "events": [event], "created_tick": 10, "release_ticks": [11]},
              {"id": "5:1", "resource": None, "resource_hash": "123"},
              {"id": "6:1", "resource": "particles/previously_resolved.vpcf"}]
    repaired, report = resolve_particle_instance_resources(source, {PATH + "_c": object()})
    assert repaired[0] == dict(source[0], resource=PATH)
    assert repaired[0]["events"] == [event] and source[0]["resource"] is None
    assert repaired[1:] == source[1:]
    assert report == {"instance_count": 3, "newly_resolved": 1, "resolved_after": 2,
                      "unresolved_after": 1, "unresolved_resource_ids": {"123": 1}}
    repeated, second = resolve_particle_instance_resources(repaired, {PATH + "_c": object()})
    assert repeated == repaired and second["newly_resolved"] == 0


def test_demo_stream_includes_preclip_active_create_and_skips_seek_snapshots(tmp_path):
    snappy = pytest.importorskip("snappy")
    demo = tmp_path / "sample.dem"
    update = field(1, 5) + field(2, 4) + field(12, field(1, 0) + field(2, HANDLE) + field(4, 7), 2)
    demo.write_bytes(b"PBDEMS2\0" + b"\0" * 8
                     + command(8, -1, packet([create()]))
                     + command(13, 8, b"not parsed: seek snapshot")
                     + command(7 | 64, 10, snappy.compress(packet([update])))
                     + command(7, 21, packet([create(index=7)])))
    result = extract_particle_events(demo, 10, 20, entities=ENTITIES,
                                     output_path=tmp_path / "particle_messages.json")
    assert result["particle_messages_through_end"] == 2
    assert [r["demo_tick"] for r in result["records"]] == [-1, 10]
    assert compile_rows(result["records"])["instances"][0]["created_tick"] == -1
    assert json.loads((tmp_path / "particle_messages.json").read_text())["records"] == result["records"]


def test_demo_filter_rejects_stale_serial_and_recovers_later_cp_identity(tmp_path):
    demo = tmp_path / "filter.dem"
    update = field(1, 5) + field(2, 4) + field(12, field(2, HANDLE), 2)
    demo.write_bytes(b"PBDEMS2\0" + b"\0" * 8
                     + command(7, 5, packet([create(handle=0xFFFFFF)]))
                     + command(7, 6, packet([update]))
                     + command(7, 7, packet([create(index=8, handle=HANDLE + (1 << 14))])))
    result = extract_particle_events(demo, 10, 20, entities=ENTITIES)
    assert [r["demo_tick"] for r in result["records"]] == [5, 6]


def test_truncated_demo_does_not_succeed_with_partial_data(tmp_path):
    demo = tmp_path / "truncated.dem"
    demo.write_bytes(b"PBDEMS2\0" + b"\0" * 8 + vi(7) + vi(1) + vi(40) + b"bad")
    with pytest.raises(ParticleEventError, match="Truncated"):
        extract_particle_events(demo, 0, 20)


@pytest.mark.parametrize("tick_rate", [0, -1, True, float("inf"), float("nan")])
def test_invalid_tick_rate_is_not_guessed(tick_rate):
    with pytest.raises(ParticleEventError, match="tick rate"):
        compile_particle_timeline([], [], 0, 1, tick_rate=tick_rate)


def test_authored_total_duration_bound_prunes_without_guessing_release_lifetime():
    from agr.particle_events import prune_expired_particle_instances
    rows = [creation(0), record(1, "RELEASE", release_particle_index={}), creation(9)]
    timeline = compile_rows(rows)
    pruned = prune_expired_particle_instances(timeline, {PATH: 0.02})
    assert len(timeline["instances"]) == 2
    assert len(pruned["instances"]) == 1
    assert pruned["instances"][0]["created_tick"] == 9
    assert len(prune_expired_particle_instances(timeline, {})["instances"]) == 2
    with pytest.raises(ParticleEventError, match="total-duration"):
        prune_expired_particle_instances(timeline, {PATH: float("inf")})
