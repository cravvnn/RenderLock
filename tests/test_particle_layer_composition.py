import math
import pytest

from agr.installed_effects import UnsupportedEffect
from agr.particle_texture_layers import (channel_mask, composite, gradient_pixels,
    gradient_source, linear_to_gamma, CHANNELS, BLENDS, particle_texture_value, alpha_window)
from agr.particle_texture_layers import zoom_parameters

A=(.2,.4,.8,.5)
B=(.9,.3,.6,.25)


@pytest.mark.parametrize('rate,expected',[
    (0.,(4.,1.,0.)),(1.,(4.,1.,0.)),
    (.5,(2.125,.4140625,.390625)),(-.5,(2.125,.4140625,.390625)),
])
def test_zoom_cycles_two_distinct_magnifications_and_preserves_negative_phase(rate,expected):
    assert zoom_parameters(rate)==pytest.approx(expected)


def test_zoom_phase_is_periodic_before_the_nonlinear_easing():
    assert zoom_parameters(3.25)==pytest.approx(zoom_parameters(.25))
    wide,tight,blend=zoom_parameters(.25)
    assert tight<1<wide and 0<blend<1


def test_animated_zoom_source_requires_its_actual_atlas_sampler():
    from types import SimpleNamespace
    from agr.particle_texture_layers import LayerNodes
    with pytest.raises(UnsupportedEffect,match='Animated zoom'):
        LayerNodes(None,None).zoom(SimpleNamespace(animated=True),None,.5)


def test_zoom_cannot_substitute_a_missing_per_particle_input(monkeypatch):
    import agr.particle_texture_layers as module
    class NumericGraph:
        op=staticmethod(module.numeric)
        def __init__(self,*args):pass
        def rgba(self,image):return (1.,1.,1.,1.)
    monkeypatch.setattr(module,'LayerNodes',NumericGraph)
    layer=dict(m_nTextureType='SPRITECARD_TEXTURE_ZOOM',m_TextureControls=dict(
        m_nPerParticleZoom='SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1'))
    with pytest.raises(UnsupportedEffect,match='Missing per-particle texture zoom'):
        module.build_layer_stack(None,None,[layer],lambda *a,**kw:(None,None),
                                 lambda value,default:default)


@pytest.mark.parametrize('mode,expected', [
    ('RGB',(.9,.3,.6,.5)), ('RGBA',B), ('A',(.2,.4,.8,.25)),
    ('RGB_A',(.9,.3,.6,.125)), ('RGB_ALPHAMASK',(.375,.375,.75,.5)),
    ('RGB_RGBMASK',(.55952,.34864,.69728,.5)),
    ('RGBA_RGBALPHA',(.9,.3,.6,.5136)), ('A_RGBALPHA',(.2,.4,.8,.5136)),
    ('RGB_A_RGBALPHA',(.2,.4,.8,.2568)), ('R',(.9,.9,.9,.5)),
    ('G',(.3,.3,.3,.5)), ('B',(.6,.6,.6,.5)), ('RALPHA',(.9,.9,.9,.9)),
    ('GALPHA',(.3,.3,.3,.3)), ('BALPHA',(.6,.6,.6,.6)),
])
def test_reference_channel_masks_keep_the_correct_color_and_alpha(mode,expected):
    assert channel_mask(A,B,mode)==pytest.approx(expected)


@pytest.mark.parametrize('mode,expected', [
    ('MULTIPLY',(.18,.12,.48,.125)),('MOD2X',(.36,.24,.96,.25)),
    ('REPLACE',B),('ADD',(1.,.7,1.,.75)),('SUBTRACT',(0,.1,.2,.25)),
    ('AVERAGE',(.55,.35,.7,.375)),
    ('LUMINANCE',(.089826,.179652,.359304,.25)),
])
def test_reference_layer_blends_are_ordered_and_clamp_after_composition(mode,expected):
    assert composite(A,B,blend=mode)==pytest.approx(expected)


def test_subtract_does_not_remove_alpha_from_a_color_only_layer():
    assert composite(A,B,'RGB','SUBTRACT')==pytest.approx((0,.1,.2,.5))
    assert composite(A,B,'A','SUBTRACT')==pytest.approx((.2,.4,.8,.25))
    assert composite(A,B,'BALPHA','SUBTRACT')==pytest.approx((.2,.4,.2,0))


def test_smoke_chain_distortion_replacement_then_alpha_noise_and_half_strength_blue_mask():
    # After distortion re-samples the base, the two masks must remain separate.
    warped=(.8,.4,.2,.6)
    accum=composite((1,1,1,1),warped,blend='REPLACE')
    accum=composite(accum,(.9,.7,.3,.25),channel='A')
    assert accum==pytest.approx((.64,.16,.04,.15))
    assert composite(accum,(.1,.2,.5,.7),channel='BALPHA',amount=.5)==pytest.approx((.48,.12,.03,.1125))


