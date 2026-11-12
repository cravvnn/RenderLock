import copy
import json
import pytest
from agr import observed_slide_trail_states as s
from agr.replay_capture import canonical_sha256
from agr.simulated_effect_package import scene_identity, validate_package as validate_normal_package
from agr.observed_slide_trail_effects import build_observed_slide_trail_effects, merge_observed_slide_trail_report
from agr.blender_observed_slide_trails import build_captured_frame
from test_observed_slide_sprite_states import data, reference as sprite_reference


def point():
    return dict(controls_xyzw=[[0., 0., 0., 1.], [1., 0., 0., 1.], [2., 0., 0., 1.], [3., 0., 0., 1.]],
                endpoint_rgba=[[128, 128, 128, 64], [128, 128, 128, 32]],
                crop_flip_rgba=[0, 0, 255, 255], native_animation_input=.1, native_random=.5)


def reseal(r):
    r['capture_sha256'] = canonical_sha256(r['capture'])
    return r


def reference(data, monkeypatch):
    inputs = dict(measured_profile='test', trail_controls_raw='00'*28)
    monkeypatch.setattr(s, 'INPUTS_SHA', s.renderer_input_sha(inputs))
    base = sprite_reference(data)
    source = {k: 'a'*64 for k in s.SOURCE_KEYS}
    source.update(report_sha256=base['capture']['source']['report_sha256'], sprite_capture_sha256=base['capture_sha256'],
                  layout_sha256=s.LAYOUT_SHA, upload_helper_sha256=s.UPLOAD_HELPER_SHA, projection_helper_sha256=s.PROJECTION_HELPER_SHA)
    c = dict(schema_version=1, policy=s.POLICY, entity_id=s.OWNER, scene_identity=scene_identity(data, [s.OWNER]),
             native=dict(s.NATIVE_DRIVER_DEFAULTS), sprite_reference=base, source=source, between_tick_policy='hold_complete_arrays',
             frames=[dict(tick=1, systems=[dict(root_ordinal=0, child_path='root/1', state='captured', collection_time=.1,
                                              renderer_inputs=inputs, points=[point()])]), dict(tick=2, systems=[])], scope=s.SCOPE)
    return reseal(dict(capture=c))


def assets(monkeypatch):
    from agr.particle_sheet_uv import POLICY, SHADERS
    definition = dict(m_Renderers=[dict(_class='C_OP_RenderTrails', m_vecTexturesInput=[dict(m_hTexture=s.TEXTURE)])])
    monkeypatch.setattr(s, 'DEFINITION_SHA', canonical_sha256(definition))
    return dict(models={}, definitions={s.RESOURCE: definition}, textures={s.TEXTURE: dict(status='supported', atlas=False,
        is_sprite_sheet=False, reasons=[], sequences={'0': dict(status='supported', frames=[dict(image='beam_generic04.png', duration=1., width=64,
        height=128, sha256=s.IMAGE_SHA)])}, images={'beam_generic04.png': dict(width=64, height=128, sha256=s.IMAGE_SHA)})},
        provenance=dict(input_sha256={s.RESOURCE:s.SOURCE_SHA, s.TEXTURE:s.TEXTURE_SHA}, spritecard_sheet_uv=dict(policy=POLICY, sha256=SHADERS)))


def test_complete_reference_accepts_absent_current_roots_and_distinguishes_empty(data, monkeypatch):
    r = reference(data, monkeypatch);s.validate_reference(r, data)
    row = r['capture']['frames'][0]['systems'][0]
    row.update(state='known_empty', points=[]);row.pop('renderer_inputs');row.pop('collection_time')
    s.validate_reference(reseal(r), data)
    row['state'] = 'unavailable'
    with pytest.raises(ValueError, match='unavailable'):s.validate_reference(reseal(r), data)


def test_native_control_padding_is_ignored_but_consumed_fields_remain_bound():
    raw = bytearray.fromhex('02000000000048420000000000e03d3f0000803f0000803f00000000')
    expected = s.renderer_input_sha(dict(trail_controls_raw=raw.hex()))
    for padding in (b'\0'*3, b'\xff'*3, b'abc'):
        changed = raw.copy();changed[13:16] = padding
        assert s.renderer_input_sha(dict(trail_controls_raw=changed.hex())) == expected
    for offset in (0,4,8,12,16,20,24):
        changed = raw.copy();changed[offset] ^= 1
        assert s.renderer_input_sha(dict(trail_controls_raw=changed.hex())) != expected


