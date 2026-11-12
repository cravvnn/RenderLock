import copy
import pytest
from agr.particle_texture_inputs import resolve_texture_random_inputs
from agr.blender_simulated_effects import texture_control_value
from agr.installed_effects import UnsupportedEffect


def control(**change):
    return dict(m_nType='PF_TYPE_RANDOM_UNIFORM',m_flRandomMin=0,m_flRandomMax=1,
                m_nMapType='PF_MAP_TYPE_MULT',m_flMultFactor=-3,**change)


def test_collection_texture_substitute_is_stable_isolated_bounded_and_reported():
    source={'m_flFinalTextureOffsetV':control()};before=copy.deepcopy(source)
    resolved,notes=resolve_texture_random_inputs(source,'instance/child/renderer0/layer0',12345,'agr_substitute_sha256_v1')
    assert source==before and len(notes)==1 and 'substitute' in notes[0]
    value=texture_control_value(resolved['m_flFinalTextureOffsetV'],99)
    assert -3<value<0
    assert resolve_texture_random_inputs(source,'instance/child/renderer0/layer0',12345,'agr_substitute_sha256_v1')[0]==resolved
    assert resolve_texture_random_inputs(source,'instance/child/renderer0/layer1',12345,'agr_substitute_sha256_v1')[0]!=resolved
    assert resolve_texture_random_inputs(source,'instance/child/renderer0/layer0',12346,'agr_substitute_sha256_v1')[0]!=resolved


@pytest.mark.parametrize('change',[
    {'m_flRandomMax':-1},{'m_flRandomMin':True},{'m_flRandomMax':float('nan')},
    {'m_nRandomSeed':3},{'m_nRandomMode':'PF_RANDOM_MODE_PER_PARTICLE'},{'m_bHasRandomSignFlip':True},
    {'m_nMapType':'PF_MAP_TYPE_UNKNOWN'},{'m_flRandomMax':1e300},
])
def test_unqualified_texture_random_modes_do_not_become_literal_defaults(change):
    value=control();value.update(change)
    with pytest.raises(UnsupportedEffect):
        resolve_texture_random_inputs({'m_flFinalTextureOffsetV':value},'instance/child/0/0',12345,'agr_substitute_sha256_v1')


@pytest.mark.parametrize('seed,policy',[(1,None),(True,'agr_substitute_sha256_v1'),(-1,'agr_substitute_sha256_v1')])
def test_missing_or_invalid_substitute_identity_policy_fails(seed,policy):
    with pytest.raises(UnsupportedEffect):
        resolve_texture_random_inputs({'m_flFinalTextureOffsetV':control()},'identity',seed,policy)


def test_degenerate_random_interval_remains_exact_without_a_seed():
    source={'m_flFinalTextureOffsetV':dict(m_nType='PF_TYPE_RANDOM_UNIFORM',m_flRandomMin=.5,m_flRandomMax=.5,m_nRandomSeed=3)}
    resolved,notes=resolve_texture_random_inputs(source,'identity',None,None)
    assert resolved==source and notes==[] and texture_control_value(resolved['m_flFinalTextureOffsetV'],0)==.5


def test_source_scale_uses_captured_particle_factor_and_never_drops_invalid_input():
    from agr.blender_simulated_effects import texture_uv_parameters,texture_uv_corners
    controls={'m_flFinalTextureScaleU':2,'m_flFinalTextureScaleV':3}
    assert texture_uv_parameters(controls,0,scale_factor=.5)[0]==(1,1.5)
    assert texture_uv_corners({},0,scale_factor=2)==[(.25,.25),(.75,.25),(.25,.75),(.75,.75)]
    for value in (0,True,float('nan'),float('inf'),1e300):
        with pytest.raises(UnsupportedEffect):texture_uv_parameters(controls,0,scale_factor=value)
