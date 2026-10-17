import copy
import json
from pathlib import Path

import pytest

from agr.scene_package import SceneError, read_scene, save_scene, slice_scene, validate_scene
from agr.cli import publish_package, animation_mode


def fixture():
    return {"schema_version": 1, "tick_rate": 64, "start_tick": 10, "end_tick": 14,
            "metadata": {}, "entities": [
                {"id": "5:1", "hero_id": 13, "samples": [{"tick": t, "position": [t, 0, 0], "body_angles": [0, 0, 0]} for t in range(10, 12)]},
                {"id": "5:2", "hero_id": 13, "samples": [{"tick": t, "position": [100+t, 0, 0], "body_angles": [0, 180, 0]} for t in range(12, 15)]}],
            "markers": [{"tick": 11, "label": "before"}, {"tick": 12, "label": "spawn"}]}


def test_slice_retains_entity_lifetimes_and_inclusive_end():
    data = fixture()
    clipped = slice_scene(data, 11, 13)
    assert [e["id"] for e in clipped["entities"]] == ["5:1", "5:2"]
    assert [[s["tick"] for s in e["samples"]] for e in clipped["entities"]] == [[11], [12, 13]]
    assert data["start_tick"] == 10
    assert clipped["entities"][1]["observed_intervals"] == [{"start_tick":12,"end_tick_exclusive":15}]
    assert "spawn_tick" not in clipped["entities"][1]
    with pytest.raises(SceneError):
        slice_scene(data, 9, 13)


def test_invalid_tracks_fail_before_blender():
    for change in [lambda d: d["entities"].append(copy.deepcopy(d["entities"][0])),
                   lambda d: d["entities"][0]["samples"].append(d["entities"][0]["samples"][0]),
                   lambda d: d["entities"][0]["samples"][0]["position"].__setitem__(0, float("nan"))]:
        data = fixture(); change(data)
        with pytest.raises(SceneError):
            validate_scene(data)


def test_crop_particle_history_excludes_future_inputs_and_retains_creation_history():
    data = fixture()
    for e, serial in zip(data['entities'], (1, 2)):
        e.update(entity_index=5, entity_serial=serial)
    handle, future_handle = (1 << 14) | 5, (2 << 14) | 5
    events = [
        {'type': 'CREATE', 'tick': 8, 'data': {'create_particle': {'entity_handle': handle}}},
        {'type': 'UPDATE_ENT', 'tick': 12, 'data': {'update_particle_ent': {'entity_handle': future_handle, 'control_point': 1}}},
        {'type': 'RELEASE', 'tick': 13, 'data': {}},
        {'type': 'DESTROY', 'tick': 14, 'data': {}}]
    data['particle_instances'] = [dict(id='99:3', created_tick=8, entity_handle=handle,
        entity_id='5:1', create={'entity_handle': handle}, events=events,
        destroy_tick=14, destroy_immediately=True, release_ticks=[13], index_reused_tick=14),
        dict(id='100:1', created_tick=13, events=[])]
    data['particle_extraction'] = {'parsed_messages': 200}
    original = copy.deepcopy(data)
    result = slice_scene(data, 11, 11)
    assert data == original
    assert len(result['particle_instances']) == 1
    item = result['particle_instances'][0]
    assert item['id'] == '99:3' and item['created_frame'] == -2
    assert [e['tick'] for e in item['events']] == [8]
    assert item['events'][0]['frame'] == -2
    assert item['involved_entity_handles'] == [handle]
    assert item['involved_entity_ids'] == ['5:1']
    assert item['destroy_tick'] is None and item['destroy_immediately'] is None
    assert item['release_ticks'] == [] and 'index_reused_tick' not in item
    assert not item['pre_range_tail_candidate']
    assert 'particle_extraction' not in result
    assert result['source_particle_extraction'] == data['particle_extraction']
    assert result['particle_slice']['retained_events'] == 1


def test_unchanged_range_preserves_recorded_particle_inputs():
    data = fixture()
    data['particle_instances'] = [{'created_tick': 8, 'events': [{'tick': 8, 'frame': -1}]}]
    assert slice_scene(data, None, None)['particle_instances'] == data['particle_instances']


