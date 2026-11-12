"""Structural transport fixtures; real pinned game-profile checks are separate."""
import copy
import math
import struct
import pytest
from agr import observed_rope_states as rope
from agr.replay_capture import canonical_sha256
from agr.particle_rope_geometry import build_rope_geometry


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    names = sorted(rope.PROFILES)
    definitions = {name: dict(m_nMaxParticles=200, m_Renderers=[dict(
        _class='C_OP_RenderRopes', m_nMinTesselation=2, m_nMaxTesselation=16,
        m_bUseScalarForTextureCoordinate=True, m_nScalarFieldForTextureCoordinate=26,
        m_flTextureVWorldSize=100)]) for name in names}
    # Private-free fixtures exercise the same source/digest comparisons. The
    # retained installed definitions are independently checked against real pins.
    monkeypatch.setattr(rope, 'PROFILES', {name: ('a'*64, canonical_sha256(d))
                                        for name, d in definitions.items()})
    files = {}
    for field in ('model_file', 'skeleton_file', 'pose_file'):
        p = tmp_path/field
        p.write_bytes(field.encode())
        files[field] = str(p)
    data = dict(start_tick=10, end_tick=12, tick_rate=64,
                metadata=dict(demo_sha256='d'*64, coordinate_system=dict(unit_scale=.0254)),
                entities=[dict(id=rope.OWNER, hero_id=65, samples=[dict(tick=10)], **files)])

    def raw_node(resource, count, tick, constant=False):
        fields = {}
        if count:
            for field, (_, components) in rope.FIELDS.items():
                cells = {}
                for index in range(count):
                    batch, lane = divmod(index, 4)
                    for axis in range(components):
                        # Independently arrange four-wide component blocks.
                        offset = (0 if constant else batch*components*16)+axis*16+lane*4
                        value = field*.01+axis+lane+(0 if constant else batch*4)+tick
                        if field == 3:
                            value = 2.
                        elif field in (7, 16):
                            value = .5
                        elif field == 21:
                            value = float(axis == 2)
                        cells[offset] = struct.pack('<f', value)
                raw = bytearray(max(cells)+4)
                for offset, value in cells.items():
                    raw[offset:offset+4] = value
                fields[str(field)] = dict(stride=0 if constant else components*4,
                                         components=components, raw_hex=raw.hex())
        return dict(resource=resource, particles=count,
                    field_state='captured' if count else 'known_empty', fields=fields)

    observations = []
    for tick, count in ((10, 5), (11, 0), (12, 3)):
        tree = dict(coherent=True, nodes=[raw_node(name, count, tick) for name in names])
        actor = dict(entity_index=56, handle=56+(895 << 15), life_state=0,
                     effects=[dict(resource=dict(name=rope.ROOT, state='resource_path'), declared_tree=tree)])
        observations.append(dict(target=tick, after=tick, native_capture=dict(
            tick=tick, actors=[actor], **rope.NATIVE_DRIVER_DEFAULTS)))
    report = dict(phase='closed', launch_count=1, exit_code=0, workflow_passed=True,
                  exact_target_coverage=True, recordings=[], error=None, cleanup_errors=[],
                  monitor_error=None, watchdog_fired=False, crash_inventory_changed=False,
                  config_unchanged_after_cleanup=True, source_unchanged_after_run=True,
                  retained_baseline_unchanged=True, pending_runtime_restoration={},
                  replay_sha256=data['metadata']['demo_sha256'], seeks=observations)
    cleanup = dict(status='PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED', source_absent=True,
                   runtime_restored=True, owned_native_removed_from_game=True)
    source = {k: 'b'*64 for k in rope.SOURCE_KEYS}
    assets = dict(definitions=definitions, provenance=dict(input_sha256={name: 'a'*64 for name in names}))
    return data, report, cleanup, source, assets, raw_node


def make(inputs):
    data, report, cleanup, source, _, _ = inputs
    return rope.build_reference(report, data, source, cleanup)


def reseal(ref):
    ref['capture_sha256'] = canonical_sha256(ref['capture'])


