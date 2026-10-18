"""Replay boundary contracts; no proprietary replay or game assets required."""
import pytest

from agr.replay import (ReplayError, _check_demo, _entities_from_raw, _position,
                        extract_replay, hero_display_name, inspect_replay)


PREFIX = "CBodyComponent.m_skeletonInstance."


def fields():
    result = {PREFIX + "m_vecOrigin.m_cell" + axis: 64 for axis in "XYZ"}
    result.update({PREFIX + "m_vecOrigin.m_vec" + axis: 0 for axis in "XYZ"})
    result[PREFIX + "m_angRotation"] = [0, 90, 0]
    result["m_angEyeAngles"] = [20, 270, 0]
    result["m_lifeState"] = 0
    result["m_iHealth"] = 100
    return result


def row(tick, serial=2, hero=13):
    return {"demo_tick": tick, "hero_id": hero, "entity_id": f"7:{serial}",
            "entity_index": 7, "entity_serial": serial, "fields": fields(),
            "serialized_pose_recipe_array": [1, 0, 0, 0], "active": True}


def test_network_render_state_preserves_changes_and_absence():
    values=[row(1),row(2),row(3)]
    values[0]['fields'].update(m_clrRender=0xffffffff,m_nRenderMode=0)
    values[1]['fields'].update(m_clrRender=0x80402010,m_nRenderMode=1)
    entities,_=_entities_from_raw(values,1,3,None)
    samples=entities[0]['samples']
    assert samples[0]['network_render_state']=={'rgba8_packed':0xffffffff,'mode':0}
    assert samples[1]['network_render_state']=={'rgba8_packed':0x80402010,'mode':1}
    assert 'network_render_state' not in samples[2]


@pytest.mark.parametrize('field',['m_clrRender','m_nRenderMode'])
@pytest.mark.parametrize('value',[-1,0x100000000,True,1.5,None])
def test_invalid_network_render_state_rejected(field,value):
    sample=row(1);sample['fields'][field]=value
    with pytest.raises(ReplayError,match='Invalid recorded'):
        _entities_from_raw([sample],1,1,None)


def test_cell_boundary_reconstructs_continuous_world_coordinates():
    before = fields()
    before[PREFIX + "m_vecOrigin.m_vecX"] = 511
    after = fields()
    after[PREFIX + "m_vecOrigin.m_cellX"] = 65
    assert _position(before) == [511, 0, 0]
    assert _position(after) == [512, 0, 0]


def test_current_deadlock_world_origin_on_all_axes():
    value = fields()
    value[PREFIX + "m_vecOrigin.m_cellY"] = 63
    value[PREFIX + "m_vecOrigin.m_vecY"] = 511
    value[PREFIX + "m_vecOrigin.m_cellZ"] = 65
    value[PREFIX + "m_vecOrigin.m_vecZ"] = 128
    assert _position(value) == [0, -1, 640]


def test_missing_cell_is_not_silently_zero_filled():
    value = fields()
    del value[PREFIX + "m_vecOrigin.m_cellY"]
    assert _position(value) is None


def test_slot_reuse_creates_distinct_tracks_and_preserves_body_angles():
    entities, _ = _entities_from_raw([row(1), row(2), row(3, serial=3)], 1, 3, None)
    assert [(e["id"], len(e["samples"])) for e in entities] == [("7:2", 2), ("7:3", 1)]
    assert entities[0]["samples"][0]["body_angles"] == [0, 90, 0]
    assert entities[0]["samples"][0]["eye_angles"] == [20, 270, 0]
    assert entities[0]["observed_intervals"] == [{"start_tick": 1, "end_tick_exclusive": 3}]
    assert entities[1]["observed_intervals"] == [{"start_tick": 3, "end_tick_exclusive": 4}]


def test_range_is_inclusive_and_warmup_is_not_a_movement_sample():
    rows = [row(0), row(1), row(2), row(3)]
    rows[0]["warmup"] = True
    entities, _ = _entities_from_raw(rows, 1, 2, {13})
    assert [sample["tick"] for sample in entities[0]["samples"]] == [1, 2]


def test_duplicate_completed_tick_is_rejected():
    with pytest.raises(ReplayError, match="Duplicate"):
        _entities_from_raw([row(1), row(1)], 1, 1, None)


def test_requested_missing_hero_is_rejected():
    with pytest.raises(ReplayError, match="absent"):
        _entities_from_raw([row(1)], 1, 1, {13, 31})


def test_hero_change_cannot_merge_in_one_lifetime():
    with pytest.raises(ReplayError, match="Hero changed"):
        _entities_from_raw([row(1), row(2, hero=31)], 1, 2, None)


