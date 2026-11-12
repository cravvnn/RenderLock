import copy
import pytest
from agr.particle_rope_inputs import resolve_rope_inputs
from agr.installed_effects import UnsupportedEffect


def renderer():
    return dict(m_flTextureVWorldSize=dict(m_nType='PF_TYPE_RANDOM_UNIFORM',m_flRandomMin=300,m_flRandomMax=500))


def test_rope_random_worldsize_is_stable_per_sibling_without_mutating_definition():
    r=renderer();before=copy.deepcopy(r)
    a,notes=resolve_rope_inputs(r,None,'root/0/0/0',123)
    b,_=resolve_rope_inputs(r,None,'root/0/0/0',123)
    c,_=resolve_rope_inputs(r,None,'root/1/0/0',123)
    assert a==b and a!=c and r==before
    assert 300<=a['m_flTextureVWorldSize']<=500
    assert 'substitute seed' in notes[0]


@pytest.mark.parametrize('update',[
    dict(m_nRandomMode='PF_RANDOM_MODE_VARYING'),dict(m_nRandomSeed=7),
    dict(m_bHasRandomSignFlip=True),dict(m_flRandomMin=600),dict(m_flRandomMax=float('nan')),
    dict(m_nMapType='PF_MAP_TYPE_CURVE')])
def test_unqualified_rope_random_modes_are_rejected(update):
    r=renderer();r['m_flTextureVWorldSize'].update(update)
    with pytest.raises(UnsupportedEffect):resolve_rope_inputs(r,None,'root',123)


def test_random_radius_is_not_whitelisted_by_texture_support():
    r=renderer();r['m_flRadiusScale']=r.pop('m_flTextureVWorldSize')
    with pytest.raises(UnsupportedEffect):resolve_rope_inputs(r,None,'root',123)


def test_rope_color_uses_the_current_system_cp_snapshot():
    r=dict(m_vecColorScale=dict(m_nType='PVEC_TYPE_CP_VALUE',m_nControlPoint=10,m_vCPValueScale=[2,3,4]))
    a,_=resolve_rope_inputs(r,dict(points={10:[.5,.25,.125]}),'root/0',123)
    b,_=resolve_rope_inputs(r,dict(points={10:[1,1,1]}),'root/1',123)
    assert a['m_vecColorScale']['m_vLiteralValue']==[1,.75,.5]
    assert b['m_vecColorScale']['m_vLiteralValue']==[2,3,4]
    with pytest.raises(UnsupportedEffect):resolve_rope_inputs(r,dict(points={0:[0,0,0]}),'root',123)
