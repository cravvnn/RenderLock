import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_silence_model import material_inputs,AUX_POLICY,is_silence_renderer,silence_material_profile
from agr.particle_model_math import component_scale,model_matrix
import numpy as np

def test_native_omitted_local_scale_default_is_frame_scale():
    renderer=dict(m_bIgnoreNormal=True,m_vecComponentScale=dict(m_vLiteralValue=[1,1,2]))
    particle=dict(position=[0,0,0],normal=[0,0,1],rotation=[.2,.4,.6],radius=1)
    np.testing.assert_allclose(model_matrix(particle,renderer),model_matrix(particle,dict(renderer,m_bLocalScale=False)))
    assert not np.allclose(model_matrix(particle,renderer),model_matrix(particle,dict(renderer,m_bLocalScale=True)))
    with pytest.raises(UnsupportedEffect):component_scale(dict(renderer,m_bLocalScale=0))

def fields():
    return dict(color=[.1,.2,.3],scratch_float0=4,scratch_float1=.7,scratch_float2=-.25)

def policies():return dict(model_material_fields_policy='agr_model_material_fields_v1',model_aux_scalar_fields_policy=AUX_POLICY)

def test_offset_controls_velocity_not_selfillum_coordinates():
    values=material_inputs(fields(),policies())
    assert values==dict(color=(.1,.2,.3),glow=4.,scroll=(-.25,-.125),alpha_test=.7)

@pytest.mark.parametrize('field',list(fields()))
def test_every_bound_input_is_captured_even_when_unused_by_material(field):
    particle=fields();del particle[field]
    with pytest.raises(UnsupportedEffect):material_inputs(particle,policies())

@pytest.mark.parametrize('key',list(policies()))
def test_old_helper_cannot_silently_default_new_inputs(key):
    policy=policies();del policy[key]
    with pytest.raises(UnsupportedEffect):material_inputs(fields(),policy)

@pytest.mark.parametrize('field,value',[('scratch_float0',-1),('scratch_float2',float('inf')),('scratch_float1',True),('color',[0,float('nan'),0]),('color',[0,-1,0])])
def test_unqualified_material_numbers_are_refused(field,value):
    particle=fields();particle[field]=value
    with pytest.raises(UnsupportedEffect):material_inputs(particle,policies())

def test_unqualified_renderer_never_gains_static_material_support():
    assert not is_silence_renderer(dict(_class='C_OP_RenderModels',m_hOverrideMaterial='silenced_debuff_start_sphere.vmat'))
    assert not is_silence_renderer(dict(m_flRadius=float('nan')))
    with pytest.raises(UnsupportedEffect):silence_material_profile({}, {})
