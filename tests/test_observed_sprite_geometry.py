import math
import numpy as np
import pytest
from agr.observed_sprite_geometry import build_frame, layer_uvs, random_offsets
from agr.installed_effects import UnsupportedEffect


def point(**kwargs):
    return dict(position=[0.,0.,0.],radius=2.,rotation=[0.,0.,0.],color_bytes=[255,255,255,255],
        sequence=0,manual_frame=.25,shader_extra_data1=2.,native_random=.25,**kwargs)


def renderer(**kwargs):
    return dict(_class='C_OP_RenderSprites',m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES',
        m_vecTexturesInput=[dict(m_hTexture='flat')],**kwargs)


def mesh(p=None,r=None):
    return build_frame(dict(points=[p or point()],collection_time=1.),r or renderer(),{'flat':{}},
        dict(right=[1.,0.,0.],up=[0.,1.,0.],position=[0.,0.,10.]))


def test_camera_axes_yaw_and_unorm_cutoff():
    g = mesh(); np.testing.assert_allclose(g['vertices'],[[-2,-2,0],[2,-2,0],[-2,2,0],[2,2,0]])
    p = point(); p['rotation'] = [math.pi/2,0.,0.]
    g = mesh(p); assert np.max(abs(g['vertices'][:,0])) < 1e-12
    assert np.max(abs(g['vertices'][:,2])) == 2.
    p = point(); p['color_bytes'][-1] = 0
    g = mesh(p); assert len(g['faces']) == 2 and g['native_points'] == 1 and not np.any(g['vertices'])


def test_size_fade_scales_rgb_and_alpha_and_preserves_radius_clamp():
    r = renderer(m_flStartFadeSize=.1,m_flEndFadeSize=.3,m_flMinSize=.25,m_flMaxSize=.4)
    g = mesh(r=r)
    np.testing.assert_allclose(g['colors'][0],[.5,.5,.5,.5],atol=2e-7)
    assert np.max(abs(g['vertices'][:,:2])) == 2.5


def test_native_source_v_scroll_then_atlas_conversion_and_manual_frame():
    p = point(); layer = dict(m_TextureControls=dict(m_flFinalTextureOffsetV=dict(m_nType='PF_TYPE_COLLECTION_AGE',m_nMapType='PF_MAP_TYPE_MULT',m_flMultFactor=.25)))
    a,_,_ = layer_uvs(p,renderer(),layer,0,1.,{})
    assert a[0] == (0.,-.25) and a[2] == (0.,.75)
    sheet = dict(schema_version=1,policy='agr_source_particle_sheet_v1',sequences=[dict(index=0,clamp=False,total_time=1.,frames=[
        dict(duration=.5,min=[0.,0.],max=[.5,.5]),dict(duration=.5,min=[.5,.5],max=[1.,1.])])])
    a,b,blend = layer_uvs(p,renderer(),{},0,1234.,dict(source_sheet=sheet))
    assert blend == .5 and a[0] == (0.,.5) and b[0] == (.5,0.)
    p['manual_frame'] = .75
    a,b,blend = layer_uvs(p,renderer(m_flAnimationRate=999.),{},0,-4.,dict(source_sheet=sheet))
    assert blend == .5 and a[0] == (.5,0.) and b[0] == (0.,.5)


def test_native_random_offsets_use_float32_and_refuse_unknown_inputs():
    # Recorded shader draw1/4, not a synthetic particle ID or a substitute seed.
    assert random_offsets(.25,1) == (0.8709821701049805,0.5263795852661133)
    with pytest.raises(UnsupportedEffect): random_offsets(.25,0)
    with pytest.raises(UnsupportedEffect): random_offsets(math.nan,1)
