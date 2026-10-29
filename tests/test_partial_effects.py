import copy
import json

import pytest

from agr.particle_diagnostics import CAPABILITY_WARNING, simulation_diagnostics
from agr.simulated_effect_package import validate_package
from test_simulated_effect_package import fixture
from test_blender_completion_stages import completion
from test_export_save_failures import export_job


def test_warning_requires_fresh_consent_and_is_preserved():
    simulation=dict(warnings=['1:1: '+CAPABILITY_WARNING],instances=[dict(id='1:1')])
    with pytest.raises(ValueError,match='unresolved diagnostics'):
        simulation_diagnostics(simulation)
    before=copy.deepcopy(simulation)
    result=simulation_diagnostics(simulation,allow_partial_effects=True)
    assert simulation==before
    assert result['status']=='partial' and result['warnings']==simulation['warnings']
    assert result['omitted_operator_classes']==['C_OP_SetControlPointFieldToScalarExpression']


def test_actual_helper_resource_suffix_is_bound_to_the_same_simulated_tree():
    sim=dict(warnings=['1:1: '+CAPABILITY_WARNING+' particles/muzzle.vpcf_c'],
             instances=[dict(id='1:1',resource='particles/muzzle.vpcf',frames=[])])
    assert simulation_diagnostics(sim,allow_partial_effects=True)['status']=='partial'
    sim['warnings'][0]='1:1: '+CAPABILITY_WARNING+' particles/unrelated.vpcf_c'
    with pytest.raises(ValueError,match='unresolved diagnostics'):
        simulation_diagnostics(sim,allow_partial_effects=True)


@pytest.mark.parametrize('warning',['Unsupported unknown operator', 'NaN in simulation',
    'other: '+CAPABILITY_WARNING, CAPABILITY_WARNING+'; invalid control points'])
def test_partial_consent_does_not_clear_other_or_unbound_diagnostics(warning):
    with pytest.raises(ValueError,match='unresolved diagnostics'):
        simulation_diagnostics(dict(warnings=[warning],instances=[dict(id='1:1')]),allow_partial_effects=True)


def test_saved_package_cannot_grant_its_own_consent_or_bypass_identity(fixture):
    scene,package=fixture
    package['simulation']['warnings']=['1:1: '+CAPABILITY_WARNING]
    package['partial_effects']=True
    with pytest.raises(ValueError,match='unresolved diagnostics'):validate_package(package,scene)
    validate_package(package,scene,allow_partial_effects=True)
    package['scene_identity']='other replay'
    with pytest.raises(ValueError,match='do not match'):
        validate_package(package,scene,allow_partial_effects=True)


@pytest.mark.parametrize('change',['missing_tick','control_point','unknown_warning'])
def test_partial_operator_consent_keeps_other_package_checks_strict(fixture,change):
    scene,package=fixture
    sim=package['simulation'];sim['warnings']=[CAPABILITY_WARNING]
    if change=='missing_tick':sim['instances'][0]['frames'].pop(1)
    elif change=='control_point':sim['instances'][0].update(control_point_indices=[0],materialized_control_point_indices=[0,7])
    else:sim['warnings'].append('unknown issue')
    with pytest.raises(ValueError):validate_package(package,scene,allow_partial_effects=True)


def test_recorded_retry_removes_simulation_without_relaxing_animation():
    from agr.export_recovery import recovery_choices
    command=['python','-m','agr','convert','--particle-probe=probe.dll','--dotnet=dotnet',
             '--particle-history-ticks','1280','--heroes','10','--start-tick','82058','--end-tick','82250',
             '--cloth','--source-lighting','--partial-effects']
    failure=dict(stage='validate_blender_completion',error='Blender did not complete requested effects: unavailable; the scene was not published.')
    choices=recovery_choices(command,failure)
    assert [row.id for row in choices]==['recorded_effects','without_effects']
    retry=choices[0].command
    assert all(not any(token.startswith(flag) for token in retry)
               for flag in ('--particle-probe','--dotnet','--particle-history-ticks','--partial-effects'))
    assert '--no-effects' not in retry and '--partial' not in retry
    assert retry[-8:]==('--heroes','10','--start-tick','82058','--end-tick','82250','--cloth','--source-lighting')
    fresh=recovery_choices(command[:-1],failure)
    assert [row.id for row in fresh]==['recorded_effects','partial_effects','without_effects']
    assert fresh[1].command==tuple(command)


