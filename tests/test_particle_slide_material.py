import math
import pytest
from agr.particle_slide_material import slide_texture_uv,slide_opacity_factors,slide_vertex_uv,PIXEL_SHADER_SHA256,VERTEX_SHADER_SHA256
from agr.particle_material_expressions import ParticleExpressionRefused

def uv(point,time,scroll,quantize=0,xform=(1,0,0,1),offset=(0,0),sha=PIXEL_SHADER_SHA256):
    return slide_texture_uv(point,time,scroll,quantize,xform,offset,shader_sha256=sha)

def opacity(a,b,alpha=1,scale=1,dot=1,power=0,bias=(1,0),mode=0,sha=PIXEL_SHADER_SHA256):
    return slide_opacity_factors(a,b,alpha,scale,dot,power,bias,blend_mode=mode,shader_sha256=sha)

def test_scroll_wraps_before_nonidentity_transform():
    assert uv((.25,.5),1,(.75,-.25),xform=(2,0,0,.5),offset=(.125,-.25))==(2.125,.375)
    assert uv((.25,.5),-1,(.75,-.25))==(.5,.75)

@pytest.mark.parametrize('time,expected',[(.125,0.),(.375,.5),(.625,.5),(-.125,0.),(-.375,.5)])
def test_time_quantization_is_nearest_even_before_scroll(time,expected):
    assert uv((0,0),time,(1,1),.25)==(expected,expected)

def test_current_source_time_and_explicit_zero_quantization():
    assert uv((0,0),318.59375,(2,-2))==(.1875,.8125)
    assert uv((.25,.5),0,(3,7))==(.25,.5)

def test_opacity_mask_modes_and_explicit_particle_factors():
    assert opacity(.75,.5,alpha=.5,scale=2,mode=0)==.375
    assert opacity(.75,.5,mode=1)==1
    assert opacity(.75,.5,mode=2)==.25
    assert opacity(.25,.5,mode=2)==0
    assert opacity(.75,.5,alpha=0,scale=2)==0

def test_facing_uses_derived_scale_bias_and_absolute_power():
    assert opacity(1,1,dot=-.5,power=2,bias=(1,0))==.25
    assert opacity(1,1,dot=.5,power=-2,bias=(-1,1))==.25
    assert opacity(1,1,dot=0,power=0,bias=(0,0))==1
    assert opacity(1,1,dot=0,power=2,bias=(1,0))==pytest.approx(1e-6,rel=2e-7)

@pytest.mark.parametrize('value',[math.nan,math.inf,-math.inf,True,'1',1e100])
def test_nonfinite_wrong_type_or_overflow_time_refuses(value):
    with pytest.raises(ParticleExpressionRefused):uv((0,0),value,(1,1))

def test_incomplete_inputs_unsupported_shader_and_invalid_masks_refuse():
    for call in(lambda:uv((0,),0,(1,1)),lambda:uv((0,0),0,(1,1),xform=(1,0)),
            lambda:uv((0,0),0,(1,1),sha='0'*64),lambda:opacity(1,1,mode=3),
            lambda:opacity(1,1,mode=True),lambda:opacity(-.1,1),lambda:opacity(1,1,scale=-1)):
        with pytest.raises(ParticleExpressionRefused):call()

def vertex(point,transform=(2,0,0,100,0,3,0,-20,0,0,4,10),primary=True,secondary=True,**changes):
    args=dict(shader_sha256=VERTEX_SHADER_SHA256);args.update(changes)
    return slide_vertex_uv(point,transform,primary,(1,0,0,0),(0,1,0,0),(.5,.5),
        secondary,(0,0,1,0),(0,0,0,1),(0,0),**args)

def test_vertex_uv_uses_source_basis_lengths_and_duplicates_original_mesh_uv():
    assert vertex((1,1))==((1.5,2),(4,1))
    assert vertex((1,1),primary=False)==((1,1),(4,1))
    assert vertex((1,1),secondary=False)==((1.5,2),(1,1))
    assert vertex((1,1),primary=False,secondary=False)==((1,1),(1,1))
    # Reflected basis changes orientation, not positive UV scale length.
    assert vertex((1,1),transform=(-2,0,0,-100,0,-3,0,40,0,0,-4,80))==((1.5,2),(4,1))

def test_negative_vertex_uv_components_remain_sentinels():
    assert vertex((-.25,1))==((-.25,2),(-.25,1))
    assert vertex((1,-.5))==((1.5,-.5),(4,-.5))

def test_invalid_vertex_binding_and_unrepresentable_integer_refuse():
    for call in(lambda:vertex((1,1),transform=(1,)*11),lambda:vertex((1,1),primary=1),
            lambda:vertex((1,1),shader_sha256='0'*64),lambda:uv((0,0),1<<4096,(0,0))):
        with pytest.raises(ParticleExpressionRefused):call()
