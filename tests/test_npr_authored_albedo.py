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
    ('VectorParams','g_vAlbedoContrastSaturationBrightness1',[.5,1,1,0]),
    ('VectorParams','g_vColorTint1',[1,-.1,0,0]),
    ('IntParams','g_nTextureColorTintMode1',3),
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
                                ('VectorParams', 'g_vColorTint1', [1, -1, 0, 0]),
                                ('IntParams', 'g_nTextureColorTintMode1', 3)]:
        with pytest.raises(ValueError):
            neutral_tint_controls({section: {key: value}}, assume_white_instance_color=True,
                                  shader_sha256=DEFAULT_SHADER_SHA256)


@pytest.mark.parametrize('mode,multiplier',[(0,1),(2,2)])
def test_authored_colored_tint_preserves_masked_composition(mode,multiplier):
    from agr.npr_albedo import albedo_color
    value=material();value['VectorParams']['g_vColorTint1']=[.2,.5,.8,0]
    value['IntParams']['g_nTextureColorTintMode1']=mode
    controls=neutral_tint_controls(value,assume_white_instance_color=True)
    np.testing.assert_allclose(controls['tint_matrix'],np.diag([.2*multiplier,.5*multiplier,.8*multiplier,1]))
    controls.pop('instance_color');controls.pop('tint_mask')
    actual=albedo_color([[1,1,1]]*3,[[1,1,1]]*3,[0,.5,1],**controls)
    expected=np.asarray([[1,1,1],(np.ones(3)+np.array([.2,.5,.8])*multiplier)/2,np.array([.2,.5,.8])*multiplier])*.98
    np.testing.assert_allclose(actual,expected)


def test_desaturated_source_and_luminance_tint_are_affine_and_masked():
    from agr.npr_albedo import albedo_color
    value=material();value['VectorParams'].update(g_vAlbedoContrastSaturationBrightness1=[1,0,1,0],g_vColorTint1=[.2,.5,.8,0])
    value['IntParams']['g_nTextureColorTintMode1']=1
    controls=neutral_tint_controls(value,assume_white_instance_color=True)
    controls.pop('instance_color');controls.pop('tint_mask')
    # Desaturation must make the three output channels equal before tint.
    gray=albedo_color([[.1,.4,.9]],[[1,1,1]],[0],**controls)[0]
    np.testing.assert_allclose(gray,np.repeat(gray[0],3))
    tinted=albedo_color([[.1,.4,.9]],[[1,1,1]],[1],**controls)[0]
    halfway=albedo_color([[.1,.4,.9]],[[1,1,1]],[.5],**controls)[0]
    assert not np.allclose(tinted,gray)
    np.testing.assert_allclose(halfway,(gray+tinted)/2)


def test_authored_contrast_uses_exact_supplied_texture_pivot():
    from agr.npr_albedo import albedo_color
    value=material();value['VectorParams']['g_vAlbedoContrastSaturationBrightness1']=[1.5,1,1,0]
    with pytest.raises(ValueError,match='authenticated original texture average'):
        neutral_tint_controls(value,assume_white_instance_color=True)
    pivot=[.2,.4,.6]
    controls=neutral_tint_controls(value,assume_white_instance_color=True,texture_average=pivot)
    controls.pop('instance_color');controls.pop('tint_mask')
    actual=albedo_color([[.3,.5,.8]],[[1,1,1]],[1],**controls)
    np.testing.assert_allclose(actual,[[.35,.55,.9]])