@pytest.mark.parametrize('status',['unavailable','failed'])
def test_effects_only_consent_cannot_accept_failed_stages_or_leak_to_animation(tmp_path,status):
    from agr.cli import read_blender_completion
    result=dict(status=status,backend='recorded_simulation_v1',partial_effects_consent=True,objects=0)
    path,job=completion(tmp_path,effects=result)
    if status=='unavailable':
        assert read_blender_completion(path,**job,requested_stages=['effects'],partial_effects=True)
    else:
        with pytest.raises(RuntimeError,match='did not complete requested effects'):
            read_blender_completion(path,**job,requested_stages=['effects'],partial_effects=True)
    with pytest.raises(RuntimeError):read_blender_completion(path,**job,requested_stages=['effects'])


def test_completion_cannot_accept_omitted_operators_without_request(tmp_path):
    from agr.cli import read_blender_completion
    path,job=completion(tmp_path,effects=dict(status='partial',accepted_simulator_diagnostics=[dict(warnings=[CAPABILITY_WARNING])]))
    with pytest.raises(RuntimeError,match='explicit --partial-effects consent'):
        read_blender_completion(path,**job,requested_stages=['effects'])


def test_partial_effects_without_a_simulator_cannot_change_export_policy(export_job):
    from agr.cli import convert
    args,_=export_job
    args.partial_effects=True
    with pytest.raises(ValueError,match='--partial-effects requires --particle-probe'):
        convert(args)
    assert not args.output.exists()


def test_summary_does_not_call_warning_accepted_simulation_fully_supported():
    from agr.coverage_diagnostics import scene_completion_message
    summary=scene_completion_message(dict(particles=dict(status='partial',objects=1,
        accepted_simulator_diagnostics=[dict(warnings=[CAPABILITY_WARNING])]),warnings=['incomplete']))
    assert 'approximate effects imported' in summary and 'operators were omitted' in summary


def test_recorded_fallback_requires_consent_and_zero_simulated_objects(monkeypatch):
    from types import SimpleNamespace
    from agr.recorded_effect_simulation import retain_recorded_fallback
    from agr import blender_effects
    scene=SimpleNamespace(render=SimpleNamespace(fps=64,fps_base=1))
    data=dict(effects=dict(recorded_fallback={'manifest_file':'recorded.json'}))
    original=dict(status='unavailable',objects=0,limitations=['simulation limit'])
    calls=[]
    def render(selected,*args):
        calls.append(selected['effects'])
        return dict(status='partial',objects=4,layers=1)
    monkeypatch.setattr(blender_effects,'build_effects',render)
    assert retain_recorded_fallback(original,data,None,scene,None,{}) is original
    assert not calls
    working=dict(original,status='partial',objects=2)
    assert retain_recorded_fallback(working,data,None,scene,None,{},allow_partial_effects=True) is working
    assert not calls
    result=retain_recorded_fallback(original,data,None,scene,None,{},allow_partial_effects=True)
    assert calls==[data['effects']['recorded_fallback']]
    assert result['objects']==4 and result['backend']=='recorded_layers_fallback_v1'
    assert result['simulation_attempt'] is original and original['objects']==0


def test_recorded_preparation_pruning_cannot_erase_simulator_instances(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from agr.scene_extensions import prepare_scene_extensions
    from agr import installed_effects
    from test_scene_extensions import scene
    original=scene();original['particle_instances']=[dict(id='old',resource='particles/test.vpcf')]
    segment=tmp_path/'segment.json';segment.write_text(json.dumps(original))
    calls=[]
    def prepare(path,folder,*args,attachments_only=False,**kwargs):
        calls.append(attachments_only)
        selected=json.loads(path.read_text())
        if not attachments_only:selected['particle_instances']=[]
        manifest=folder/'manifest.json';manifest.write_text('{}')
        selected['effects']=dict(manifest_file=str(manifest),status='prepared')
        path.write_text(json.dumps(selected))
        return dict(status='prepared',prepared_layer_count=0 if attachments_only else 1)
    monkeypatch.setattr(installed_effects,'prepare_installed_effects',prepare)
    prepare_scene_extensions(segment,tmp_path,SimpleNamespace(resources={}),object(),{},
        include_effects=True,simulate_effects=True,recorded_effect_fallback=True,log=lambda _:None)
    selected=json.loads(segment.read_text())
    assert calls==[True,False] and selected['particle_instances']==original['particle_instances']
    assert selected['entities']==original['entities']
    assert 'recorded_fallback' in selected['effects']
