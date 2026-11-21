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


@pytest.mark.parametrize('kind',['flag','copied_simulation','missing_birth'])
def test_no_bare_v4_flag_or_incomplete_context_authorizes_renderer_inputs(package,kind):
    value,scene=package;sim=snapshot_fixture(value);context=(value,scene)
    if kind=='flag':context=True
    if kind=='copied_simulation':sim=copy.deepcopy(sim)
    if kind=='missing_birth':value.pop('silence_birth_capture')
    with pytest.raises(ValueError):render_contexts(sim,silence_context=context)
