import copy

import pytest

from agr.particle_render_controls import render_contexts,SNAPSHOT_POLICY
from agr.installed_effects import UnsupportedEffect
from test_silence_package_admission import package
from test_particle_silence_capture import source


def snapshot_fixture(value):
    sim=value['simulation'];sim['control_point_snapshot_policy']=SNAPSHOT_POLICY
    for frame in sim['instances'][0]['frames']:
        frame['systems']=[dict(system_path='root',system_age=.25,control_points=[dict(index=n,position=[float(n),0,0])for n in range(5)])]
    return sim


def test_actual_root_policy_reproduces_refusal_without_complete_package_context(package):
    value,scene=package;sim=snapshot_fixture(value)
    with pytest.raises(UnsupportedEffect,match='provenance'):render_contexts(sim)
    contexts=render_contexts(sim,silence_context=(value,scene))
    assert len(contexts)==3
    assert contexts[('5718:1','root',10)]['points'][4]==[4.,0.,0.]


@pytest.mark.parametrize('kind',['flag','copied_simulation','missing_child','missing_birth','definition',
    'missing_root','unexpected_point','bool_point','wrong_resource'])
def test_no_bare_v4_flag_or_incomplete_context_authorizes_renderer_inputs(package,kind):
    value,scene=package;sim=snapshot_fixture(value);context=(value,scene)
    if kind=='flag':context=True
    if kind=='copied_simulation':sim=copy.deepcopy(sim)
    if kind=='missing_child':value.pop('client_child_capture')
    if kind=='missing_birth':value.pop('silence_birth_capture')
    if kind=='definition':value['assets']['definitions'].pop(next(iter(value['assets']['definitions'])))
    if kind=='missing_root':sim['instances'][0]['authored_root_position_indices']=[3]
    if kind=='unexpected_point':sim['instances'][0]['frames'][0]['systems'][0]['control_points'].append(dict(index=5,position=[0,0,0]))
    if kind=='bool_point':sim['instances'][0]['frames'][0]['systems'][0]['control_points'][0]['index']=False
    if kind=='wrong_resource':sim['instances'][0]['resource']='particles/other.vpcf'
    with pytest.raises(ValueError):render_contexts(sim,silence_context=context)
