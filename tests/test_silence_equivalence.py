import copy
import json

import numpy as np
import pytest

from agr.particle_silence_binding import bind_equivalence, validate_equivalence
from agr.particle_silence_inputs import prepare_observed_inputs
from agr.particle_silence_capture import bind_silence_capture
from agr.particle_client_capture import bind_client_child_capture
from agr.particle_observations import read_observations, bind_runtime_observations
from test_particle_silence_capture import source


def poses(path, ticks):
    np.savez(path, ticks=np.array(ticks, dtype='i8'), q=np.tile([0., 0., 0., 1.], (len(ticks), 1, 1)),
        t=np.zeros((len(ticks), 1, 3)), s=np.ones((len(ticks), 1)), fresh=np.ones(len(ticks), dtype=bool))


@pytest.fixture
def scenes(tmp_path, source):
    raw, original, instance, child = source
    original = copy.deepcopy(original)
    a = original['entities'][0]
    a['pose_file'] = str(tmp_path / 'original.npz')
    poses(a['pose_file'], [10, 11, 12])
    a['samples'] = [dict(tick=t, position=[t, 0, 0]) for t in (10, 11, 12)]
    birth = bind_silence_capture(raw, original, instance)
    child = bind_client_child_capture(child, original, instance)
    target = copy.deepcopy(original)
    b = target['entities'][0]
    b['pose_file'] = str(tmp_path / 'target.npz')
    poses(b['pose_file'], [9, 10, 11, 12])
    b['samples'].insert(0, dict(tick=9, position=[9, 0, 0]))
    target['particle_history'] = dict(schema_version=1, start_tick=9)
    target['network_projectiles'] = []
    return original, target, instance, birth, child


def test_added_history_admitted_only_with_explicit_equivalence_and_original_packets(scenes):
    source, target, instance, birth, child = scenes
    before = copy.deepcopy((source, target, birth, child))
    with pytest.raises(ValueError):
        prepare_observed_inputs(target, instance, birth, child)
    proof = bind_equivalence(source, target, instance, birth, child)
    validate_equivalence(proof, target, instance, birth, child)
    value = prepare_observed_inputs(target, instance, birth, child, proof)
    assert value['birth'] == birth and value['child'] == child
    assert proof['source_pose_sha256'] != proof['target_pose_sha256']
    assert proof['consumed_range_inclusive'] == [10, 12]
    assert before == (source, target, birth, child)


@pytest.mark.parametrize('kind', ['pose', 'root', 'constraint', 'dependency', 'basis', 'model', 'skeleton',
    'event', 'child', 'birth', 'unavailable', 'missing_tick', 'duplicate_tick', 'range', 'model_track',
    'secondary', 'target_instance', 'duplicate_instance', 'forged_proof'])
def test_no_rehash_can_admit_a_changed_consumed_input(scenes, kind, tmp_path):
    source, target, instance, birth, child = scenes
    proof = bind_equivalence(source, target, instance, birth, child)
    entity = target['entities'][0]
    if kind == 'pose':
        with np.load(entity['pose_file']) as archive:
            rows = {k:archive[k].copy() for k in archive.files}
        rows['t'][2, 0, 0] = 1
        np.savez(entity['pose_file'], **rows)
    if kind == 'root': entity['samples'][2]['position'][0] += 1
    if kind == 'constraint': entity['model_constraints']['definitions'] = [dict(changed=True)]
    if kind == 'dependency': entity['model_constraints']['preparation_dependencies'] = {'new': 'a'*64}
    if kind == 'basis': entity['render_root_yaw_degrees'] = 90
    if kind in ('model', 'skeleton'):
        field = kind + '_file'; p = tmp_path / ('changed-' + kind); p.write_bytes(b'changed'); entity[field] = str(p)
    if kind == 'event': target['particle_instances'][0]['events'][0]['data']['create_particle']['attach_type'] = 1
    if kind == 'child': child['capture']['graph_nodes'] = 2
    if kind == 'birth': birth['capture']['owner_srt'][0] = 100
    if kind == 'unavailable': entity['pose_unavailable_ticks'] = [11]
    if kind == 'missing_tick': poses(entity['pose_file'], [9, 10, 12])
    if kind == 'duplicate_tick': poses(entity['pose_file'], [9, 10, 10, 11, 12])
    if kind == 'range': target['end_tick'] += 1
    if kind == 'model_track': entity['model_tracks'] = [dict()]
    if kind == 'secondary': entity['secondary_motion_file'] = 'unqualified'
    if kind == 'target_instance': target['particle_instances'] = []
    if kind == 'duplicate_instance': target['particle_instances'] *= 2
    if kind == 'forged_proof': proof['consumed_pose_sha256']['q'] = '0'*64
    with pytest.raises(ValueError):
        validate_equivalence(proof, target, instance, birth, child)


def test_schema_three_import_keeps_original_packets_and_audited_bridge(scenes, tmp_path):
    source, target, instance, birth, child = scenes
    path = tmp_path / 'observations.json'
    path.write_text(json.dumps(dict(format='renderlock-particle-observations', schema_version=3,
        captures=[dict(instance_id=instance['id'], profile='silence', birth=birth, child=child, source_scene=source)])))
    document = read_observations(path)
    path.write_text('replaced')
    runtime, receipt = bind_runtime_observations(document, target)
    assert runtime['silence_birth_captures'][instance['id']] == birth
    assert runtime['client_child_captures'][instance['id']] == child
    validate_equivalence(runtime['silence_input_equivalences'][instance['id']], target, instance, birth, child)
    assert receipt['schema_version'] == 3
