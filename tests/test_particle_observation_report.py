import copy
import json

import pytest

from agr import cli
from agr import particle_observation_report as producer
from agr.particle_observations import read_observations, bind_observations
from agr.particle_snapshot_provider import digest
from test_particle_crossbow_owner import observed


@pytest.fixture
def capture_report(observed):
    scene, instance, capture = observed
    body = {k: copy.deepcopy(capture[k]) for k in ('coherent', 'identity_flags', 'skeleton_vtable_rva',
        'body_component_type_index', 'body_component_offset', 'owner_srt')}
    body.update(model=capture['model'].removesuffix('_c'), method=capture['model_method'])
    actor = {k: capture[k] for k in ('graph_coherence_policy', 'graph_coherent',
        'child_model_inputs_complete', 'eligible_render_children')}
    actor.update(handle=capture['runtime_owner_handle'], entity_index=3, body_model=body, nodes=[{'n': 1}, {'n': 2}],
        effects=[dict(resource=dict(name=capture['resource']), raw=capture['cp0_record'],
            bindings=[dict(cp=0, mode=1, owner=capture['runtime_owner_handle'],
                collection_owner=capture['collection_owner_handle'], collection_record_raw=capture['collection_record'])])])
    sample = dict(tick=10, client_sha256=capture['client_sha256'], particles_sha256=capture['particles_sha256'],
                  actors=[actor], runtime_axes=dict(rvas=capture['runtime_axes_rvas'], raw=capture['runtime_axes_raw'], coherent=True))
    report = dict(phase='closed', workflow_passed=True, exit_code=0, cleanup_errors=[],
        pending_runtime_restoration={}, config_unchanged_after_cleanup=True, retained_baseline_unchanged=True,
        source_unchanged_after_run=True, product_unchanged_after_run=True,
        replay_sha256=capture['demo_sha256'], seeks=[dict(target=10, after=10, cp_owner_capture=sample)])
    return scene, instance, capture, report


def test_normalization_roundtrip_uses_actual_slot_not_first_record(tmp_path, capture_report):
    scene, instance, capture, report = capture_report
    actor = report['seeks'][0]['cp_owner_capture']['actors'][0]
    effect = actor['effects'][0]
    effect['raw'] = '00' * 88 + effect['raw']
    effect['bindings'].insert(0, dict(cp=7))
    document = producer.normalize_report(report, scene, [instance['id']], 'b' * 64)
    expected = dict(capture, graph_sha256=digest(actor['nodes']))
    assert document['captures'][0]['capture'] == expected
    path = tmp_path / 'prepared.json'
    path.write_text(json.dumps(document))
    bound, _ = bind_observations(read_observations(path), scene)
    assert bound[instance['id']]['capture'] == expected
    assert effect['bindings'][0] == {'cp': 7}


@pytest.mark.parametrize('change', [
    lambda r: r.update(phase='running'),
    lambda r: r.update(exit_code=False),
    lambda r: r.update(watchdog_fired=True),
    lambda r: r.update(source_unchanged_after_run=False),
    lambda r: r.update(pending_runtime_restoration={'owned': True}),
    lambda r: r.update(replay_sha256='e' * 64),
    lambda r: r['seeks'][0].update(after=11),
    lambda r: r['seeks'][0]['cp_owner_capture'].update(tick=11),
    lambda r: r['seeks'].append(copy.deepcopy(r['seeks'][0])),
    lambda r: r['seeks'][0]['cp_owner_capture']['actors'].append(copy.deepcopy(r['seeks'][0]['cp_owner_capture']['actors'][0])),
    lambda r: r['seeks'][0]['cp_owner_capture']['actors'][0]['effects'].append(copy.deepcopy(r['seeks'][0]['cp_owner_capture']['actors'][0]['effects'][0])),
    lambda r: r['seeks'][0]['cp_owner_capture']['actors'][0]['body_model'].update(model='models/wrong.vmdl'),
    lambda r: r['seeks'][0]['cp_owner_capture']['actors'][0]['body_model'].update(identity_flags=1),
    lambda r: r['seeks'][0]['cp_owner_capture']['actors'][0]['effects'][0]['bindings'][0].update(mode=3),
    lambda r: r['seeks'][0]['cp_owner_capture']['actors'][0]['effects'][0].update(raw=''),
])
def test_changed_report_never_normalizes(capture_report, change):
    scene, instance, _, report = capture_report
    change(report)
    with pytest.raises(ValueError):
        producer.normalize_report(report, scene, [instance['id']], 'b' * 64)


def test_all_requested_instances_and_ambiguous_births_refuse(capture_report):
    scene, instance, _, report = capture_report
    for ids in ([], [instance['id']] * 2, [instance['id'], 'missing']):
        with pytest.raises(ValueError):
            producer.normalize_report(report, scene, ids, 'b' * 64)
    scene['particle_instances'].append(dict(instance, id='9:1', index=9))
    with pytest.raises(ValueError, match='simultaneous'):
        producer.normalize_report(report, scene, [instance['id']], 'b' * 64)


def test_cli_prepares_json_to_stdout_without_files_or_live_code(tmp_path, capture_report, capsys):
    from agr.scene_package import save_scene
    scene, instance, _, report = capture_report
    scene['schema_version'] = 1
    for sample in scene['entities'][0]['samples']:
        sample['body_angles'] = [0, 0, 0]
    source = tmp_path / 'report.json'; source.write_text(json.dumps(report))
    segment = save_scene(scene, tmp_path / 'segment.json')
    before = source.read_bytes()
    assert cli.main(['prepare-particle-observations', '--capture-report', str(source),
                     '--segment', str(segment), '--instance', instance['id']]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document['captures'][0]['instance_id'] == instance['id']
    assert source.read_bytes() == before
    path = tmp_path / 'import.json'; path.write_text(json.dumps(document))
    bind_observations(read_observations(path), scene)


@pytest.mark.parametrize('content', [b'{}', b'{"n":NaN}', b'{"n":1,"n":2}', b'\xff'],
                         ids=['missing-fields', 'nonfinite', 'duplicate', 'utf8'])
def test_bad_report_input(tmp_path, content):
    path = tmp_path / 'report.json'; path.write_bytes(content)
    with pytest.raises(ValueError):
        report, sha = producer.read_report(path)
        producer.normalize_report(report, {}, ['1:1'], sha)
