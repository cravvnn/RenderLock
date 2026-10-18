import pytest
import numpy as np
from agr.npr_authored_albedo import neutral_tint_controls


def material():
    return dict(VectorParams=dict(g_vAlbedoContrastSaturationBrightness1=[1,1,.98,0],g_vColorTint1=[1,1,1,0]),
                IntParams=dict(g_nTextureColorTintMode1=0,g_bApplyTintToVertexColors=0,g_bMaskVertexColorTint1=1,g_bMaskColorTint1=1),
                FloatParams=dict(g_fVertexColorStrength1=1))


def test_qualified_brightness_and_explicit_assumption():
    r=neutral_tint_controls(material(),assume_white_instance_color=True)
    np.testing.assert_array_equal(r['correction_matrix'],np.diag([.98,.98,.98,1]))
    with pytest.raises(ValueError):neutral_tint_controls(material(),assume_white_instance_color=False)


def test_missing_order_is_equivalent_for_explicit_white_instance():
    v=material();del v['IntParams']['g_bApplyTintToVertexColors']
    r=neutral_tint_controls(v,assume_white_instance_color=True)
    assert r['instance_color']==[1,1,1] and r['apply_tint_to_vertex_colors'] is False


@pytest.mark.parametrize('section,key,value',[
    ('VectorParams','g_vAlbedoContrastSaturationBrightness1',[1,.5,1,0]),
    ('VectorParams','g_vColorTint1',[1,0,0,0]),
    ('IntParams','g_nTextureColorTintMode1',1),
    ('IntParams','g_bMaskColorTint1',None)])
def test_unqualified_controls_rejected(section,key,value):
    v=material();v[section][key]=value
    with pytest.raises(ValueError):neutral_tint_controls(v,assume_white_instance_color=True)


def test_omitted_bebop_controls_use_only_exact_qualified_shader_defaults():
    from agr.npr_authored_albedo import DEFAULT_SHADER_SHA256
    v = {'VectorParams': {'g_vColorTint1': [1, 1, 1, 0]}}
    r = neutral_tint_controls(v, assume_white_instance_color=True,
                              shader_sha256=DEFAULT_SHADER_SHA256)
    np.testing.assert_array_equal(r['correction_matrix'], np.eye(4))
    assert r['vertex_strength'] == 1 and r['mask_color_tint'] is True
    assert v == {'VectorParams': {'g_vColorTint1': [1, 1, 1, 0]}}
    with pytest.raises(ValueError):
        neutral_tint_controls(v, assume_white_instance_color=True)
    with pytest.raises(ValueError, match='requalification'):
        neutral_tint_controls(v, assume_white_instance_color=True, shader_sha256='0' * 64)


def test_shader_defaults_never_override_explicit_unsupported_controls():
    from agr.npr_authored_albedo import DEFAULT_SHADER_SHA256
    for section, key, value in [('VectorParams', 'g_vAlbedoContrastSaturationBrightness1', None),
                                ('VectorParams', 'g_vColorTint1', [1, 0, 0, 0]),
                                ('IntParams', 'g_nTextureColorTintMode1', 1)]:
        with pytest.raises(ValueError):
            neutral_tint_controls({section: {key: value}}, assume_white_instance_color=True,
                                  shader_sha256=DEFAULT_SHADER_SHA256)
