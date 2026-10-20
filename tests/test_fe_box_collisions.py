import numpy as np
import pytest

from agr.fe_body_collisions import project_box,world_box
from agr.fe_bone_motion import simulate_node_motion,validate_motion_physics


FRAME=np.c_[np.eye(3),np.zeros(3)]


def test_box_radius_expansion_preserves_unselected_and_outside_points():
    points=np.array([[.5,0,0],[.5,0,0],[4,0,0]])
    actual=project_box(points,FRAME,[1,2,3],[2]*3,[0,2])
    np.testing.assert_array_equal(actual,[[3,0,0],[.5,0,0],[4,0,0]])
    np.testing.assert_array_equal(points,[[.5,0,0],[.5,0,0],[4,0,0]])


def test_zero_friction_array_keeps_native_face_ties_distinct_from_no_array():
    points=np.array([[0,0,0],[.5,.5,0]])
    np.testing.assert_array_equal(project_box(points,FRAME,[1]*3,[0]*2,[0,1]),points)
    actual=project_box(points,FRAME,[1]*3,[0]*2,[0,1],previous=points,
        relative_transform=FRAME.tolist(),friction=[0,0])
    np.testing.assert_array_equal(actual,[[0,0,1],[.5,1,0]])


def test_rounded_box_corner_uses_cloth_radius_and_not_an_expanded_cube():
    point=[[1.1,1.1,0]]
    result=project_box(point,FRAME,[1]*3,[.2],[0],previous=point,
        relative_transform=FRAME,friction=[0])
    np.testing.assert_allclose(result,[[1+.2/np.sqrt(2),1+.2/np.sqrt(2),0]],atol=2e-7)
    np.testing.assert_allclose(project_box(point,FRAME,[1]*3,[.2],[0]),point,atol=1e-7)


def test_native_corner_threshold_uses_face_fallback_below_one_e_minus_five_squared():
    points=[[1.0015]*3,[1.002]*3]
    result=project_box(points,FRAME,[1]*3,[.2]*2,[0,1],previous=points,
        relative_transform=FRAME,friction=[0,0])
    np.testing.assert_allclose(result[0],[1.0015,1.0015,1.2],atol=2e-7)
    np.testing.assert_allclose(result[1],[1+.2/np.sqrt(3)]*3,atol=2e-7)


def test_signed_authored_extent_retains_native_min_then_max_order():
    point=[[0,0,0]]
    result=project_box(point,FRAME,[1,-.5,1],[1],[0],previous=point,
        relative_transform=FRAME,friction=[0])
    np.testing.assert_array_equal(result,[[0,-.5,0]])
    np.testing.assert_array_equal(project_box(point,FRAME,[1,-.5,1],[1],[0]),[[0,.5,0]])


def test_box_world_frame_rotates_local_offset_and_scales_signed_extents():
    half=np.sqrt(.5)
    frame,size=world_box([1,0,0,1,0,0,0,1],[10,20,30,2,0,0,half,half],[1,-.5,3])
    np.testing.assert_allclose(frame,np.c_[[[0,-1,0],[1,0,0],[0,0,1]],[10,22,30]],atol=2e-6)
    np.testing.assert_array_equal(size,[2,-1,6])


@pytest.mark.parametrize('selected',[[True],[0,0],[1]])
def test_box_rejects_unqualified_selection(selected):
    with pytest.raises(ValueError,match='selected nodes'):
        project_box([[0,0,0]],FRAME,[1]*3,[0],selected)


def test_box_validates_omitted_points_and_requires_explicit_history():
    with pytest.raises(ValueError,match='Source coordinates'):
        project_box([[0,0,0],[1e100,0,0]],FRAME,[1]*3,[0,0],[0])
    with pytest.raises(ValueError,match='previous points'):
        project_box([[0,0,0]],FRAME,[1]*3,[0],[0],friction=[0])
    with pytest.raises(ValueError,match='explicit friction'):
        project_box([[0,0,0]],FRAME,[1]*3,[0],[0],previous=[[0,0,0]])
    bad=FRAME.copy();bad[0,0]=-1
    with pytest.raises(ValueError,match='proper unit rotation'):
        project_box([[0,0,0]],bad,[1]*3,[0],[0])


def source():
    return dict(m_pFeModel=dict(m_nNodeCount=2,m_nStaticNodes=1,m_CtrlName=['anchor','cloth'],
        m_InitPose=[[0,0,0,1,0,0,0,1],[.2,0,0,1,0,0,0,1]],m_NodeInvMasses=[0,1],
        m_NodeIntegrator=[dict(flPointDamping=0.,flAnimationForceAttraction=0.,
            flAnimationVertexAttraction=0.,flGravity=0.) for _ in range(2)],m_Rods=[],
        m_TreeCollisionMasks=[0],m_NodeCollisionRadii=[.25],
        m_BoxRigids=[dict(nNode=0,tmFrame2=[0,0,0,1,0,0,0,1],vSize=[1,2,3],
            nFlags=0,nVertexMapIndex=0,nCollisionMask=0)],
        m_VertexMapValues=[255,128],m_VertexMaps=[dict(nVertexBase=0,nVertexCount=2,
            nMapOffset=0,nNodeListOffset=0,nNodeListCount=2)]))


def test_box_production_gather_radius_and_named_map_keep_static_control_fixed():
    physics=source();fe=physics['m_pFeModel']
    goals=np.broadcast_to(np.eye(4),(3,2,4,4)).copy();goals[:,1,0,3]=.2
    result,report=simulate_node_motion(physics,goals,64,1.)
    assert result[-1,1,0]>=1.25
    np.testing.assert_array_equal(result[:,0],goals[:,0,:3,3])
    assert report['native_qualified_outward_box_contact_count']==1
    assert report['approximate_box_contact_count']==0 and report['source_solver_parity'] is False
    for scale in (.01,2):
        scaled=goals.copy();scaled[:,:,:3,:3]*=scale
        actual,_=simulate_node_motion(physics,scaled,64,1.)
        np.testing.assert_array_equal(actual,result)
    fe['m_VertexMapValues'][1]=0;fe['m_VertexMaps'][0]['nNodeListCount']=1
    excluded,_=simulate_node_motion(physics,goals,64,1.)
    np.testing.assert_array_equal(excluded[:,1],goals[:,1,:3,3])
    fe['m_BoxRigids'][0]['nFlags']=1
    with pytest.raises(ValueError,match='authored flag'):
        validate_motion_physics(physics)