def test_qualified_native_helpers_are_admitted_only_as_matched_pairs(data, monkeypatch):
    for upload,projection in s.QUALIFIED_HELPER_PAIRS:
        r = reference(data, monkeypatch)
        r['capture']['source'].update(upload_helper_sha256=upload, projection_helper_sha256=projection)
        s.validate_reference(reseal(r),data)
    pairs = sorted(s.QUALIFIED_HELPER_PAIRS)
    r = reference(data, monkeypatch)
    r['capture']['source'].update(upload_helper_sha256=pairs[0][0],projection_helper_sha256=pairs[1][1])
    with pytest.raises(ValueError,match='helper'):s.validate_reference(reseal(r),data)


@pytest.mark.parametrize('change', ['prefix', 'root_reuse', 'missing_root', 'ordinal', 'child_path', 'truncated', 'renderer',
    'layout', 'source', 'helper', 'base_report', 'base_identity', 'base_prefix', 'scope', 'clock_policy', 'bool_tick',
    'nan_control', 'negative_radius', 'float_byte', 'missing_uv', 'random', 'point_budget'])
def test_rejects_incomplete_different_run_or_unqualified_inputs(data, monkeypatch, change):
    r = reference(data, monkeypatch);c = r['capture'];row = c['frames'][0]['systems'][0];p = row['points'][0]
    if change == 'prefix':c['frames'].pop()
    elif change == 'root_reuse':c['frames'][1]['systems'] = copy.deepcopy(c['frames'][0]['systems'])
    elif change == 'missing_root':c['frames'][0]['systems'] = []
    elif change == 'ordinal':row['root_ordinal'] = True
    elif change == 'child_path':row['child_path'] = 'root/2'
    elif change == 'truncated':row['points'] = []
    elif change == 'renderer':row['renderer_inputs']['fading'] = True
    elif change == 'layout':c['source']['layout_sha256'] = 'b'*64
    elif change == 'source':c['source']['report_sha256'] = 'c'*64
    elif change == 'helper':c['source']['upload_helper_sha256'] = 'd'*64
    elif change == 'base_report':c['sprite_reference']['capture']['source']['report_sha256'] = 'c'*64;reseal(c['sprite_reference']);c['source']['sprite_capture_sha256'] = c['sprite_reference']['capture_sha256']
    elif change == 'base_identity':c['sprite_reference']['capture']['scene_identity'] = 'e'*64;reseal(c['sprite_reference'])
    elif change == 'base_prefix':c['sprite_reference']['capture']['samples'].pop();reseal(c['sprite_reference'])
    elif change == 'scope':c['scope'] = 'All particles complete'
    elif change == 'clock_policy':c['between_tick_policy'] = 'interpolate'
    elif change == 'bool_tick':c['frames'][0]['tick'] = True
    elif change == 'nan_control':p['controls_xyzw'][0][0] = float('nan')
    elif change == 'negative_radius':p['controls_xyzw'][0][3] = -1.
    elif change == 'float_byte':p['endpoint_rgba'][0][0] = 1.
    elif change == 'missing_uv':p.pop('crop_flip_rgba')
    elif change == 'random':p['native_random'] = 2.
    else:row['points'] *= 65
    with pytest.raises(ValueError):s.validate_reference(reseal(r), data)


def test_normal_package_validates_and_freezes_inputs(data, monkeypatch):
    r = reference(data, monkeypatch);a = assets(monkeypatch)
    p = s.prepare_package(data, r, a);validate_normal_package(p, data)
    r['capture']['frames'][0]['systems'][0]['points'][0]['controls_xyzw'][0][0] = 99.
    assert p['reference']['capture']['frames'][0]['systems'][0]['points'][0]['controls_xyzw'][0][0] == 0.
    changed = copy.deepcopy(p);changed['assets']['textures'][s.TEXTURE]['is_sprite_sheet'] = True
    changed['assets_sha256'] = canonical_sha256(changed['assets'])
    with pytest.raises(ValueError, match='static'):validate_normal_package(changed, data)
    changed = copy.deepcopy(p);changed['assets']['definitions'][s.RESOURCE]['m_Renderers'][0]['_class'] = 'Other'
    changed['assets_sha256'] = canonical_sha256(changed['assets'])
    with pytest.raises(ValueError, match='definition'):validate_normal_package(changed, data)
    changed = copy.deepcopy(p);changed['assets']['textures'][s.TEXTURE]['sequences']['0']['status'] = 'unsupported'
    changed['assets_sha256'] = canonical_sha256(changed['assets'])
    with pytest.raises(ValueError, match='frame identity'):validate_normal_package(changed, data)