def test_dormancy_and_sample_gaps_are_exposed():
    values = [row(1), row(3)]
    values[1]["active"] = False
    entities, coverage = _entities_from_raw(values, 1, 3, None)
    assert entities[0]["samples"][1]["active"] is False
    assert entities[0]["samples"][1]["present"] is False
    assert coverage["entities"]["7:2"]["interior_missing_ticks"] == 1
    assert coverage["entities"]["7:2"]["dormant_samples"] == 1
    assert entities[0]["observed_intervals"] == [
        {"start_tick": 1, "end_tick_exclusive": 2}, {"start_tick": 3, "end_tick_exclusive": 4}]


def test_disappearing_lifetime_does_not_extend_to_clip_end_or_new_serial():
    values = [row(1), row(2), row(5, serial=3), row(6, serial=3)]
    entities, _ = _entities_from_raw(values, 0, 10, None)
    old, new = entities
    assert old["observed_intervals"] == [{"start_tick": 1, "end_tick_exclusive": 3}]
    assert new["observed_intervals"] == [{"start_tick": 5, "end_tick_exclusive": 7}]
    assert "spawn_tick" not in old and "despawn_tick" not in old


def test_invalid_transform_gap_hides_unsupported_interval_without_claiming_deletion():
    values = [row(1), row(2), row(3)]
    del values[1]["fields"][PREFIX + "m_vecOrigin.m_cellX"]
    entities, coverage = _entities_from_raw(values, 0, 5, None)
    entity = entities[0]
    assert entity["observed_intervals"] == [
        {"start_tick": 1, "end_tick_exclusive": 2}, {"start_tick": 3, "end_tick_exclusive": 4}]
    assert coverage["entities"][entity["id"]]["invalid_transform_samples_skipped"] == 1
    assert "despawn_tick" not in entity
    assert "not asserted" in coverage["visibility_basis"]


def test_death_alone_does_not_remove_observed_body():
    values = [row(1), row(2)]
    values[1]["fields"]["m_lifeState"] = 2
    values[1]["fields"]["m_iHealth"] = 0
    entities, _ = _entities_from_raw(values, 1, 2, None)
    entity = entities[0]
    assert entity["samples"][1]["alive"] is False
    assert entity["samples"][1]["present"] is True
    assert entity["observed_intervals"] == [{"start_tick": 1, "end_tick_exclusive": 3}]


@pytest.mark.parametrize("start,end,heroes", [
    (2, 1, None), (-1, 1, None), (False, 1, None), (0, 1.5, None),
    (0, 1, []), (0, 1, [True]), (0, 1, [-2]), (0, 2**31 - 1, None),
])
def test_invalid_requests_fail_before_replay_or_native_access(tmp_path, start, end, heroes):
    with pytest.raises(ReplayError):
        extract_replay(tmp_path / "missing.dem", tmp_path / "output", start, end, heroes)
    assert not (tmp_path / "output").exists()


def test_bad_demo_header_is_rejected(tmp_path):
    demo = tmp_path / "wrong.dem"
    demo.write_bytes(b"not a demo at all")
    with pytest.raises(ReplayError, match="PBDEMS2"):
        _check_demo(demo)


def _inspect_fixture(tmp_path, monkeypatch, players, error=None):
    from types import SimpleNamespace
    import agr.replay as replay

    demo = tmp_path / "owned.dem"
    demo.write_bytes(b"PBDEMS2\0" + b"\0" * 8)
    calls = []

    class Frame:
        def to_dicts(self):
            if error is not None:
                raise error
            return players

    class Demo:
        build = 10932
        map_name = "start"
        match_id = 1
        tick_rate = 64
        total_ticks = 2
        total_seconds = 2 / 64

        def __init__(self, path, *, preload):
            calls.append((path, preload))
            assert preload is False

        @property
        def players(self):
            return Frame()

        def load(self, *names):
            pytest.fail("Character inspection must not load per-tick/event datasets")

    def forbidden_catalog(*args, **kwargs):
        pytest.fail("Character inspection must not request a downloadable name catalog")

    boon = SimpleNamespace(Demo=Demo, hero_names=forbidden_catalog)
    monkeypatch.setattr(replay, "_dependencies", lambda: (boon, None))
    return demo, calls


