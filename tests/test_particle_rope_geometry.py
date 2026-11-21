import copy
import pytest
from agr.particle_rope_geometry import build_rope_geometry, rope_subdivisions, rope_collection_bounds
from agr.installed_effects import UnsupportedEffect


def nodes():
    return [dict(id=i,position=[i*10,0,0],normal=[0,0,1],radius=2,color=[1,.5,0],
                 alpha=1,alpha_alternate=.5,scratch_float0=i*.25) for i in range(3)]


def settings(**changes):
    return dict(m_nMinTesselation=2,m_nMaxTesselation=2,
                m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',**changes)


def test_segment_shader_extras_copy_n1_without_interpolation_and_keep_legacy_geometry():
    p=nodes()
    for i,row in enumerate(p):row.update(shader_extra_data1=10+i,shader_extra_data2=-10-i)
    old=build_rope_geometry(p,settings())
    new=build_rope_geometry(p,settings(),shader_extra_policy='agr_shader_extra_fields_v1')
    assert {key:new[key]for key in old}==old
    assert new['shader_extras']==[[10,-10]]*8+[[11,-11]]*8
    reverse=build_rope_geometry(p,settings(m_bReverseOrder=True),shader_extra_policy='agr_shader_extra_fields_v1')
    assert reverse['shader_extras']==[[12,-12]]*8+[[11,-11]]*8
    p[0].pop('shader_extra_data1')
    with pytest.raises(UnsupportedEffect):build_rope_geometry(p,settings(),shader_extra_policy='agr_shader_extra_fields_v1')
    with pytest.raises(UnsupportedEffect):build_rope_geometry(p,settings(),shader_extra_policy='unknown')


def test_ribbon_is_connected_interpolated_and_uses_chord_texture_length():
    mesh=build_rope_geometry(nodes(),settings())
    assert len(mesh['faces'])==4 and len(mesh['vertices'])==16
    # Normal-aligned ribbons put the U=0 edge on the other side (matched to live game footage, review branch).
    assert mesh['vertices'][0]==[0,-2,0] and mesh['vertices'][-2]==[20,2,0]
    assert mesh['vertices'][1][0]==4.375 # duplicated open endpoint, not linear interpolation
    assert mesh['uv'][1]==(0,.5) and mesh['uv'][-2]==(1,2.)
    assert mesh['colors'][0]==[1,.5,0,.5]
    for i in range(3):
        assert mesh['vertices'][4*i+1]==mesh['vertices'][4*(i+1)]
        assert mesh['vertices'][4*i+2]==mesh['vertices'][4*(i+1)+3]


@pytest.mark.parametrize('change',[
    {'m_nOrientationType':'unknown'},
])
def test_unsupported_geometry_does_not_silently_fall_back(change):
    render=settings();render.update(change)
    with pytest.raises(UnsupportedEffect):build_rope_geometry(nodes(),render)


def test_missing_scalar_and_duplicate_ids_fail():
    p=nodes();del p[0]['scratch_float0']
    with pytest.raises(UnsupportedEffect,match='Missing'):
        build_rope_geometry(p,settings(m_bUseScalarForTextureCoordinate=True,m_nScalarFieldForTextureCoordinate=18))
    p=nodes();p.append(copy.deepcopy(p[0]))
    with pytest.raises(UnsupportedEffect,match='Duplicate'):build_rope_geometry(p,settings())


def test_adaptive_density_uses_radius_and_authored_padding_not_position_bounds():
    p=nodes()[:2];p[1]['position']=[100,0,0]
    low,high=rope_collection_bounds(p)
    assert low.tolist()==[-12,-12,-12] and high.tolist()==[112,12,12]
    renderer=dict(m_nMinTesselation=1,m_nMaxTesselation=128)
    projection=dict(type='PERSP',projection_y=1.7)
    near=rope_subdivisions(p,renderer,[50,0,0],projection)
    far=rope_subdivisions(p,renderer,[1000000,0,0],projection)
    assert (near,far)==(64,1)
    for row in p:row['radius']=200
    assert rope_subdivisions(p,renderer,[10000,0,0],projection)==32


def test_original_density_bounds_and_orthographic_camera_are_resolved_per_sample():
    p=nodes()[:2];p[1]['position']=[100,0,0]
    renderer=dict(m_nMinTesselation=2,m_nMaxTesselation=4)
    assert rope_subdivisions(p,renderer,[0,0,0],dict(type='PERSP',projection_y=1.7))==4
    assert rope_subdivisions(p,renderer,[1e6,0,0],dict(type='PERSP',projection_y=1.7))==2
    renderer['m_nMaxTesselation']=128
    projection=dict(type='ORTHO',height=1000)
    assert rope_subdivisions(p,renderer,[0,0,0],projection)==32
    assert rope_subdivisions(p,renderer,[1e6,0,0],projection)==32
    with pytest.raises(UnsupportedEffect,match='sampled camera'):
        rope_subdivisions(p,renderer,[0,0,0])
    with pytest.raises(UnsupportedEffect,match='child/trail'):
        rope_subdivisions(p,renderer,[0,0,0],projection,dict(m_Children=[{}]))
