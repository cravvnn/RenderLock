import copy
import hashlib
import json

import pytest

from agr import particle_observations as transport
from agr.particle_silence_capture import bind_silence_capture
from agr.particle_client_capture import bind_client_child_capture
from test_particle_silence_capture import source
from test_particle_crossbow_owner import observed


def document(tmp_path, source):
    raw, scene, instance, child = source
    birth = bind_silence_capture(raw, scene, instance)
    child = bind_client_child_capture(child, scene, instance)
    value = dict(format=transport.FORMAT, schema_version=2, captures=[
        dict(profile='silence', instance_id=instance['id'], birth=birth, child=child)])
    path = tmp_path / 'observations.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value, scene


def test_import_preserves_both_original_envelopes_after_file_replacement(tmp_path, source):
    path, value, scene = document(tmp_path, source)
    raw = path.read_bytes()
    snapshot = transport.read_observations(path)
    path.write_text('replaced after read')
    runtime, receipt = transport.bind_runtime_observations(snapshot, scene)
    key = value['captures'][0]['instance_id']
    assert runtime['silence_birth_captures'][key] == value['captures'][0]['birth']
    assert runtime['client_child_captures'][key] == value['captures'][0]['child']
    assert receipt['sha256'] == hashlib.sha256(raw).hexdigest()
    runtime['silence_birth_captures'][key]['capture']['owner_srt'][0] = 900
    assert snapshot['captures'][key]['birth']['capture']['owner_srt'][0] == 1


@pytest.mark.parametrize('change', [
    lambda d: d['captures'][0].pop('instance_id'),
    lambda d: d['captures'][0].pop('birth'),
    lambda d: d['captures'][0].pop('child'),
    lambda d: d['captures'][0].update(profile='crossbow'),
    lambda d: d['captures'][0].update(extra=0),
    lambda d: d.update(schema_version=3),
])
def test_schema_two_is_narrow_and_complete(tmp_path, source, change):
    path, value, _ = document(tmp_path, source)
    change(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        transport.read_observations(path)


@pytest.mark.parametrize('kind', ['history', 'motion', 'birth', 'child', 'missing', 'duplicate'])
def test_changed_scene_is_refused_without_rebinding_any_packet(tmp_path, source, kind):
    path, _, scene = document(tmp_path, source)
    snapshot = transport.read_observations(path)
    before = copy.deepcopy(snapshot)
    key = next(iter(snapshot['captures']))
    if kind == 'history':
        scene['particle_history'] = dict(start_tick=9)
    if kind == 'motion':
        scene['entities'][0]['samples'] = [dict(tick=10, position=[1, 0, 0])]
    if kind in ('birth', 'child'):
        snapshot['captures'][key][kind]['content_sha256'] = '0'*64
    if kind == 'missing':
        scene['particle_instances'] = []
    if kind == 'duplicate':
        scene['particle_instances'] *= 2
    bad = copy.deepcopy(snapshot)
    with pytest.raises(ValueError):
        transport.bind_runtime_observations(snapshot, scene)
    assert snapshot == bad
    if kind not in ('birth', 'child'):
        assert snapshot == before


def test_schema_one_runtime_adapter_preserves_existing_binding(observed, tmp_path):
    from test_particle_observations import write_document
    scene, instance, capture = observed
    path, _ = write_document(tmp_path, instance, capture)
    snapshot = transport.read_observations(path)
    old, receipt = transport.bind_observations(snapshot, scene)
    runtime, actual = transport.bind_runtime_observations(snapshot, scene)
    assert runtime == dict(crossbow_owner_captures=old) and actual == receipt
