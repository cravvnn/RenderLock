import copy
import math
import struct
import numpy as np
import pytest
from agr.observed_slide_sprite_geometry import build_captured_frame, captured_renderer, MODES
from agr.observed_slide_geometry_base import texture_corners
from agr.installed_effects import UnsupportedEffect


def inputs(mode=2):
    literal = lambda v: dict(state='literal', value=v)
    return dict(kind='C_OP_RenderSprites', animation_raw=struct.pack('<fi', .1, mode).hex(), gamma_raw='01',
        radius_scale=literal(1.), alpha_scale=literal(1.), depth_bias=literal(0.),
        texture_layers=[dict(accepted_index=0, source_index=0, scale_u=dict(state='constant_random',value=.75),scale_v=literal(1.25))])


def renderer(mode=2):
    return dict(_class='C_OP_RenderSprites', m_nAnimationType=MODES[mode], m_vecTexturesInput=[dict(m_hTexture='flat')])


def point():
    return dict(position=[0.,0.,0.], radius=2., rotation=[0.,0.,-2.], color_bytes=[255,255,255,255],
        sequence=[0,0], native_animation_input=.25, shader_extra_data1=1., native_random=.25)


def test_measured_texture_values_keep_source_immutable_and_refuse_reassociation():
    r=renderer();before=copy.deepcopy(r);n=inputs()
    effective,mode=captured_renderer(r,n)
    assert r==before and mode==2
    assert effective['m_vecTexturesInput'][0]['m_TextureControls']['m_flFinalTextureScaleU']==.75
    n['texture_layers'][0]['source_index']=1
    with pytest.raises(UnsupportedEffect,match='association'):captured_renderer(r,n)


@pytest.mark.parametrize('axis,index', [('ROLL',2),('YAW',0),('PITCH',1)])
def test_signed_native_rotation_texture_scale(axis,index):
    p=point();p['rotation'][index]=-2.
    uv=texture_corners(dict(m_nPerParticleScale='SPRITECARD_TEXTURE_PP_SCALE_'+axis),0.,p,0)
    np.testing.assert_array_equal(uv,[(.75,.25),(.25,.25),(.75,.75),(.25,.75)])
    p['rotation'][index]=0.
    with pytest.raises(UnsupportedEffect,match='zero/nonfinite'):texture_corners(dict(m_nPerParticleScale='SPRITECARD_TEXTURE_PP_SCALE_'+axis),0.,p,0)


@pytest.mark.parametrize('mode',[0,1,2])
def test_modes_retain_exact_native_points_without_invented_age(mode):
    row=dict(points=[point()],native_inputs=inputs(mode),collection_time=123.)
    g=build_captured_frame(row,renderer(mode),{'flat':{}},dict(right=[1.,0.,0.],up=[0.,1.,0.],position=[0.,0.,10.]))
    assert g['native_points']==1 and len(g['faces'])==2 and 'age' not in row['points'][0]
    assert row['points'][0]['native_animation_input']==.25


def test_unknown_animated_timing_and_missing_values_refuse():
    sheet=dict(sequences=[dict(frames=[{},{}])])
    with pytest.raises(UnsupportedEffect,match='timing'):build_captured_frame(dict(points=[],native_inputs=inputs(1),collection_time=0.),renderer(1),{'flat':dict(source_sheet=sheet)},None)
    for key in ('alpha_scale','radius_scale','depth_bias'):
        n=inputs();del n[key]
        with pytest.raises(UnsupportedEffect,match='literal'):captured_renderer(renderer(),n)
    n=inputs();n['texture_layers'][0]['scale_u']['value']=math.nan
    with pytest.raises(UnsupportedEffect,match='scale'):captured_renderer(renderer(),n)
