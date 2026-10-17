import copy
import json
from types import SimpleNamespace

import pytest

from agr.coverage_diagnostics import (effect_preparation_status, merge_stage_coverage,
                                      scene_coverage_warnings)
from agr.installed_effects import prepare_installed_effects


def unresolved_preparation():
    return effect_preparation_status({
        'network_instances': 63525, 'resolved_resource_instances': 0,
        'unresolved_resource_instances': 63525, 'prepared_layer_count': 0,
        'prepared_particle_count': 0, 'unsupported_occurrences': 63525,
        'unsupported': [
            {'resource': None, 'reason': 'Particle resource ID unresolved', 'occurrences': 30082},
            {'resource': None, 'reason': 'No recorded control-point 0 entity attachment', 'occurrences': 33443}],
        'limitations': ['Only supported layers are rendered.']})


def test_uploaded_zero_layer_shape_preserves_preparation_diagnostics():
    prep = unresolved_preparation()
    before = copy.deepcopy(prep)
    render = {'status': 'partial', 'objects': 0, 'layers': 0, 'unsupported': [], 'materials': 0}
    result = merge_stage_coverage(prep, render)
    assert result['status'] == 'unavailable'
    assert result['stage'] == 'preparation'
    assert result['reason_code'] == 'no_resolved_particle_resources'
    assert result['unsupported_occurrences'] == 63525
    assert len(result['unsupported']) == 2
    assert all(i['stage'] == 'preparation' for i in result['unsupported'])
    assert result['objects'] == result['layers'] == 0
    assert prep == before
    assert merge_stage_coverage(prep, result) == result


def test_render_failure_retains_its_error_and_preparation_omissions():
    prep = {'status': 'partial', 'prepared_layer_count': 3,
            'unsupported': [{'resource': 'particles/beam.vpcf', 'reason': 'Unsupported beam'}]}
    render = {'status': 'failed', 'error': 'Texture image is missing', 'error_type': 'FileNotFoundError'}
    result = merge_stage_coverage(prep, render)
    assert result['status'] == 'failed' and result['stage'] == 'rendering'
    assert result['error'] == render['error']
    assert result['preparation']['prepared_layer_count'] == 3
    assert result['unsupported'][0]['resource'] == 'particles/beam.vpcf'


def test_skipped_blender_stage_does_not_hide_map_preparation_failure():
    prep = {'status': 'failed', 'error': 'World conversion failed', 'error_type': 'ConversionError'}
    result = merge_stage_coverage(prep, {'status': 'not_requested'})
    assert result == prep
    warnings = scene_coverage_warnings({'map': result})
    assert warnings == ['Map preparation failed: World conversion failed']


def test_successful_map_remains_a_structured_import_result():
    result = merge_stage_coverage({'status': 'ready', 'model_file': 'region.gltf'},
                                 {'status': 'imported', 'meshes': 7, 'collision': False})
    assert result['status'] == 'imported' and result['meshes'] == 7
    assert result['model_file'] == 'region.gltf' and result['collision'] is False
    assert scene_coverage_warnings({'map': result}) == []


def test_zero_recorded_events_are_not_a_warning_or_failure(tmp_path):
    path = tmp_path / 'segment.json'
    path.write_text(json.dumps({'entities': [], 'particle_instances': []}))
    logs = []
    result = prepare_installed_effects(path, tmp_path, SimpleNamespace(resources=[]), None, log=logs.append)
    assert result['status'] == 'no_events'
    assert result['reason_code'] == 'no_recorded_particle_instances'
    assert not any(s.startswith('WARNING:') for s in logs)
    assert scene_coverage_warnings({'particles': result}) == []
    manifest = json.loads((tmp_path / 'effects/effects_manifest.json').read_text())
    assert manifest['layers'] == []
    assert manifest['coverage']['status'] == 'no_events'


@pytest.mark.parametrize('resource,reason_code', [
    (None, 'no_resolved_particle_resources'),
    ('particles/new_operator.vpcf', 'no_supported_particle_layers')])
def test_unavailable_effects_have_a_reason_in_the_report_and_manifest(tmp_path, resource, reason_code):
    path = tmp_path / 'segment.json'
    scene = {'start_tick': 10, 'end_tick': 20, 'tick_rate': 64,
             'entities': [{'id': '3:5', 'model_handle': 91}],
             'particle_instances': [{'id': '1:1', 'created_tick': 10, 'resource': resource, 'events': []}]}
    path.write_text(json.dumps(scene))
    result = prepare_installed_effects(path, tmp_path, SimpleNamespace(resources=[]), None, log=lambda _: None)
    assert result['status'] == 'unavailable' and result['reason_code'] == reason_code
    assert result['prepared_layer_count'] == 0
    manifest = json.loads((tmp_path / 'effects/effects_manifest.json').read_text())
    assert manifest['coverage']['reason_code'] == reason_code
    assert manifest['coverage']['unsupported']
    saved = json.loads(path.read_text())
    assert saved['entities'] == scene['entities']
    assert saved['particle_instances'] == scene['particle_instances']


def test_rendering_omissions_warn_even_if_preparation_succeeded():
    prep = {'status': 'prepared_supported_subset', 'unsupported': []}
    render = {'status': 'partial', 'objects': 5,
              'unsupported': [{'resource': 'particles/test.vpcf', 'reason': 'Attachment bone absent'}]}
    result = merge_stage_coverage(prep, render)
    warnings = scene_coverage_warnings({'particles': result, 'blender': {'warnings': []}})
    assert len(warnings) == 1
    assert 'Particles rendering partial' in warnings[0]
    assert result['unsupported'][0]['stage'] == 'rendering'


def test_particle_warning_distinguishes_birth_timing_from_missing_active_effects():
    from agr.coverage_diagnostics import recorded_birth_scope
    cases = [(9, 'before_recovered_history'), (10, 'in_recovered_history'),
             (19, 'in_recovered_history'), (20, 'in_output'), (30, 'in_output'),
             (31, 'after_output'), (True, 'unknown')]
    for birth, scope in cases:
        assert recorded_birth_scope({'created_tick': birth}, 20, 30, 10) == scope
    render = dict(status='partial', objects=110, unsupported_occurrences=682,
        unsupported_by_birth_scope=dict(in_output=2, in_recovered_history=1, before_recovered_history=679))
    merged = merge_stage_coverage(dict(status='prepared', unsupported=[]), render)
    warning = scene_coverage_warnings({'particles': merged})[0]
    assert '2 instances were born inside this clip' in warning
    assert '680 were born earlier' in warning
    assert 'does not establish' in warning


def test_blender_zero_layer_report_retains_root_resolution_failure(tmp_path):
    bpy = pytest.importorskip('bpy')
    from agr.blender_effects import build_effects
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prep = unresolved_preparation()
    manifest = {'schema_version': 1, 'layers': [], 'attachments': {}, 'textures': {},
                'limitations': [], 'coverage': prep}
    path = tmp_path / 'effects.json'
    path.write_text(json.dumps(manifest))
    data = {'start_tick': 10, 'end_tick': 20, 'tick_rate': 64,
            'effects': {'manifest_file': str(path)}}
    result = build_effects(data, tmp_path, bpy.context.scene, None, {}, 64)
    assert result['status'] == 'unavailable'
    assert result['reason_code'] == 'no_resolved_particle_resources'
    assert result['objects'] == 0 and result['unsupported_occurrences'] == 63525
    embedded = json.loads(bpy.context.scene['agr_effects_report'])
    assert embedded == result
    assert merge_stage_coverage(prep, result) == result
