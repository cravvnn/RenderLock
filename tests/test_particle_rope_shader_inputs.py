from copy import deepcopy
from types import SimpleNamespace
import pytest
from agr.particle_rope_shader_inputs import validate_inputs,needs_inputs,collect_inputs,POLICY,PARTICLES_SHA
from agr.particle_sheet_uv import SHADERS
from agr.installed_effects import UnsupportedEffect
from agr.blender_simulated_effects import validate_renderer_classes


def proof():
    return dict(policy=POLICY,particles_sha256=PARTICLES_SHA,shader_sha256=deepcopy(SHADERS),segment_seed=0,
        blend_alpha_parameters={'ALPHA':1.,'ADD':0.,'BLEND_ADD':-1.})


def test_old_ordinary_rope_needs_no_new_native_input():
    renderer=dict(_class='C_OP_RenderRopes',m_vecTexturesInput=[dict(m_hTexture='materials/plain.vtex')])
    assert not needs_inputs(renderer)
    assert not needs_inputs(dict(renderer,m_flDepthBias=dict(m_nType='PF_TYPE_LITERAL',m_flLiteralValue=0.)))
    assert validate_inputs(proof())==proof()
    assets=dict(definitions={'particles/test.vpcf':dict(m_Renderers=[renderer])},textures={})
    assert validate_renderer_classes({('i','root','particles/test.vpcf',0):[]},assets)=={}


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(policy='unknown'),lambda p:p.update(particles_sha256='a'*64),
    lambda p:p.update(segment_seed=True),lambda p:p.update(segment_seed=1),lambda p:p.update(extra=True),
    lambda p:p['shader_sha256'].update({next(iter(SHADERS)):'a'*64}),
    lambda p:p['blend_alpha_parameters'].update(BLEND_ADD=1.),
    lambda p:p['blend_alpha_parameters'].update(ALPHA=True),
    lambda p:p['blend_alpha_parameters'].update(ADD=0),
])
def test_unqualified_profile_cannot_admit_new_rope_inputs(mutation):
    value=proof();mutation(value)
    with pytest.raises(UnsupportedEffect):validate_inputs(value)


@pytest.mark.parametrize('renderer',[
    dict(m_flDepthBias=5.),dict(m_nOutputBlendMode='PARTICLE_OUTPUT_BLEND_MODE_BLEND_ADD'),
    dict(m_vecTexturesInput=[dict(m_hTexture='materials/plain.vtex',m_TextureControls=dict(m_bRandomizeOffsets=True))]),
    dict(m_vecTexturesInput=[dict(m_hTexture='materials/noise.vtex',m_nTextureType='SPRITECARD_TEXTURE_UVDISTORTION')]),
])
def test_new_features_require_valid_proof_in_preflight(renderer):
    renderer=dict(_class='C_OP_RenderRopes',**renderer);assert needs_inputs(renderer)
    tracks={('i','root','particles/test.vpcf',0):[]};assets=dict(definitions={'particles/test.vpcf':dict(m_Renderers=[renderer])},textures={})
    with pytest.raises(UnsupportedEffect,match='qualified native'):validate_renderer_classes(tracks,assets)
    assets['provenance']=dict(native_rope_shader=proof());assert validate_renderer_classes(tracks,assets)=={}


def test_missing_installed_module_cannot_become_a_qualified_input(tmp_path):
    with pytest.raises(UnsupportedEffect,match='qualified installed'):collect_inputs(SimpleNamespace(install_path=tmp_path))
