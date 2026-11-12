import struct
import math
import pytest
from agr import observed_slide_light_states as s
from agr.particle_point_light import blender_parameters, decode_descriptor, source_attenuation
from agr.replay_capture import canonical_sha256
from agr.simulated_effect_package import scene_identity, validate_package
from test_observed_slide_sprite_states import data


def descriptor():
    raw = bytearray(0x120)
    struct.pack_into('<I6f', raw, 0, 1, 100., 400., 1000., 100., 400., 1000.)
    struct.pack_into('<f', raw, 0x1c, 240.)
    struct.pack_into('<3f', raw, 0x24, 0., 0., 1.)
    struct.pack_into('<3f', raw, 0x98, -5., 6., 7.)
    struct.pack_into('<fI', raw, 0xcc, 1/240**2, 12)
    return bytes(raw)


def reseal(r):
    r['capture_sha256'] = canonical_sha256(r['capture'])
    return r


def reference(data):
    source = {k: 'a'*64 for k in s.SOURCE_KEYS}
    source.update(evaluated_reader_sha256=s.READER_SHA, layout_sha256=s.LAYOUT_SHA, copy_proof_sha256=s.COPY_SHA)
    c = dict(schema_version=1, policy=s.POLICY, entity_id=s.OWNER, scene_identity=scene_identity(data, [s.OWNER]),
             native=dict(s.NATIVE_DRIVER_DEFAULTS), source=source, between_tick_policy='hold_complete_arrays',
             frames=[dict(tick=1, root_count=1, systems=[dict(root_ordinal=0, child_path='root/6', state='captured',
                     particle_count=1, points=[dict(particle_id=3, descriptor_raw=descriptor().hex())])]),
                     dict(tick=2, root_count=0, systems=[])], scope=s.SCOPE)
    return reseal(dict(capture=c))


def test_explicit_native_units_energy_and_quadratic_falloff():
    raw = descriptor();d = decode_descriptor(raw);p = blender_parameters(raw, .0254)
    assert p['color'] == [.1, .4, 1.] and p['casts_shadows'] is False
    assert p['location'] == pytest.approx([-.127, .1524, .1778])
    assert p['energy'] == pytest.approx(4*math.pi*1000*.0254**2)
    for distance in (1., 2., 100., 240., 241.):
        expected = max(0., 1/distance**2-d['falloff_offset']) if distance <= 240 else 0.
        assert source_attenuation(distance, d) == pytest.approx(expected)
    assert source_attenuation(0., d) == pytest.approx(1-d['falloff_offset'])
    for offset, fmt, value in ((0, '<I', 3), (4, '<f', float('nan')), (0x1c, '<f', -1.),
                               (0x24, '<f', 1.), (0xd0, '<I', 8), (0x3d, '<B', 2)):
        changed = bytearray(raw);struct.pack_into(fmt, changed, offset, value)
        with pytest.raises(ValueError): decode_descriptor(bytes(changed))


def test_normal_package_accepts_complete_arrays_and_explicit_empty(data):
    r = reference(data);p = s.prepare_package(data, r);validate_package(p, data)
    row = r['capture']['frames'][0]['systems'][0]
    row.update(state='known_empty', particle_count=0, points=[])
    s.validate_reference(reseal(r), data)


@pytest.mark.parametrize('change', ['prefix', 'missing_root', 'missing_point', 'unavailable', 'duplicate_id',
    'bool_count', 'ordinal', 'different_scene', 'layout', 'scope', 'clock_policy', 'descriptor'])
def test_normal_admission_rejects_incomplete_or_unqualified_frames(data, change):
    r = reference(data);c = r['capture'];frame = c['frames'][0];row = frame['systems'][0]
    if change == 'prefix':c['frames'].pop()
    elif change == 'missing_root':frame['systems'] = []
    elif change == 'missing_point':row['points'] = []
    elif change == 'unavailable':row['state'] = 'unavailable'
    elif change == 'duplicate_id':row['points'] *= 2;row['particle_count'] = 2
    elif change == 'bool_count':row['particle_count'] = True
    elif change == 'ordinal':row['root_ordinal'] = 1
    elif change == 'different_scene':c['scene_identity'] = 'e'*64
    elif change == 'layout':c['source']['layout_sha256'] = 'b'*64
    elif change == 'scope':c['scope'] = 'All particles complete'
    elif change == 'clock_policy':c['between_tick_policy'] = 'interpolate'
    elif change == 'descriptor':row['points'][0]['descriptor_raw'] = '00'*0x120
    with pytest.raises(ValueError):s.validate_reference(reseal(r), data)


def test_optional_stage_refuses_mixed_cohort_before_allocation_and_keeps_birth_counts(data, tmp_path):
    from agr.observed_slide_light_effects import build_observed_slide_light_effects, merge_observed_slide_light_report
    runtime = dict(output_dir=str(tmp_path), observed_slide_light_reference=reference(data),
                   observed_slide_sprite_reference=dict(capture=dict(source=dict(report_sha256='b'*64))))
    stage = build_observed_slide_light_effects(data, None, None, {s.OWNER:{}}, runtime)
    assert stage['status'] == 'unavailable' and 'cohort' in stage['reason']
    assert not (tmp_path/'native-slide-lights.package.json').exists()
    report = dict(objects=3, point_lights=2, instances=7, particle_tracks=9, unsupported=[])
    merge_observed_slide_light_report(report, stage)
    assert (report['objects'],report['point_lights'],report['instances'],report['particle_tracks']) == (3,2,7,9)
