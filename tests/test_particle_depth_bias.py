import numpy as np
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_depth_bias import projective_depth_bias
from agr.particle_depth_bias import discard_xy_clipped_triangles


def camera(perspective=True):
    near,far=.1,1000.
    matrix=([[1.2,0,0,0],[0,1.7,0,0],[0,0,-(far+near)/(far-near),-2*far*near/(far-near)],[0,0,-1,0]]
        if perspective else [[.1,0,0,0],[0,.2,0,0],[0,0,-2/(far-near),-(far+near)/(far-near)],[0,0,0,1]])
    return dict(world_to_clip=matrix,forward=[0,0,-1])


def clip(points,matrix):
    return np.column_stack((points,np.ones(len(points))))@np.array(matrix).T


@pytest.mark.parametrize('perspective',[True,False])
@pytest.mark.parametrize('bias',[-5,0,7])
def test_proxy_preserves_screen_position_and_uses_shifted_depth(perspective,bias):
    points=np.array([[-2,-1,-12],[3,-1,-20],[0,4,-30.]])
    view=camera(perspective);proxy,ratio=projective_depth_bias(points,bias,view)
    original=clip(points,view['world_to_clip']);result=clip(proxy,view['world_to_clip'])
    shifted=clip(points+np.array(view['forward'])*bias,view['world_to_clip'])
    np.testing.assert_allclose(result[:,:2]/result[:,3,None],original[:,:2]/original[:,3,None],atol=1e-12)
    np.testing.assert_allclose(result[:,2]/result[:,3],shifted[:,2]/shifted[:,3],atol=1e-12)
    np.testing.assert_allclose(ratio,result[:,3]/original[:,3],atol=1e-12)
    if not perspective:np.testing.assert_allclose(ratio,1)


def test_weighted_uv_and_color_recover_original_perspective_interpolation():
    points=np.array([[-2,-1,-12],[3,-1,-20],[0,4,-30.]])
    view=camera();proxy,ratio=projective_depth_bias(points,-5,view)
    w=clip(points,view['world_to_clip'])[:,3];pw=clip(proxy,view['world_to_clip'])[:,3]
    values=np.array([[0,0,.1,.3,.5,.7],[1,0,.9,.8,.4,.6],[.2,1,.3,.5,.7,.2]])
    barycentric=np.array([.2,.3,.5])
    def interpolate(v,weights):return ((barycentric/weights)@v)/sum(barycentric/weights)
    expected=interpolate(values,w)
    recovered=interpolate(values*ratio[:,None],pw)/interpolate(ratio,pw)
    np.testing.assert_allclose(recovered,expected,atol=1e-12)
    assert np.max(abs(interpolate(values,pw)-expected))>.01


@pytest.mark.parametrize('points,bias,view',[
    ([[0,0,-2]],-3,camera()), ([[0,0,-2],[0,0,1]],1,camera()),
    ([[0,0,-.11]],-.05,camera()), ([[0,0,-999]],5,camera()),
    ([[0,0,-10]],1,{}), ([[0,0,-10]],1,dict(world_to_clip=np.zeros((4,4)).tolist(),forward=[0,0,-1])),
    ([[0,0,-10]],1,dict(camera(),forward=[0,0,-2])),
    ([[0,0,float('nan')]],1,camera()), ([[0,0,-10]],True,camera()),
])
def test_unknown_or_clipped_depth_cannot_be_accepted(points,bias,view):
    with pytest.raises(UnsupportedEffect):projective_depth_bias(points,bias,view)


def test_wholly_behind_mesh_remains_clipped_because_shader_does_not_change_clip_w():
    points=np.array([[1,2,3],[2,3,4],[1,3,4.]])
    for bias in (-50,5):
        proxy,ratio=projective_depth_bias(points,bias,camera())
        np.testing.assert_array_equal(proxy,points);np.testing.assert_array_equal(ratio,1)


def geometry(points,faces=None):
    return dict(vertices=points,faces=faces or[(0,1,2)],uv=[[i,.25]for i in range(len(points))],
        colors=[[i/10,.2,.3,.5]for i in range(len(points))],subdivisions=1)


