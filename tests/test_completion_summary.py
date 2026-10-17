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
