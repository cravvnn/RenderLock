import copy
import hashlib

import pytest

from agr import captured_slide_binding as binding


@pytest.fixture
def pair(tmp_path, monkeypatch):
    entity = dict(id=binding.OWNER, hero_id=65, model_handle=10, model_yaw_degrees=270,
        pose_basis_yaw_degrees=-90, render_root_yaw_degrees=180, model_scale=1,
        observed_intervals=[dict(start_tick=17920, end_tick_exclusive=18241)],
        samples=[dict(tick=t, position=[float(t), 0, 0], body_angles=[0, 180, 0],
            network_render_state=dict(material_group=0) if t < 17920 else
                dict(material_group=0, rgba8_packed=0xffffffff, mode=0)) for t in range(17858, 18241)],
        model_constraints=dict(schema_version=1, definitions=[], names=['root'], parents=[-1],
            preparation_dependencies={'effect_assets.py': binding.EFFECT_ASSETS_SOURCE, 'math.py': 'unchanged'}))
    pins = {}
    for key in binding.SOURCE_FILES:
        path = tmp_path / key
        path.write_bytes(key.encode())
        entity[key] = str(path)
        pins[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(binding, 'SOURCE_FILES', pins)
    source = dict(start_tick=17920, end_tick=18240, tick_rate=64,
        metadata=dict(demo_sha256=binding.DEMO, coordinate_system=dict(unit_scale=.0254, origin=[0, 0, 0])),
        entities=[entity], particle_history=dict(schema_version=1, start_tick=17858),
        particle_instances=[], network_projectiles=dict(legacy='retained'))
    monkeypatch.setattr(binding, 'SOURCE_ID', binding._source_scene_identity(source, [binding.OWNER]))
    target = copy.deepcopy(source)
    e = target['entities'][0]
    e['observed_intervals'] = [dict(start_tick=17858, end_tick_exclusive=18241)]
    e['model_constraints']['preparation_dependencies']['effect_assets.py'] = binding.EFFECT_ASSETS_TARGET
    for sample in e['samples']:
        if sample['tick'] < 17920:
            sample['network_render_state'].update(rgba8_packed=0xffffffff, mode=0)
    target['network_projectiles'] = dict(current='independently validated envelope')
    return source, target


def test_binding_preserves_original_and_binds_full_fresh_identity(pair):
    source, target = pair
    before = copy.deepcopy(source)
    value = binding.bind_retained_owner(source, target)
    binding.validate_retained_binding(value, source, target)
    assert source == before and value['source_scene_identity'] == binding.SOURCE_ID
    other = copy.deepcopy(target)
    other['particle_instances'].append(dict(id='99:1', entity_id=binding.OWNER))
    changed = binding.bind_retained_owner(source, other)
    assert changed['target_scene_identity'] != value['target_scene_identity']
    with pytest.raises(ValueError, match='contents or fresh inputs'):
        binding.validate_retained_binding(value, source, other)


@pytest.mark.parametrize('change', [
    lambda d: d['metadata'].update(demo_sha256='b' * 64),
    lambda d: d['entities'][0]['samples'][62]['network_render_state'].update(mode=1),
    lambda d: d['entities'][0]['model_constraints']['preparation_dependencies'].update(**{'math.py': 'changed'}),
])
def test_changed_target_never_binds(pair, change):
    source, target = pair
    change(target)
    with pytest.raises(ValueError):
        binding.bind_retained_owner(source, target)


def test_changed_original_is_not_rehashed_and_changed_file_is_refused(pair, tmp_path):
    source, target = pair
    wrong = copy.deepcopy(source)
    wrong['network_projectiles']['legacy'] = 'changed'
    with pytest.raises(ValueError, match='original retained scene identity'):
        binding.bind_retained_owner(wrong, target)
    path = tmp_path / 'changed_pose'
    path.write_bytes(b'changed')
    target['entities'][0]['pose_file'] = str(path)
    with pytest.raises(ValueError, match='asset bytes changed'):
        binding.bind_retained_owner(source, target)