def test_gradient_lookup_uses_gamma_luminance_and_separate_alpha_mask_weights():
    value,alpha=gradient_source((.25,.25,.25,.5),'RGBA_RGBALPHA')
    assert value==pytest.approx(linear_to_gamma(.125))
    assert not alpha
    value,alpha=gradient_source((.1,.8,.2,.5),'A')
    assert value==pytest.approx(linear_to_gamma(.5)) and alpha
    assert linear_to_gamma(.0031308)==pytest.approx(.040449936)


def test_generated_gradient_is_a_clamped_byte_quantized_srgb_ramp_with_unsorted_stops():
    pixels=gradient_pixels(dict(m_Stops=[dict(m_flPosition=1,m_Color=[255]*3),
        dict(m_flPosition=0,m_Color=[0]*3)]))
    assert len(pixels)==256*4
    assert pixels[:4]==[0,0,0,1] and pixels[-4:]==[1,1,1,1]
    gamma=128/255
    assert pixels[128*4]==pytest.approx(((gamma+.055)/1.055)**2.4)
    assert gradient_pixels({})==[1]*1024


@pytest.mark.parametrize('stops', [
    [dict(m_flPosition=math.nan,m_Color=[255]*3)],
    [dict(m_flPosition=0,m_Color=[256,0,0])],
    [dict(m_flPosition=0,m_Color=[True,0,0])],
])
def test_invalid_gradients_are_not_silently_replaced(stops):
    with pytest.raises(UnsupportedEffect):gradient_pixels(dict(m_Stops=stops))


def test_unknown_masks_and_blends_cannot_be_reported_as_supported():
    assert len(CHANNELS)==15 and len(BLENDS)==7
    with pytest.raises(UnsupportedEffect):composite(A,B,channel='UNKNOWN')
    with pytest.raises(UnsupportedEffect):composite(A,B,blend='UNKNOWN')


def test_shader_extra_inputs_require_the_new_storage_contract_and_finite_values():
    mode='SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1'
    assert particle_texture_value(mode,dict(shader_extra_data1=1.75),'agr_shader_extra_fields_v1')==1.75
    for particle,policy in [({},'agr_shader_extra_fields_v1'),(dict(shader_extra_data1=1),None),
                           (dict(shader_extra_data1=math.nan),'agr_shader_extra_fields_v1')]:
        with pytest.raises(UnsupportedEffect):particle_texture_value(mode,particle,policy)
    with pytest.raises(UnsupportedEffect):particle_texture_value('SPRITECARD_TEXTURE_PP_SCALE_RANDOM',{},None)




def test_native_alpha_reference_window_changes_eroded_shape_without_changing_rgb():
    assert alpha_window(.6,.8,1.,0.,1.)==pytest.approx(.36)
    assert alpha_window(.6,.2,1.,0.,1.)==0
    assert alpha_window(.6,.8,1.,.1,1.)==pytest.approx(.36)




@pytest.mark.parametrize('layers',[None,[None],[dict(m_TextureControls=None)],
    [dict(m_nTextureChannels=1)],[dict(m_nTextureBlendMode=None)],[{}]*7])
def test_malformed_or_unbounded_layer_stacks_fail_before_sampling(layers):
    from agr.particle_texture_layers import build_layer_stack
    def unexpected_sample(*args,**kwargs):raise AssertionError('Invalid layer reached image sampling')
    with pytest.raises(UnsupportedEffect):build_layer_stack(None,None,layers,unexpected_sample,None)


def test_disabled_layers_preserve_original_indices_and_six_enabled_layers_are_retained():
    from agr.particle_texture_layers import enabled_texture_layers
    layers=[{},dict(m_bEnabled=False),{},dict(m_bEnabled=False),{},{},{},{}]
    assert [i for i,_ in enabled_texture_layers(layers)]==[0,2,4,5,6,7]
    with pytest.raises(UnsupportedEffect):enabled_texture_layers([{}]*7)
    with pytest.raises(UnsupportedEffect):enabled_texture_layers([dict(m_bEnabled=1)])
    with pytest.raises(UnsupportedEffect,match='fallback texture'):
        enabled_texture_layers([dict(m_bEnabled=False)])


def test_sixth_enabled_layer_changes_output_and_disabled_layers_are_not_sampled(monkeypatch):
    import agr.particle_texture_layers as module
    class NumericGraph:
        op=staticmethod(module.numeric)
        def __init__(self,*args):pass
        def rgba(self,image):return image
        def vector(self,value):return tuple(value)
    monkeypatch.setattr(module,'LayerNodes',NumericGraph)
    layers=[dict(m_nTextureBlendMode='SPRITECARD_TEXTURE_BLEND_REPLACE') for _ in range(7)]
    layers[2]=dict(m_bEnabled=False,m_TextureControls=None)
    sampled=[]
    def sample(layer,index,raw=False):
        sampled.append(index)
        return ((0,0,1,1) if index==6 else (0,1,0,1)),None
    color,alpha=module.build_layer_stack(None,None,layers,sample,lambda value,default:default)
    assert color==(0,0,1) and alpha==1 and sampled==[0,1,3,4,5,6]
