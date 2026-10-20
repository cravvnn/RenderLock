"""Coverage follows successful Blender bakes, not preparation intent."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agr import cli
from agr.coverage_diagnostics import (merge_secondary_motion_coverage,
                                      scene_completion_message, scene_coverage_warnings)
from agr.scene_package import read_scene, save_scene
from blender_fixture import write_blender_completion


def preparation():
    return {'status': 'prepared_partial', 'scope': 'Supported secondary motion',
            'supported_entities': [
                {'id': '13:1', 'hero_name': 'Haze', 'status': 'ready_to_bake', 'dynamic_controls': 4},
                {'id': '2:1', 'hero_name': 'Seven', 'status': 'ready_to_bake', 'dynamic_controls': 20}],
            'unsupported_entities': [
                {'id': '15:1', 'hero_name': 'Bebop', 'status': 'unsupported', 'reason': 'Garment topology is unsupported'}]}


def blender_rows(haze_status='baked', seven_status='baked'):
    return [{'id': '13:1', 'hero_name': 'Haze', 'secondary_motion': {
                'status': haze_status, **({'dynamic_controls': ['a', 'b', 'c', 'd']} if haze_status == 'baked'
                    else {'error': 'Body collision acceptance failed', 'diagnostics': {'penetration_m': .012}})}},
            {'id': '2:1', 'hero_name': 'Seven', 'secondary_motion': {
                'status': seven_status, **({'dynamic_control_count': 20} if seven_status == 'baked'
                    else {'error': 'Cloth bake rejected'})}}]


def test_prepared_definitions_are_counted_only_after_successful_bakes():
    prep, rows = preparation(), blender_rows()
    originals = deepcopy((prep, rows))
    result = merge_secondary_motion_coverage(prep, rows)
    assert result['status'] == 'partial'
    assert result['baked_entity_count'] == 2
    assert result['failed_entity_count'] == 0
    assert result['unsupported_entity_count'] == 1
    assert result['dynamic_control_count'] == 24
    assert result['unsupported_entities'][0]['reason'] == 'Garment topology is unsupported'
    assert (prep, rows) == originals
    warnings = scene_coverage_warnings({'secondary_motion': result})
    assert len(warnings) == 1 and 'Bebop' in warnings[0]
    assert 'failed' not in warnings[0]
    summary = scene_completion_message({'secondary_motion': result})
    assert 'Cloth baked for 2 entities' in summary and 'unsupported cloth' in summary


def test_withheld_bake_is_prominent_even_when_another_hero_succeeds():
    result = merge_secondary_motion_coverage(preparation(), blender_rows(haze_status='failed'))
    assert result['status'] == 'partial'
    assert result['baked_entity_count'] == result['failed_entity_count'] == 1
    assert result['dynamic_control_count'] == 20
    assert result['failed_entities'][0]['diagnostics']['penetration_m'] == .012
    warning = scene_coverage_warnings({'secondary_motion': result})[0]
    assert 'Withheld cloth bakes: Haze [13:1]: Body collision acceptance failed' in warning
    summary = scene_completion_message({'secondary_motion': result, 'particles': {'objects': 160, 'status': 'partial'}})
    assert 'cloth failed for Haze [13:1]' in summary
    assert 'particles failed' not in summary


def test_missing_or_all_failed_blender_results_cannot_remain_prepared():
    for rows in ([], blender_rows(haze_status='failed', seven_status='failed')):
        result = merge_secondary_motion_coverage(preparation(), rows)
        assert result['status'] == 'failed'
        assert result['baked_entity_count'] == result['dynamic_control_count'] == 0
        assert result['failed_entity_count'] == 2
        assert all(row['error'] for row in result['failed_entities'])
        assert 'Haze' in scene_completion_message({'secondary_motion': result})
        assert 'Seven' in scene_completion_message({'secondary_motion': result})


def test_disabled_and_unsupported_selections_are_distinct_and_zero_counted():
    disabled = merge_secondary_motion_coverage({'status': 'not_requested'}, [])
    assert disabled['status'] == 'not_requested'
    assert scene_coverage_warnings({'secondary_motion': disabled}) == []
    unsupported = merge_secondary_motion_coverage({
        'status': 'no_supported_entities', 'supported_entities': [],
        'unsupported_entities': preparation()['unsupported_entities']}, [])
    assert unsupported['status'] == 'no_supported_entities'
    assert unsupported['baked_entity_count'] == unsupported['dynamic_control_count'] == 0
    assert unsupported['unsupported_entity_count'] == 1
    assert 'Bebop' in scene_coverage_warnings({'secondary_motion': unsupported})[0]
    assert 'failed' not in scene_completion_message({'secondary_motion': unsupported})


def test_approximate_complete_garment_does_not_claim_missing_cloth_parts():
    render = dict(surface_bake(), approximation_only=True, unsupported_parts=[])
    result = merge_secondary_motion_coverage(None, [
        {'id':'8:1','hero_name':'McGinnis','secondary_motion':render}])
    assert result['status'] == 'partial' and result['baked_entity_count'] == 1
    warning = scene_coverage_warnings({'secondary_motion':result})[0]
    assert 'Approximate cloth simulation' in warning
    assert 'Additional cloth parts remain unsupported' not in warning
    assert 'simulation is approximate' in scene_completion_message({'secondary_motion':result})
    assert 'Additional cloth parts remain unsupported' not in scene_completion_message({'secondary_motion':result})


def test_all_supported_bakes_and_actual_saved_package_bakes_are_recognized():
    prep = preparation()
    prep['unsupported_entities'] = []
    prep['status'] = 'prepared'
    result = merge_secondary_motion_coverage(prep, blender_rows())
    assert result['status'] == 'baked' and result['dynamic_control_count'] == 24
    assert scene_coverage_warnings({'secondary_motion': result}) == []
    # Older saved manifests may lack preparation coverage but retain bake inputs.
    result = merge_secondary_motion_coverage(None, blender_rows())
    assert result['status'] == 'baked' and result['dynamic_control_count'] == 24


def test_preparation_failure_preserves_original_error():
    result = merge_secondary_motion_coverage({'status': 'failed', 'error': 'PHYS decode failed',
                                              'error_type': 'ConversionError'}, [])
    assert result['status'] == 'failed'
    assert result['error'] == 'PHYS decode failed'
    assert result['error_type'] == 'ConversionError'
    assert 'PHYS decode failed' in scene_coverage_warnings({'secondary_motion': result})[0]


@pytest.mark.parametrize('flag', ['--cloth', '--secondary-motion'])
def test_cli_cloth_alias_and_legacy_option_share_the_same_setting(flag):
    parsed = cli.parser().parse_args(['convert', '--segment', 'scene.json', '--output', 'scene.blend', flag])
    assert parsed.secondary_motion


def test_cli_publishes_actual_per_hero_cloth_outcomes_and_external_map_scope(tmp_path, monkeypatch):
    monkeypatch.setattr('agr.blender_runtime.check_blender', lambda *a, **k: {'fixture': True})
    from agr import installed_assets
    assets = tmp_path / 'assets'
    assets.mkdir()
    model = assets / 'hero.blend'
    model.write_bytes(b'authored stub; never imported')
    engine = tmp_path / 'blender.exe'
    engine.write_bytes(b'authored stub; never executed')
    scene = {'schema_version': 1, 'tick_rate': 64, 'start_tick': 10, 'end_tick': 10,
             'metadata': {}, 'markers': [], 'entities': [
                 {'id': row['id'], 'hero_name': row['hero_name'], 'model_file': str(model),
                  'samples': [{'tick': 10, 'position': [0, 0, 0], 'body_angles': [0, 0, 0]}]}
                 for row in preparation()['supported_entities'] + preparation()['unsupported_entities']]}
    manifest = save_scene(scene, tmp_path / 'input.json')
    monkeypatch.setattr(cli, 'resolve_asset_source', lambda args: {
        'install_path': tmp_path / 'game', 'cache_dir': tmp_path / 'cache', 'converter': engine, 'map_name': None})
    seen = []
    def prepare(**kwargs):
        seen.append(kwargs)
        data = read_scene(kwargs['segment_path'])
        data['coverage'] = {'scene_extras': {'secondary_motion': preparation()}}
        supported={row['id'] for row in preparation()['supported_entities']}
        for entity in data['entities']:
            if entity['id'] in supported:
                definition=tmp_path/('cloth-'+entity['id'].replace(':','-')+'.json')
                definition.write_text('{}')
                entity['secondary_motion_file']=str(definition)
        save_scene(data, kwargs['segment_path'])
        return assets
    monkeypatch.setattr(installed_assets, 'prepare_installed_assets', prepare)
    def render(command, check):
        result = write_blender_completion(command,
            effects={'status': 'partial', 'objects': 160, 'unsupported': [{'reason': 'unsupported layer'}]})
        cloth = {row['id']: row['secondary_motion'] for row in blender_rows(haze_status='failed')}
        for row in result['entities']:
            if row['id'] in cloth:
                row['secondary_motion'] = cloth[row['id']]
        Path(command[command.index('--output')+1]).with_suffix('.scene.json').write_text(json.dumps(result))
    monkeypatch.setattr(cli.subprocess, 'run', render)
    output = tmp_path / 'output.blend'
    args = cli.parser().parse_args(['convert', '--segment', str(manifest), '--output', str(output),
                                   '--blender', str(engine), '--motion-only', '--cloth','--partial'])
    report = cli.convert(args)
    assert seen[0]['secondary_motion'] and not seen[0]['include_map']
    assert report['secondary_motion']['failed_entity_count'] == 1
    assert report['secondary_motion']['baked_entity_count'] == 1
    assert report['secondary_motion']['dynamic_control_count'] == 20
    assert report['status'] == 'converted_with_warnings'
    assert 'cloth failed for Haze' in report['summary']
    assert report['particles']['objects'] == 160
    assert report['map_workflow'] == 'external_blend'
    assert report['death_workflow'] == 'user_authored_animation'
    assert report['ragdoll'] == 'outside_current_scope'
    saved = json.loads(output.with_suffix('.agr-report.json').read_text())
    assert saved == report and output.is_file()


def test_partial_garment_components_count_only_accepted_controls_without_failing_the_hero():
    prep = {'status': 'prepared', 'supported_entities': [
        {'id': '2:1', 'hero_name': 'Seven', 'status': 'ready_to_bake', 'dynamic_controls': 34}],
        'unsupported_entities': []}
    components = [{'status': 'accepted', 'controls': ['accepted_'+str(n) for n in range(27)]},
                  {'status': 'withheld', 'controls': ['retained_'+str(n) for n in range(7)],
                   'reason': 'Collision or distance acceptance failed'}]
    render = {'status': 'baked_partial', 'dynamic_controls': components[0]['controls'],
              'dynamic_control_count': 27, 'withheld_controls': components[1]['controls'],
              'component_coverage': components}
    result = merge_secondary_motion_coverage(prep, [{'id': '2:1', 'hero_name': 'Seven',
                                                   'secondary_motion': render}])
    assert result['status'] == 'partial'
    assert result['baked_entity_count'] == result['partial_entity_count'] == 1
    assert result['failed_entity_count'] == result['unsupported_entity_count'] == 0
    assert result['dynamic_control_count'] == 27
    assert result['withheld_control_count'] == 7
    assert result['partially_baked_entities'][0]['component_coverage'] == components
    warnings = scene_coverage_warnings({'secondary_motion': result})
    assert len(warnings) == 1
    assert 'Seven [2:1] (7 controls)' in warnings[0]
    assert 'retained their recorded motion' in warnings[0]
    summary = scene_completion_message({'secondary_motion': result})
    assert 'Cloth baked for 1 entity' in summary
    assert 'Some cloth controls retained their recorded motion; see coverage.' in summary
    assert 'failed' not in summary


def surface_bake():
    return {'status': 'baked_partial', 'scope': 'Seven waist only',
            'simulation_vertex_count': 240, 'affected_render_vertex_count': 480,
            'pinned_vertex_count': 132, 'dynamic_controls': [], 'dynamic_control_count': 0,
            'unsupported_parts': ['sleeves', 'straps']}


def test_native_surface_cloth_counts_vertices_without_claiming_bone_simulation():
    prep = {'status': 'prepared', 'supported_entities': [
        {'id': '2:1', 'hero_name': 'Seven', 'status': 'ready_to_bake'}], 'unsupported_entities': []}
    result = merge_secondary_motion_coverage(prep, [{'id': '2:1', 'hero_name': 'Seven',
                                                   'secondary_motion': surface_bake()}])
    assert result['status'] == 'partial'
    assert result['baked_entity_count'] == result['partial_entity_count'] == 1
    assert result['failed_entity_count'] == result['dynamic_control_count'] == 0
    assert result['simulation_vertex_count'] == 240
    assert result['affected_render_vertex_count'] == 480
    assert result['pinned_vertex_count'] == 132
    assert result['withheld_control_count'] == 0
    warning = scene_coverage_warnings({'secondary_motion': result})[0]
    assert '240 simulation vertices' in warning and '480 affected render vertices' in warning
    assert 'Seven [2:1]: sleeves, straps' in warning
    summary = scene_completion_message({'secondary_motion': result})
    assert 'Additional cloth parts remain unsupported; see coverage.' in summary
    assert 'controls retained' not in summary and 'dynamic controls' not in warning
    assert 'failed' not in summary


def test_failed_surface_bakes_do_not_inflate_successful_vertex_counts():
    successful = surface_bake()
    failed = dict(surface_bake(), status='failed', error='Surface acceptance failed')
    result = merge_secondary_motion_coverage(None, [
        {'id': '2:1', 'hero_name': 'Seven', 'secondary_motion': successful},
        {'id': '2:2', 'hero_name': 'Seven', 'secondary_motion': failed}])
    assert result['baked_entity_count'] == result['failed_entity_count'] == 1
    assert result['simulation_vertex_count'] == 240
    assert result['affected_render_vertex_count'] == 480
    assert result['pinned_vertex_count'] == 132
    assert result['dynamic_control_count'] == 0
    assert 'cloth failed for Seven [2:2]' in scene_completion_message({'secondary_motion': result})
