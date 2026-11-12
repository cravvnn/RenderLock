"""Private-free transport fixtures; actual native/profile checks live separately."""
import copy
import math
import struct
import pytest
from agr import observed_sprite_states as sprite
from agr.replay_capture import canonical_sha256
from agr.particle_sheet_uv import POLICY as UV_POLICY, SHADERS


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    definitions = {n: {'m_Renderers': [{'_class': 'C_OP_RenderSprites'}]} for n in sprite.PROFILES}
    monkeypatch.setattr(sprite, 'PROFILES', {n: ('a'*64, canonical_sha256(d)) for n,d in definitions.items()})
    files = {}
    for name in ('model_file','skeleton_file','pose_file'):
        p = tmp_path/name; p.write_bytes(name.encode()); files[name] = str(p)
    data = dict(start_tick=10,end_tick=12,tick_rate=64,
        metadata=dict(demo_sha256='d'*64,coordinate_system=dict(unit_scale=.0254)),
        entities=[dict(id=sprite.OWNER,hero_id=65,samples=[],**files)])
    def node(name, count, constant=False):
        fields = {}
        for f,c in sprite.RAW_FIELDS.items() if count else ():
            stride = 0 if constant else c*4; cells = {}
            for i in range(count):
                for axis in range(c):
                    value = (i % 4 if constant else i)+axis*10 if f == 0 else .5
                    if f == 6: value = .2+.2*axis
                    if f == 3: value = 2.
                    if f == 9: value = float(i % 4)
                    if f == 38: value = .125
                    if f == 39: value = 2.
                    if f == 11: value = -2147483648+i
                    cells[((i//4)*stride+axis*4+i % 4)*4] = struct.pack('<i' if f == 11 else '<f',value)
            raw = bytearray(max(cells)+4)
            for at,b in cells.items(): raw[at:at+4] = b
            fields[str(f)] = dict(components=c,stride=stride,raw_hex=raw.hex())
        return dict(resource=name,particles=count,field_state='captured' if count else 'known_empty',fields=fields,
            clock_50_raw=(struct.pack('<f',-.25)+bytes(36)).hex(),flags_8c9=0,
            sprite_inputs=dict(random_policy=sprite.INPUT_POLICY['random'],random_draw_offset=235,native_random_values=[.625]*count))
    observations = []
    for tick,count in ((10,5),(11,0),(12,3)):
        actor = dict(entity_index=56,handle=56+(895<<15),life_state=0,effects=[dict(resource=dict(name=sprite.ROOT),
            declared_tree=dict(coherent=True,nodes=[node(n,count) for n in sprite.PROFILES]))])
        observations.append(dict(target=tick,after=tick,native_capture=dict(tick=tick,actors=[actor],**sprite.NATIVE_DRIVER_DEFAULTS)))
    report = dict(phase='closed',launch_count=1,exit_code=0,workflow_passed=True,exact_target_coverage=True,
        recordings=[],error=None,cleanup_errors=[],config_unchanged_after_cleanup=True,source_unchanged_after_run=True,
        retained_baseline_unchanged=True,pending_runtime_restoration={},replay_sha256='d'*64,seeks=observations)
    cleanup = dict(status='PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED',source_absent=True,runtime_restored=True,owned_native_removed_from_game=True)
    source = {k:'b'*64 for k in sprite.SOURCE_KEYS}
    assets = dict(definitions=definitions,textures={},provenance=dict(input_sha256={n:'a'*64 for n in sprite.PROFILES},
        spritecard_sheet_uv=dict(policy=UV_POLICY,sha256=SHADERS.copy())))
    return data,report,source,cleanup,assets,node


def reference(inputs): return sprite.build_reference(inputs[1],inputs[0],inputs[2],inputs[3])


def test_complete_arrays_native_inputs_empty_and_no_lifetime_identity(inputs):
    before = copy.deepcopy(inputs[:5]); ref = reference(inputs)
    rows = [s['collections'][0] for s in ref['capture']['samples']]
    assert [r['count'] for r in rows] == [5,0,3] and rows[1]['state'] == 'known_empty'
    assert rows[0]['collection_time'] == -.25
    p = rows[0]['points'][4]
    assert p['position'] == [4.,14.,24.] and p['manual_frame'] == .125 and p['native_random'] == .625
    assert p['color_bytes'] == [51,102,153,64]
    assert not {'id','native_id','birth','age','seed'} & set(p)
    assert inputs[:5] == before
    sprite.validate_package(sprite.prepare_package(inputs[0],ref,inputs[4]),inputs[0])


def test_constant_simd_lanes_and_changed_raw_input_refusal(inputs):
    for count in (1,3,4,5,7,8,9,64):
        n = inputs[-1](next(iter(sprite.PROFILES)),count,True)
        assert [p['position'][0] for p in sprite._point_rows(n)] == [float(i % 4) for i in range(count)]
    n['fields']['11']['raw_hex'] = n['fields']['11']['raw_hex'][:-2]
    with pytest.raises(ValueError,match='length'): sprite._point_rows(n)


def test_resealed_stale_partial_unknown_or_invented_reference_refused(inputs):
    changes = [lambda c:c.update(demo_sha256='e'*64), lambda c:c.update(entity_id='58:283'),
        lambda c:c.update(inclusive=False), lambda c:c['samples'].pop(1),
        lambda c:c['samples'][0]['collections'].pop(), lambda c:c['samples'][0]['collections'][0].update(state='unavailable'),
        lambda c:c['samples'][0]['collections'][0].update(count=True), lambda c:c['source'].pop('native_inputs_sha256'),
        lambda c:c['input_policy'].update(animation='fixed_rate'),
        lambda c:c['samples'][0]['collections'][0]['points'][0].update(native_random=math.nan),
        lambda c:c['samples'][0]['collections'][0]['points'][0].update(manual_frame=None),
        lambda c:c['samples'][0]['collections'][0]['points'][0].update(id=1)]
    for change in changes:
        ref = reference(inputs); change(ref['capture']); ref['capture_sha256'] = canonical_sha256(ref['capture']) if change != changes[-3] else 'a'*64
        with pytest.raises(ValueError): sprite.validate_reference(ref,inputs[0])


def test_changed_pose_digest_assets_or_owned_cleanup_refuse(inputs):
    data,report,source,cleanup,assets,_ = inputs
    for key,value in (('exit_code',False),('workflow_passed',False),('config_unchanged_after_cleanup',False),('monitor_error','lost ownership')):
        changed = copy.deepcopy(report); changed[key] = value
        with pytest.raises(ValueError): sprite.build_reference(changed,data,source,cleanup)
    changed = dict(cleanup,source_absent=False)
    with pytest.raises(ValueError): sprite.build_reference(report,data,source,changed)
    package = sprite.prepare_package(data,reference(inputs),assets); package['assets']['definitions'][next(iter(sprite.PROFILES))]['changed'] = True
    with pytest.raises(ValueError): sprite.validate_package(package,data)
    ref = reference(inputs)
    from pathlib import Path
    Path(data['entities'][0]['pose_file']).write_bytes(b'changed pose')
    with pytest.raises(ValueError,match='identity'): sprite.validate_reference(ref,data)


def test_package_extra_fields_and_unrelated_models_refuse(inputs):
    package = sprite.prepare_package(inputs[0], reference(inputs), inputs[4])
    package['unqualified'] = True
    with pytest.raises(ValueError, match='fields differ'):
        sprite.validate_package(package, inputs[0])
    package.pop('unqualified')
    package['assets']['models'] = {'unrelated': {}}
    package['assets_sha256'] = canonical_sha256(package['assets'])
    with pytest.raises(ValueError, match='model assets'):
        sprite.validate_package(package, inputs[0])


def test_generic_component_admission_uses_the_strict_sprite_validator(inputs):
    from agr.simulated_effect_package import validate_package
    package = sprite.prepare_package(inputs[0], reference(inputs), inputs[4])
    validate_package(package, inputs[0])
    package['reference']['capture']['samples'].pop()
    package['reference']['capture_sha256'] = canonical_sha256(package['reference']['capture'])
    with pytest.raises(ValueError, match='complete/dense'):
        validate_package(package, inputs[0])