def test_complete_array_order_empty_and_input_immutability(inputs):
    before = copy.deepcopy(inputs[:5])
    ref = make(inputs)
    frames = rope.geometry_frames(ref, inputs[0], sorted(rope.PROFILES)[0])
    assert [len(f['particles']) for f in frames] == [5, 0, 3]
    assert frames[0]['particles'][4]['position'][0] == 14.
    assert frames[0]['particles'][3]['position'][2] == 15.
    assert all('id' not in p for f in ref['capture']['samples']
               for r in f['collections'] for p in r['points'])
    assert before == inputs[:5]


@pytest.mark.parametrize('count', [1, 3, 4, 5, 7, 8, 9, 64])
def test_constant_stride_retains_all_four_native_lanes(inputs, count):
    node = inputs[-1](sorted(rope.PROFILES)[0], count, 10, constant=True)
    points = rope._raw_points(node)
    assert [p['position'][0] for p in points] == [10.+i % 4 for i in range(count)]


@pytest.mark.parametrize('mutation', [
    lambda c: c.update(demo_sha256='e'*64),
    lambda c: c.update(entity_id='58:283'),
    lambda c: c.update(end_tick=11),
    lambda c: c.update(inclusive=False),
    lambda c: c.update(scene_identity='f'*64),
    lambda c: c['native'].update(client_sha256='a'*64),
    lambda c: c['source'].pop('cleanup_sha256'),
    lambda c: c['samples'].pop(1),
    lambda c: c['samples'][1].update(tick=10),
    lambda c: c['samples'][1]['collections'].pop(),
    lambda c: c['samples'][0]['collections'][0].update(state='unavailable'),
    lambda c: c['samples'][0]['collections'][0].update(count=64),
    lambda c: c['samples'][0]['collections'][0].update(count=True),
    lambda c: c['samples'][0]['collections'][0]['points'][0].update(id=1),
    lambda c: c['samples'][0]['collections'][0]['points'][0].update(radius=-1),
])
def test_reference_rejects_stale_partial_unknown_or_fake_identity(inputs, mutation):
    ref = make(inputs)
    mutation(ref['capture'])
    reseal(ref)
    with pytest.raises(ValueError):
        rope.validate_reference(ref, inputs[0])


def test_payload_digest_and_pose_file_changes_refused(inputs):
    ref = make(inputs)
    ref['capture']['samples'][0]['collections'][0]['points'][0]['radius'] = 1
    with pytest.raises(ValueError, match='digest'):
        rope.validate_reference(ref, inputs[0])
    ref = make(inputs)
    from pathlib import Path
    Path(inputs[0]['entities'][0]['pose_file']).write_bytes(b'changed pose')
    with pytest.raises(ValueError, match='identity'):
        rope.validate_reference(ref, inputs[0])


@pytest.mark.parametrize('key,value', [('exit_code', False), ('workflow_passed', False),
    ('config_unchanged_after_cleanup', False), ('crash_inventory_changed', True),
    ('monitor_error', 'lost ownership'), ('recordings', ['unexpected'])])
def test_incomplete_owned_run_never_publishes(inputs, key, value):
    inputs[1][key] = value
    with pytest.raises(ValueError, match='owned run'):
        make(inputs)


def test_no_root_is_not_empty_and_no_archive_is_not_complete(inputs):
    inputs[1]['seeks'][0]['native_capture']['actors'][0]['effects'] = []
    with pytest.raises(ValueError, match='root missing'):
        make(inputs)
    inputs[1]['seeks'] = []
    inputs[2]['source_absent'] = False
    with pytest.raises(ValueError, match='archived'):
        make(inputs)


@pytest.mark.parametrize('change', ['stride', 'length', 'nonfinite', 'count', 'field', 'components'])
def test_raw_attribute_bounds_and_nonfinite_refused(inputs, change):
    node = inputs[1]['seeks'][0]['native_capture']['actors'][0]['effects'][0]['declared_tree']['nodes'][0]
    if change == 'stride':
        node['fields']['3']['stride'] = 12
    elif change == 'length':
        node['fields']['0']['raw_hex'] += '00000000'
    elif change == 'nonfinite':
        node['fields']['0']['raw_hex'] = '0000c07f'+node['fields']['0']['raw_hex'][8:]
    elif change == 'count':
        node['particles'] = 65
    elif change == 'field':
        node['fields'].pop('26')
    else:
        node['fields']['0']['components'] = 1
    with pytest.raises(ValueError):
        make(inputs)


