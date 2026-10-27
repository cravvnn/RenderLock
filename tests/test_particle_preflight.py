"""Early checks must never promise particles or discard a possible binding."""
import copy
import json
import pytest

from agr import cli
from agr.export_recovery import recovery_choices
from agr.recorded_effect_simulation import preflight_recorded_bindings
from agr.scene_package import read_scene, save_scene


def bound_instance():
    handle = (3 << 14) | 17
    return {'id': '23:2', 'index': 23, 'generation': 2, 'entity_id': '17:3',
            'entity_handle': handle, 'resource': 'particles/example.vpcf', 'created_tick': 100,
            'destroy_tick': None, 'release_ticks': [100],
            'events': [
                {'type': 'CREATE', 'tick': 100, 'data': {'index': 23, 'create_particle': {
                    'entity_handle': handle, 'attach_type': 5}}},
                {'type': 'UPDATE_ENT', 'tick': 100, 'data': {'index': 23, 'update_particle_ent': {
                    'control_point': 0, 'entity_handle': handle, 'attach_type': 5, 'attachment': 7}}},
                {'type': 'RELEASE', 'tick': 100, 'data': {'index': 23}}]}


def scene(instances, start=100):
    return {'particle_instances': instances, 'start_tick': start, 'end_tick': 120, 'effects': {}}


def test_preflight_definitely_unavailable_includes_reasons_but_no_false_success():
    row = bound_instance()
    row['events'][1]['data']['update_particle_ent']['entity_handle'] += 1
    result = preflight_recorded_bindings(scene([row]))
    assert result['status'] == 'unavailable' and result['possible_instances'] == 0
    assert result['rejection_groups'][0]['resource'] == 'particles/example.vpcf'
    assert 'exact-serial' in result['rejection_groups'][0]['reason']
    assert 'requires simulation and rendering' in result['scope']


def test_preflight_overestimates_candidates_and_respects_recovered_history():
    supported = bound_instance()
    other = copy.deepcopy(supported)
    other['entity_id'] = 'another:entity'  # A rejected unselected row cannot hide the possible one.
    assert preflight_recorded_bindings(scene([other, supported]))['status'] == 'possible'
    assert preflight_recorded_bindings(scene([supported], start=101))['status'] == 'unavailable'
    result = preflight_recorded_bindings(scene([supported], start=90), output_start=105)
    assert result['status'] == 'possible' and result['motion_start_tick'] == 90


def test_preflight_does_not_invent_an_answer_for_incomplete_inputs():
    assert preflight_recorded_bindings({})['status'] == 'indeterminate'
    assert preflight_recorded_bindings(scene([None]))['status'] == 'indeterminate'
    assert preflight_recorded_bindings(scene([]), output_start=121)['status'] == 'indeterminate'


def test_strict_export_stops_before_blender_and_offers_explicit_effects_recovery(tmp_path, monkeypatch):
    assets = tmp_path/'assets'
    assets.mkdir()
    (assets/'model.blend').write_bytes(b'fixture')
    (assets/'assets.json').write_text(json.dumps({'heroes': {'13': {'model_file': 'model.blend'}}}))
    engine = tmp_path/'blender.exe'
    engine.touch()
    probe = tmp_path/'ParticleProbe.exe'
    probe.touch()
    data = {'schema_version': 1, 'tick_rate': 64, 'start_tick': 100, 'end_tick': 100,
            'entities': [{'id': '17:3', 'hero_id': 13,
                          'samples': [{'tick': 100, 'position': [1, 2, 3], 'body_angles': [0, 0, 0]}]}]}
    segment = save_scene(data, tmp_path/'input.json')
    args = cli.parser().parse_args(['convert', '--segment', str(segment), '--game-install', str(tmp_path),
        '--source2viewer', str(tmp_path/'Source2Viewer-CLI.exe'), '--blender', str(engine),
        '--particle-probe', str(probe), '--output', str(tmp_path/'scene.blend')])
    monkeypatch.setattr(cli, 'resolve_asset_source', lambda args: {'install_path': tmp_path,
        'cache_dir': tmp_path/'cache', 'converter': tmp_path/'cli.exe', 'map_name': None})
    monkeypatch.setattr('agr.blender_runtime.check_blender', lambda *a, **k: {'fixture': True})
    monkeypatch.setattr('agr.particle_runtime.particle_command', lambda *a: [str(probe)])
    logs = tmp_path/'logs'
    monkeypatch.setattr('agr.export_diagnostics.log_directory', lambda: logs)
    def prepare(**kwargs):
        current = read_scene(kwargs['segment_path'])
        unsupported = bound_instance()
        unsupported['events'][1]['data']['update_particle_ent']['entity_handle'] += 1
        current.update(particle_instances=[unsupported], effects={})
        save_scene(current, kwargs['segment_path'])
        return assets
    monkeypatch.setattr('agr.installed_assets.prepare_installed_assets', prepare)
    calls = []
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError, match='Particle simulation has unresolved diagnostics'):
        cli.convert(args)
    assert calls == [] and not args.output.exists()
    import zipfile
    with zipfile.ZipFile(next(logs.glob('*.zip'))) as archive:
        failure = json.loads(archive.read('failure.json'))
        report = json.loads(archive.read('work/particle-preflight.json'))
    assert failure['stage'] == 'particle_preflight' and report['status'] == 'unavailable'
    choice = recovery_choices(['python', '-m', 'agr', 'convert', '--particle-probe', str(probe)], failure)
    assert [item.id for item in choice] == ['without_effects']

    # The explicit broad partial choice still reaches animation/scene work.
    class ReachedAnimation(Exception):
        pass
    def reached_animation(*args, **kwargs):
        raise ReachedAnimation()
    monkeypatch.setattr('agr.animation.evaluate_segment', reached_animation)
    args.partial = True
    with pytest.raises(ReachedAnimation):
        cli.convert(args)