@pytest.mark.parametrize('points',[
    [[20,-1,-10],[30,0,-10],[20,1,-10]],
    [[-20,-1,-10],[-30,0,-10],[-20,1,-10]],
    [[-1,20,-10],[0,30,-10],[1,20,-10]],
    [[-1,-20,-10],[0,-30,-10],[1,-20,-10]],
    [[1,1,10],[-1,-1,10],[0,1,10]],
    # Mixed camera-plane triangle still lies wholly outside the same right plane.
    [[100,0,-10],[100,1,10],[100,2,-10]],
])
def test_only_provably_invisible_triangles_are_removed(points):
    result=discard_xy_clipped_triangles(geometry(points),camera())
    assert result['faces']==[] and len(result['vertices'])==0
    assert result['depth_xy_cull']['culled_triangles']==1


def test_mixed_plane_potentially_visible_triangle_still_refuses_depth_proxy():
    points=[[-1,-1,-10],[1,-1,-10],[0,1,1]]
    result=discard_xy_clipped_triangles(geometry(points),camera())
    np.testing.assert_array_equal(result['vertices'],points)
    with pytest.raises(UnsupportedEffect,match='camera plane'):
        projective_depth_bias(result['vertices'],5,camera())


def test_culling_keeps_original_attribute_order_and_uncertain_clip_boundary():
    points=[[20,-1,-10],[30,0,-10],[20,1,-10],[-1,-1,-10],[1,-1,-10],[0,1,-10]]
    source=geometry(points,[(0,1,2),(3,4,5)]);result=discard_xy_clipped_triangles(source,camera())
    np.testing.assert_array_equal(result['vertices'],points[3:])
    np.testing.assert_array_equal(result['uv'],source['uv'][3:]);np.testing.assert_array_equal(result['colors'],source['colors'][3:])
    assert result['faces']==[(0,1,2)] and len(source['vertices'])==6
    edge=geometry([[10/1.2+1e-7,0,-10],[10/1.2+1e-7,1,-10],[10/1.2+1e-7,-1,-10]])
    assert len(discard_xy_clipped_triangles(edge,camera())['faces'])==1


def test_trivial_rejections_agree_with_independent_homogeneous_polygon_clipper():
    rng=np.random.default_rng(187);view=camera();discarded=retained=0
    for _ in range(128):
        points=rng.uniform([-30,-30,-20],[30,30,5],(3,3));result=discard_xy_clipped_triangles(geometry(points.tolist()),view)
        polygon=list(clip(points,view['world_to_clip']))
        for plane in(np.array([1,0,0,1]),np.array([-1,0,0,1]),np.array([0,1,0,1]),np.array([0,-1,0,1]),np.array([0,0,0,1])):
            output=[]
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                da,db=np.dot(a,plane),np.dot(b,plane)
                if da>=0:output.append(a)
                if(da<0)!=(db<0):output.append(a+(b-a)*(da/(da-db)))
            polygon=output
        if not result['faces']:assert not polygon;discarded+=1
        else:retained+=1
    assert discarded>10 and retained>10
def test_live_card_outside_xy_can_cross_camera_without_guessing_visible_clipping():
    from agr.particle_depth_bias import qualify_depth_card
    # Simple perspective clip matrix; every vertex is strictly below Y=-W.
    matrix=[[1,0,0,0],[0,1,0,0],[0,0,-1,-.2],[0,0,-1,0]]
    projection=dict(world_to_clip=matrix,forward=[0,0,-1])
    points=[[-1,-8,2],[1,-8,2],[1,-8,-2],[-1,-8,-2]]
    assert qualify_depth_card(points,20,projection)=='xy_clipped'
    with pytest.raises(ValueError,match='camera plane'):qualify_depth_card(points,-20,projection)
    visible=[[-1,-1,2],[1,-1,2],[1,1,-2],[-1,1,-2]]
    with pytest.raises(ValueError,match='camera plane'):qualify_depth_card(visible,20,projection)