def test_published_package_keeps_pose_files_after_source_removed(tmp_path):
    src = tmp_path/"work"; src.mkdir()
    pose = src/"track.npz"; pose.write_bytes(b"pose bytes")
    skel = src/"skeleton.json"; skel.write_text("{}")
    data = fixture()
    data["entities"][0].update(pose_file=str(pose), skeleton_file=str(skel))
    manifest = save_scene(data, src/"source.json")
    dest = tmp_path/"shot.agrscene"
    publish_package(manifest, dest)
    pose.unlink(); skel.unlink()
    reopened = read_scene(dest)
    assert Path(reopened["entities"][0]["pose_file"]).read_bytes() == b"pose bytes"
    assert not Path(json.loads((dest/"manifest.json").read_text())["entities"][0]["pose_file"]).is_absolute()
    with pytest.raises(ValueError):
        publish_package(manifest, dest)


def test_refuses_to_replace_unrelated_directory(tmp_path):
    src = save_scene(fixture(), tmp_path/"source.json")
    dest = tmp_path/"owned_by_user"; dest.mkdir()
    (dest/"important.txt").write_text("keep")
    with pytest.raises(ValueError):
        publish_package(src, dest, overwrite=True)
    assert (dest/"important.txt").read_text() == "keep"


@pytest.mark.parametrize('winerror',[5,32,33])
def test_package_retries_transient_windows_rename_lock(tmp_path,monkeypatch,winerror):
    import agr.cli as cli
    src=save_scene(fixture(),tmp_path/'source.json')
    dest=tmp_path/'shot.agrscene'
    original=Path.rename
    calls=[]
    def rename(path,target):
        calls.append((path,target))
        if len(calls)==1:
            error=PermissionError('Temporary Windows lock');error.winerror=winerror
            raise error
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    monkeypatch.setattr(cli.time,'sleep',lambda delay:None)
    publish_package(src,dest)
    assert len(calls)==2
    assert read_scene(dest)['entities'][0]['id']=='5:1'


def test_package_persistent_lock_restores_previous_package(tmp_path,monkeypatch):
    import agr.cli as cli
    src=save_scene(fixture(),tmp_path/'source.json')
    dest=tmp_path/'shot.agrscene'
    publish_package(src,dest)
    before=(dest/'manifest.json').read_bytes()
    original=Path.rename; attempts=[]
    def rename(path,target):
        if '-new-' in path.name:
            attempts.append(path)
            error=PermissionError('Persistent Windows lock');error.winerror=5
            raise error
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    monkeypatch.setattr(cli.time,'sleep',lambda delay:None)
    with pytest.raises(PermissionError):publish_package(src,dest,overwrite=True)
    assert len(attempts)==6
    assert (dest/'manifest.json').read_bytes()==before


def test_package_other_rename_errors_are_not_retried(tmp_path,monkeypatch):
    import agr.cli as cli
    def rename(path,target):raise OSError('Not a Windows sharing error')
    def sleep(delay):raise AssertionError('Unexpected retry')
    monkeypatch.setattr(Path,'rename',rename)
    monkeypatch.setattr(cli.time,'sleep',sleep)
    with pytest.raises(OSError,match='Not a Windows'):
        cli._rename_package_path(tmp_path/'a',tmp_path/'b')


def test_partial_package_reuses_available_tracks_without_raw_sidecars():
    data = fixture()
    data["entities"][0].update(pose_file="a.npz", skeleton_file="a.json")
    with pytest.raises(ValueError, match="Saved package"):
        animation_mode(copy.deepcopy(data), saved_package=True)
    assert animation_mode(data, saved_package=True, partial=True) == "cached"
    assert data["entities"][1]["animation_status"] == "unsupported"


def test_normal_import_restores_poses_after_motion_only_export():
    data = fixture()
    for e in data["entities"]:
        e.update(pose_file="a.npz", skeleton_file="a.json")
    assert animation_mode(data, motion_only=True) == "motion"
    assert data["coverage"]["animation"] == "not_requested"
    assert animation_mode(data, saved_package=True) == "cached"
    assert "animation" not in data["coverage"]


def test_slice_coverage_describes_current_selection():
    data = fixture()
    data["entities"][0].update(pose_file="a.npz", skeleton_file="a.json")
    data["entities"][1].update(pose_file="b.npz", skeleton_file="b.json")
    data["coverage"] = {"skeletal_animation": {"evaluated_entities": 2}}
    clipped = slice_scene(data, 10, 11)
    assert len(clipped["entities"]) == 1
    assert clipped["coverage"]["skeletal_animation"]["evaluated_entities"] == 1
    assert clipped["source_coverage"]["skeletal_animation"]["evaluated_entities"] == 2
