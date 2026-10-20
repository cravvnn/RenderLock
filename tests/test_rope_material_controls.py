import pytest
from agr.blender_rope_material import rope_color_scale
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
