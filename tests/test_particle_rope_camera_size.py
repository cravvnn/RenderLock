import copy
import numpy as np
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_rope_geometry import reference_camera_size,build_rope_geometry
from agr.particle_rope_inputs import resolve_rope_inputs

def r(**kw):return dict(m_bEnableFadingAndClamping=True,m_flStartFadeSize=.15,m_flEndFadeSize=.3,**kw)
def response(radius,distance,renderer=None):
    return reference_camera_size(np.float32(radius),np.float32(.75),np.zeros(3,dtype=np.float32),np.array([0,0,distance],dtype=np.float32),r()if renderer is None else renderer)

def test_reference_fades_before_clamping_and_never_changes_disabled_values():
    radius,alpha=response(2,10,r(m_flMinSize=.25,m_flMaxSize=.5))
    assert radius==2.5 and alpha==pytest.approx(.5)
    assert response(1,10)==(1,.75)
    assert response(3,10)==(3,0)
    assert response(4,10)==(4,0)
    assert response(2,10,dict(m_bEnableFadingAndClamping=False,m_flStartFadeSize=-1))==(2,.75)

def test_reference_zero_distance_and_equal_slopes_do_not_divide_by_zero():
    assert response(2,0)==(0,.75)
    radius,alpha=response(2,10,dict(m_bEnableFadingAndClamping=True,m_flStartFadeSize=.2,m_flEndFadeSize=.2))
    assert radius==2 and alpha==.75

@pytest.mark.parametrize('changes',[dict(m_bEnableFadingAndClamping=1),dict(m_flMinSize=-1),dict(m_flMinSize=3,m_flMaxSize=2),dict(m_flStartFadeSize=float('nan'))])
def test_invalid_camera_parameters_are_refused(changes):
    renderer=r();renderer.update(changes)
    with pytest.raises(UnsupportedEffect):response(2,10,renderer)

def test_geometry_applies_size_before_splining_without_editing_particle_state():
    particles=[dict(id=i,position=[i*10,0,0],normal=[0,0,1],radius=2,color=[1,1,1],alpha=1,alpha_alternate=.75)for i in range(2)]
    before=copy.deepcopy(particles)
    renderer=dict(r(m_flMinSize=.25),m_nMinTesselation=1,m_nMaxTesselation=1,m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL')
    geometry=build_rope_geometry(particles,renderer,camera_position=[0,0,10])
    assert geometry['vertices'][0]==[0,2.5,0]
    assert geometry['colors'][0][3]==pytest.approx(.5)
    assert particles==before
    with pytest.raises(UnsupportedEffect,match='sampled camera'):build_rope_geometry(particles,renderer)

def test_dynamic_fade_requires_real_control_point_and_reports_reference_limit():
    renderer=r();renderer['m_flStartFadeSize']=dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT',m_nControlPoint=3,m_nVectorComponent=0)
    resolved,notes=resolve_rope_inputs(renderer,dict(points={3:[.15,0,0]}),'id',0)
    assert resolved['m_flStartFadeSize']==pytest.approx(.15)
    assert any('native renderer equivalence is not qualified'in note for note in notes)
    with pytest.raises(UnsupportedEffect):resolve_rope_inputs(renderer,None,'id',0)
