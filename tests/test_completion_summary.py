"""Distinguish failed stages from partial supported content in the GUI worker."""
import json
import queue
import sys
import threading

import pytest

from agr.coverage_diagnostics import scene_completion_message, scene_coverage_warnings


def latest_shape(map_status='failed'):
    return {'status':'converted_with_warnings',
            'particles':{'status':'partial','objects':160,'layers':54,'unsupported_occurrences':99454},
            'map':{'status':map_status},'warnings':['coverage limitation']}


def test_latest_failure_summary_names_map_without_marking_working_particles_failed():
    summary=scene_completion_message(latest_shape())
    assert summary=='Scene saved; map failed. See the coverage report.'


def test_successful_map_and_partial_effects_are_explicit_supported_content():
    summary=scene_completion_message(latest_shape('imported'))
    assert 'map and supported effects imported' in summary
    assert 'Additional effect layers remain unsupported' in summary
    assert 'failed' not in summary
    warning=scene_coverage_warnings(latest_shape('imported'))[0]
    assert 'earlier replay history' in warning and 'not the number of effects missing from the selected clip' in warning


def test_partial_character_export_names_missing_skeletal_animation_before_optional_content():
    report = dict(coverage={'skeletal_animation': {'evaluated_entities': 1, 'unsupported_entities': 1}},
        blender={'entities': [
            {'id': '1:1', 'hero_name': 'Bebop', 'pose': {'status': 'baked'}},
            {'id': '2:1', 'hero_name': 'Seven', 'pose': {'status': 'unsupported'}}]},
        particles={'status': 'partial', 'objects': 3}, warnings=['missing pose'])
    summary = scene_completion_message(report)
    assert summary == 'Scene saved; skeletal animation unavailable for Seven [2:1]. See the coverage report.'
    assert 'Bebop' not in summary


def test_model_reset_summary_names_only_the_hidden_sample_without_claiming_whole_actor_missing():
    report=dict(coverage={'skeletal_animation':{'unsupported_entities':1}},blender={'entities':[
        dict(id='7:2',hero_name='Silver',pose=dict(status='baked')),
        dict(id='7:2@model:12',hero_name='Silver',pose=dict(status='baked_partial',unavailable_pose_ticks=[12]))]})
    message=scene_completion_message(report)
    assert 'partial skeletal animation for Silver' in message
    assert '1 unavailable recorded pose samples hidden' in message
    assert 'skeletal animation unavailable for' not in message


def test_source_diffuse_completion_names_actual_material_application():
    report=dict(hero_lighting=dict(status='partial',applied_components=['diffuse'],material_count=6))
    assert 'Source character diffuse lighting applied to 6 materials.' in scene_completion_message(report)
    report['hero_lighting']['applied_components']=[]
    assert 'lighting applied' not in scene_completion_message(report)


def test_preserved_unlit_surface_is_not_counted_as_applied_diffuse():
    report=dict(hero_lighting=dict(status='partial',applied_components=['diffuse'],material_count=2,
        material_routes=[dict(applied_components=['diffuse']),dict(applied_components=[])]))
    assert 'lighting applied to 1 material.' in scene_completion_message(report)


@pytest.mark.parametrize('exit_code', [0,3])
def test_gui_worker_reads_summary_only_after_successful_export(tmp_path,exit_code):
    from agr.gui import AGRApp
    report=tmp_path/'scene.agr-report.json';report.write_text(json.dumps(latest_shape()))
    app=AGRApp.__new__(AGRApp)
    app.events=queue.Queue();app.cancel_requested=threading.Event()
    app.termination_done=threading.Event();app.termination_done.set()
    app.process_lock=threading.Lock();app.process=None
    app._run([sys.executable,'-c',f'raise SystemExit({exit_code})'],'convert',report)
    finished=[payload for kind,payload in list(app.events.queue) if kind=='finished']
    assert len(finished)==1
    operation,code,summary=finished[0]
    assert operation=='convert' and code==exit_code
    assert summary==(scene_completion_message(latest_shape()) if exit_code==0 else '')
