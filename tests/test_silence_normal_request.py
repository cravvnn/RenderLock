import copy
from types import SimpleNamespace
import json

import pytest

from agr import particle_hitbox_assets as inventory
from agr import recorded_silence_effects as normal
from agr.particle_silence_inputs import prepare_observed_inputs
from agr.particle_silence_capture import bind_silence_capture
from agr.particle_client_capture import bind_client_child_capture
from agr.particle_snapshot_provider import digest
from agr.recorded_effect_simulation import substitute_seed
from test_particle_silence_capture import source


@pytest.fixture
def normal_request(source):
    raw, scene, instance, child = source
    birth = bind_silence_capture(raw, scene, instance)
    child = bind_client_child_capture(child, scene, instance)
    observed = prepare_observed_inputs(scene, instance, birth, child)
    points = [dict(tick=t, position=[t, 0, 0], rotation=[0, 0, 0, 1], model_hitboxes=[])
              for t in range(10, 13)]
    provider = dict(normal_inputs_policy=normal.NORMAL_POLICY, points_sha256=digest(points), policy='fixture', bindings=[])
    row = normal.request_inputs(scene, instance, observed, points, provider)
    plan = dict(start_tick=10, end_tick=12, tick_rate=64, gameinfo='fixture',
                seed=substitute_seed(scene, instance), instances=[row])
    package = dict(hitbox_provider=provider, silence_birth_capture=birth, client_child_capture=child,
                   simulation_request=plan, simulation_request_sha256=digest(plan))
    return package, scene, instance, points, observed


def test_request_preserves_original_creation_and_events_without_aliasing(normal_request):
    package, scene, instance, points, observed = normal_request
    before = copy.deepcopy((instance, points, observed))
    normal.validate_normal_request(package, scene, instance)
    row = package['simulation_request']['instances'][0]
    assert row['events'] == instance['events']
    assert row['events'][0]['data']['create_particle']['attach_type'] == 3
    row['control_points'][0]['position'][0] = 900
    assert (instance, points, observed) == before


@pytest.mark.parametrize('kind', ['events', 'pose', 'missing_frame', 'seed', 'range', 'profile', 'header', 'extra'])
def test_rehashed_request_does_not_bypass_original_inputs(normal_request, kind):
    package, scene, instance, _, _ = normal_request
    request = package['simulation_request']
    row = request['instances'][0]
    if kind == 'events':
        row['events'][0]['data']['create_particle']['attach_type'] = 1
    if kind == 'pose':
        row['control_points'][0]['position'][0] = 900
    if kind == 'missing_frame':
        row['control_points'].pop()
    if kind == 'seed':
        request['seed'] += 1
    if kind == 'range':
        request['end_tick'] += 1
    if kind == 'profile':
        package['hitbox_provider']['normal_inputs_policy'] = 'unknown'
    if kind == 'header':
        row['silence_birth_provider']['observed_mode'] = 3
    if kind == 'extra':
        row['other_profile'] = True
    package['simulation_request_sha256'] = digest(request)
    with pytest.raises(ValueError):
        normal.validate_normal_request(package, scene, instance)


@pytest.fixture
def collector(tmp_path, monkeypatch):
    probe = tmp_path / 'probe.dll'
    parser = tmp_path / 'ValveResourceFormat.dll'
    probe.write_bytes(b'probe'); parser.write_bytes(b'parser')
    model = dict(resource='models/owner.vmdl_c', resource_sha256='a'*64, names=['root'])
    value = dict(schema_version=1, model='models/owner.vmdl', model_bone_names=['root'],
        sources=[dict(route='model', resource='models/owner.vmdl', sha256='a'*64)])
    provider = SimpleNamespace(resolve=lambda resource: SimpleNamespace(sha256='a'*64))
    runtime = dict(probe=str(probe), install_path=str(tmp_path))
    monkeypatch.setattr(inventory, 'particle_command', lambda *args: ['fake'])
    monkeypatch.setattr(inventory, 'particle_environment', lambda *args: {})
    def run(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_text(json.dumps(value))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(inventory.subprocess, 'run', run)
    return provider, runtime, model, value, tmp_path / 'hitboxes.json', probe


def test_collector_returns_only_successful_fresh_snapshot(collector):
    provider, runtime, model, value, output, _ = collector
    actual, receipt = inventory.collect_inventory(provider, runtime, model, output)
    assert actual == value and receipt['sources'] == value['sources']
    with pytest.raises(ValueError, match='fresh'):
        inventory.collect_inventory(provider, runtime, model, output)


@pytest.mark.parametrize('kind', ['partial_failure', 'wrong_bones', 'wrong_model', 'wrong_source', 'helper_changed', 'log_collision'])
def test_collector_refuses_failed_or_changed_outputs(collector, monkeypatch, kind):
    provider, runtime, model, value, output, probe = collector
    if kind == 'wrong_bones':
        value['model_bone_names'] = ['other']
    if kind == 'wrong_model':
        value['model'] = 'models/other.vmdl'
    if kind == 'wrong_source':
        value['sources'][0]['sha256'] = 'b'*64
    if kind == 'log_collision':
        output.with_suffix('.log').write_text('prior')
    if kind in ('partial_failure', 'helper_changed'):
        original = inventory.subprocess.run
        def changed(command, **kwargs):
            result = original(command, **kwargs)
            if kind == 'partial_failure':
                result.returncode = 1
            else:
                probe.write_bytes(b'changed')
            return result
        monkeypatch.setattr(inventory.subprocess, 'run', changed)
    with pytest.raises(ValueError):
        inventory.collect_inventory(provider, runtime, model, output)
    if kind == 'log_collision':
        assert output.with_suffix('.log').read_text() == 'prior' and not output.exists()
