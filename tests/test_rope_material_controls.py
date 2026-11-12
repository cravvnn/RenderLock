import pytest
from agr.blender_rope_material import rope_color_scale, place_rope_texture_vertices
from agr.installed_effects import UnsupportedEffect


def test_literal_byte_color_is_normalized_after_vertex_gamma_without_a_second_gamma():
    assert rope_color_scale({})==(1,1,1)
    assert rope_color_scale({'m_vecColorScale':{'m_nType':'PVEC_TYPE_LITERAL_COLOR',
        'm_LiteralColor':[200,128,255]}})==(0.7843137979507446,0.501960813999176,1.)
    assert rope_color_scale({'m_vecColorScale':{'m_nType':'PVEC_TYPE_LITERAL',
        'm_vLiteralValue':[2,.5,-1]}})==(2,.5,-1)


@pytest.mark.parametrize('provider',[
    {'m_nType':'PVEC_TYPE_PARTICLE_VECTOR','m_vLiteralValue':[1,1,1]},
    {'m_nType':'PVEC_TYPE_LITERAL_COLOR','m_LiteralColor':[256,128,255]},
    {'m_nType':'PVEC_TYPE_LITERAL_COLOR','m_LiteralColor':[True,128,255]},
    {'m_nType':'PVEC_TYPE_LITERAL','m_vLiteralValue':[1e300,1,1]},
])
def test_unavailable_or_invalid_vector_providers_cannot_become_white(provider):
    with pytest.raises(UnsupportedEffect):rope_color_scale({'m_vecColorScale':provider})


def test_rope_clamp_uses_actual_vertices_instead_of_a_fragment_or_unit_card_clamp():
    controls={'m_bClampUVs':True,'m_flFinalTextureScaleV':.5}
    assert place_rope_texture_vertices([(0,-1),(0,.25),(0,.5),(0,.75),(0,2)],controls)==[(0,0),(0,0),(0,.5),(0,1),(0,1)]
    segment=place_rope_texture_vertices([(0,.125),(0,.375)],controls)
    assert (segment[0][1]+segment[1][1])/2==.125
    assert max(0,min(1,(.25-.5)/.5+.5))==0


def test_rope_uv_rotation_and_wrapped_offsets_precede_saturation():
    import math
    controls={'m_bClampUVs':True,'m_flFinalTextureUVRotation':math.pi/2,
              'm_flFinalTextureOffsetU':1.25,'m_flFinalTextureOffsetV':-.25}
    for actual,expected in zip(place_rope_texture_vertices([(0,0),(1,1),(.5,.5)],controls),[(1,0),(.25,.75),(.75,.25)]):
        assert actual==pytest.approx(expected)


@pytest.mark.parametrize('uv,controls',[
    ([(0,float('nan'))],{}), ([(0,0,0)],{}),
    ([(0,0)],{'m_flFinalTextureScaleU':0}),
    ([(0,0)],{'m_flFinalTextureOffsetV':{'m_nType':'PF_TYPE_CONTROL_POINT_COMPONENT','m_nControlPoint':4}}),
])
def test_rope_placed_uvs_refuse_invalid_or_unprovided_inputs(uv,controls):
    with pytest.raises((UnsupportedEffect,ValueError)):place_rope_texture_vertices(uv,controls)