def test_package_source_profile_and_scope_bound(inputs):
    ref = make(inputs)
    package = rope.prepare_package(inputs[0], ref, inputs[4])
    rope.validate_package(package, inputs[0])
    package['assets']['definitions'][sorted(rope.PROFILES)[0]]['m_nMaxParticles'] = 1
    with pytest.raises(ValueError, match='descriptor digest'):
        rope.validate_package(package, inputs[0])
    package['assets_sha256'] = canonical_sha256(package['assets'])
    with pytest.raises(ValueError, match='definition changed'):
        rope.validate_package(package, inputs[0])


def test_reordered_frame_stays_reordered_without_cross_frame_identity(inputs):
    ref = make(inputs)
    name = sorted(rope.PROFILES)[0]
    ref['capture']['samples'][2]['collections'][0]['points'].reverse()
    reseal(ref)
    frames = rope.geometry_frames(ref, inputs[0], name)
    assert [p['id'] for p in frames[2]['particles']] == [0, 1, 2]
    assert [p['position'][0] for p in frames[2]['particles']] == [14, 13, 12]


def test_pinned_scalar_uv_geometry_does_not_use_an_invented_age(inputs):
    ref = make(inputs)
    name = sorted(rope.PROFILES)[0]
    particles = rope.geometry_frames(ref, inputs[0], name)[0]['particles']
    renderer = inputs[4]['definitions'][name]['m_Renderers'][0]
    a = build_rope_geometry(particles, renderer, 0, [0, 0, 100], camera_projection=dict(type='PERSP', projection_y=1))
    b = build_rope_geometry(particles, renderer, 1e8, [0, 0, 100], camera_projection=dict(type='PERSP', projection_y=1))
    import numpy as np
    for field in ('vertices', 'uv', 'colors'):
        assert np.array_equal(a[field], b[field])


def test_screen_facing_geometry_neither_needs_nor_uses_particle_normals(inputs):
    ref = make(inputs)
    name = sorted(rope.PROFILES)[0]
    particles = rope.geometry_frames(ref, inputs[0], name)[0]['particles']
    assert all('normal' not in p for p in particles)
    renderer = inputs[4]['definitions'][name]['m_Renderers'][0]
    params = dict(camera_position=[0, 0, 100], camera_projection=dict(type='PERSP', projection_y=1))
    a = build_rope_geometry(particles, renderer, **params)
    b = build_rope_geometry([dict(p, normal=[999, 0, -123]) for p in particles], renderer, **params)
    import numpy as np
    for field in ('vertices', 'uv', 'colors'):
        assert np.array_equal(a[field], b[field])
    renderer = dict(renderer, m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL')
    with pytest.raises((KeyError, ValueError)):
        build_rope_geometry(particles, renderer, **params)


@pytest.mark.parametrize('missing', [True, False])
def test_scalar26_requires_the_observed_finite_value(inputs, missing):
    ref = make(inputs)
    name = sorted(rope.PROFILES)[0]
    particles = rope.geometry_frames(ref, inputs[0], name)[0]['particles']
    if missing:
        particles[0].pop('scratch_float1')
    else:
        particles[0]['scratch_float1'] = float('nan')
    with pytest.raises(ValueError):
        build_rope_geometry(particles, inputs[4]['definitions'][name]['m_Renderers'][0],
                            camera_position=[0,0,100], camera_projection=dict(type='PERSP',projection_y=1))


def test_package_extra_fields_and_unrelated_model_assets_refuse(inputs):
    package = rope.prepare_package(inputs[0], make(inputs), inputs[4])
    package['unqualified'] = True
    with pytest.raises(ValueError, match='fields differ'):
        rope.validate_package(package, inputs[0])
    package.pop('unqualified')
    package['assets']['models'] = {'unrelated': {}}
    package['assets_sha256'] = canonical_sha256(package['assets'])
    with pytest.raises(ValueError, match='model assets'):
        rope.validate_package(package, inputs[0])


def test_generic_component_admission_uses_the_strict_rope_validator(inputs):
    from agr.simulated_effect_package import validate_package
    package = rope.prepare_package(inputs[0], make(inputs), inputs[4])
    validate_package(package, inputs[0])
    package['reference']['capture']['samples'].pop()
    package['reference']['capture_sha256'] = canonical_sha256(package['reference']['capture'])
    with pytest.raises(ValueError, match='complete/dense'):
        validate_package(package, inputs[0])