def test_geometry_uses_explicit_uploaded_controls_bytes_camera_and_uv():
    row = dict(points=[point()]);g = build_captured_frame(row, [0., 0., 10.])
    assert len(g['vertices']) == 4 and g['faces'] == [(0, 1, 2), (1, 2, 3)]
    assert g['uv'] == [(0., 0.), (1., 0.), (0., 1.), (1., 1.)]
    assert g['colors'][0][3] == pytest.approx(64/255) and g['colors'][2][3] == pytest.approx(32/255)
    other = build_captured_frame(row, [0., 10., 0.])
    assert other['vertices'] != g['vertices'] and other['colors'] == g['colors']
    assert build_captured_frame(dict(points=[]), [0., 0., 10.])['vertices'] == []


def test_unselected_owner_prepares_nothing_and_component_merge_preserves_births():
    observed = build_observed_slide_trail_effects({}, None, None, {}, {}, None, None, None)
    assert observed['status'] == 'not_selected' and observed['objects'] == 0
    report = dict(objects=5, point_lights=2, rendered_instances=91, verified_empty_instances=1,
                  unsupported_by_birth_scope={'history':28})
    old = copy.deepcopy(report);observed.update(objects=48, coverage_scope=s.SCOPE)
    merge_observed_slide_trail_report(report, observed)
    assert report['objects'] == 53
    for k in ('rendered_instances', 'verified_empty_instances', 'unsupported_by_birth_scope'):assert report[k] == old[k]


def test_retained_projection_requires_exact_same_report_and_file_link(data, monkeypatch, tmp_path):
    import hashlib
    r = reference(data, monkeypatch);base = r['capture']['sprite_reference']
    frames = copy.deepcopy(r['capture']['frames']);pframes = copy.deepcopy(frames)
    for f in pframes:
        for system in f['systems']:
            system['points'] = [dict(controls_xyz=[v[:3]for v in p['controls_xyzw']], radius=1., color_bytes=p['endpoint_rgba'][0],
                native_animation_input=p['native_animation_input'], native_random=p['native_random'])for p in system['points']]
    pc = dict(policy='private_native_slide_trail_gather_v1', entity_id=s.OWNER, particles_sha256=s.NATIVE_DRIVER_DEFAULTS['particles_sha256'],
              source_script_sha256=s.PROJECTION_HELPER_SHA, source_report_sha256=base['capture']['source']['report_sha256'], frames=pframes)
    projection = tmp_path/'projection.json';projection.write_text(json.dumps(reseal(dict(capture=pc))))
    uc = dict(policy='private_native_trail_upload_v1', entity_id=s.OWNER, particles_sha256=s.NATIVE_DRIVER_DEFAULTS['particles_sha256'],
              source_script_sha256=s.UPLOAD_HELPER_SHA, source_projection_sha256=hashlib.sha256(projection.read_bytes()).hexdigest(), frames=frames)
    upload = tmp_path/'upload.json';upload.write_text(json.dumps(reseal(dict(capture=uc))))
    layout = tmp_path/'layout.json';layout.write_bytes(b'test layout')
    monkeypatch.setattr(s, 'LAYOUT_SHA', hashlib.sha256(layout.read_bytes()).hexdigest())
    for upload_helper, projection_helper in sorted(s.QUALIFIED_HELPER_PAIRS):
        pc['source_script_sha256'] = projection_helper
        projection.write_text(json.dumps(reseal(dict(capture=pc))))
        uc['source_script_sha256'] = upload_helper
        uc['source_projection_sha256'] = hashlib.sha256(projection.read_bytes()).hexdigest()
        upload.write_text(json.dumps(reseal(dict(capture=uc))))
        rebuilt = s.build_reference(data, base, upload, projection, layout)
        assert rebuilt['capture']['source']['upload_helper_sha256'] == upload_helper
        assert rebuilt['capture']['source']['projection_helper_sha256'] == projection_helper
    uc['source_projection_sha256'] = 'c'*64;upload.write_text(json.dumps(reseal(dict(capture=uc))))
    with pytest.raises(ValueError, match='provenance'):s.build_reference(data, base, upload, projection, layout)
    uc['source_projection_sha256'] = hashlib.sha256(projection.read_bytes()).hexdigest()
    pc['source_report_sha256'] = 'c'*64;projection.write_text(json.dumps(reseal(dict(capture=pc))))
    uc['source_projection_sha256'] = hashlib.sha256(projection.read_bytes()).hexdigest();upload.write_text(json.dumps(reseal(dict(capture=uc))))
    with pytest.raises(ValueError, match='provenance'):s.build_reference(data, base, upload, projection, layout)