def test_inspect_groups_recorded_heroes_offline_without_account_identity(tmp_path, monkeypatch):
    demo, calls = _inspect_fixture(tmp_path, monkeypatch, [
        {"hero_id": 999, "player_name": "Private A", "steam_id": 123},
        {"hero_id": 2, "player_name": "Private B", "steam_id": 456},
        {"hero_id": 999, "player_name": "Private C", "steam_id": 789},
    ])
    metadata = inspect_replay(demo)
    assert calls == [(str(demo.resolve()), False)]
    assert metadata == {
        "build": 10932, "map_name": "start", "match_id": 1, "tick_rate": 64,
        "total_ticks": 2, "total_seconds": 2 / 64,
        "heroes": [{"hero_id": 2, "hero_name": "Seven", "player_count": 1},
                   {"hero_id": 999, "hero_name": "Hero 999", "player_count": 2}],
        "roster_source": "recorded_players",
    }


def test_inspect_invalid_roster_ids_are_not_silently_selectable(tmp_path, monkeypatch):
    demo, _ = _inspect_fixture(tmp_path, monkeypatch, [
        {"hero_id": 13}, {"hero_id": True}, {"hero_id": "13"}, {"hero_id": 1.5},
        {"hero_id": 0}, {"hero_id": -1}, {}, None,
    ])
    metadata = inspect_replay(demo)
    assert metadata["heroes"] == [{"hero_id": 13, "hero_name": "Haze", "player_count": 1}]
    assert metadata["roster_source"] == "recorded_players"
    assert "7 recorded player row(s)" in metadata["roster_warning"]
    assert "may be incomplete" in metadata["roster_warning"]


def test_inspect_empty_roster_preserves_metadata_with_honest_warning(tmp_path, monkeypatch):
    demo, _ = _inspect_fixture(tmp_path, monkeypatch, [])
    metadata = inspect_replay(demo)
    assert metadata["total_ticks"] == 2
    assert metadata["heroes"] == []
    assert "No recorded hero IDs" in metadata["roster_warning"]


def test_inspect_failed_roster_preserves_metadata_without_private_error_details(tmp_path, monkeypatch):
    demo, _ = _inspect_fixture(tmp_path, monkeypatch, [], RuntimeError("private player/account data"))
    metadata = inspect_replay(demo)
    assert metadata["total_ticks"] == 2
    assert metadata["heroes"] == []
    assert metadata["roster_source"] == "unavailable"
    assert "RuntimeError" in metadata["roster_warning"]
    assert "private" not in str(metadata)


@pytest.mark.parametrize("hero_id,name", [(2, "Seven"), (76, "Graves"), (999, "Hero 999")])
def test_hero_display_names_are_cosmetic_offline_labels(hero_id, name):
    assert hero_display_name(hero_id) == name


def test_extraction_kill_markers_do_not_request_downloadable_name_catalog(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import agr.replay as replay
    demo = tmp_path / 'owned.dem'
    demo.write_bytes(b'PBDEMS2\0' + b'\0' * 8)
    helper = tmp_path / 'agr-extract.exe'
    helper.write_bytes(b'placeholder')
    class Frame:
        def __init__(self, rows): self.rows = rows
        def filter(self, expression): return self
        def to_dicts(self): return self.rows
    class Demo:
        build=10932; map_name='start'; match_id=1; tick_rate=64; total_ticks=2; total_seconds=2/64
        def __init__(self, path): pass
        def load(self, *names):
            self.kills=Frame([{'tick':1,'victim_hero_id':13}])
            self.abilities=Frame([])
    def forbidden_catalog():
        pytest.fail('Replay extraction must not request a name catalog')
    boon = SimpleNamespace(Demo=Demo, hero_names=forbidden_catalog)
    pl = SimpleNamespace(col=lambda name:SimpleNamespace(is_between=lambda a,b:None))
    monkeypatch.setattr(replay, '_dependencies', lambda:(boon,pl))
    monkeypatch.setattr(replay, 'package_version', lambda name:'0.15.0')
    monkeypatch.setattr(replay, 'find_native_extractor', lambda:helper)
    def native(command, **kwargs):
        from pathlib import Path
        destination=Path(command[2])
        for name,data in [('raw_animation_state.json',[row(1)]),
                          ('network_ticks.json',[{'demo_tick':1,'server_tick':1}]),
                          ('native_coverage.json',{'source_world_half':32768,'boon_version':'0.15.0'})]:
            (destination/name).write_text(json.dumps(data))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(replay.subprocess,'run',native)
    segment=extract_replay(demo,tmp_path/'output',1,1)
    result=json.loads(segment.read_text())
    assert result['markers'][0]['label']=='Hero 13 death'
    assert result['coverage']['markers']['kills']==1
    assert not result['coverage']['warnings']
