import numpy as np
import pytest

from agr.fe_proxy_bones import proxy_bone_outputs, uniform_proxy_colliders, weighted_rigid_delta, skin_proxy_goals, project_proxy_rods


def fixture():
    proxy=dict(vertices=[[0,0,0],[1,0,0],[0,1,0]],faces=[[0,1,2]],fe_node_indices=[1,2,3])
    physics={'m_pFeModel':dict(m_nNodeCount=5,m_nStaticNodes=1,
        m_CtrlName=['carrier','a','b','c','output'],
        m_InitPose=[[9,9,9,1,0,0,0,1],[0,0,0,1,0,0,0,1],[1,0,0,1,0,0,0,1],
                    [0,1,0,1,0,0,0,1],[0,0,0,1,0,0,0,1]],
        m_FitWeights=[dict(nNode=n,flWeight=w) for n,w in ((1,.2),(2,.3),(3,.5))],
        m_FitMatrices=[dict(nBeginDynamic=0,nEnd=3,nNode=4)],m_ReverseOffsets=[],
        m_TreeCollisionMasks=[1,1,1,1,1,1,1],
        m_SphereRigids=[dict(nNode=0,nCollisionMask=1,nVertexMapIndex=65535,nFlags=0)])}
    return proxy,physics


def test_weighted_fit_recovers_actual_rotation_translation_and_identity():
    source=np.array([[0.,0,0],[1,0,0],[0,2,0],[0,0,3]])
    rotation=np.array([[0,-1,0],[1,0,0],[0,0,1]])
    translation=np.array([2.,-3.,4.])
    result=weighted_rigid_delta(source,source@rotation.T+translation,[.1,.5,.2,.2])
    np.testing.assert_allclose(result[:3,:3],rotation,atol=1e-12)
    np.testing.assert_allclose(result[:3,3],translation,atol=1e-12)
    np.testing.assert_allclose(weighted_rigid_delta(source,source,[1,2,3,4]),np.eye(4),atol=1e-12)


def test_fit_refuses_degenerate_plane_and_maintains_proper_rotation():
    with pytest.raises(ValueError,match='Degenerate'):
        weighted_rigid_delta([[0,0,0],[1,0,0],[2,0,0]],[[0,0,0],[1,0,0],[2,0,0]],[1,1,1])
    source=np.array([[0.,0,0],[1,0,0],[0,1,0],[0,0,1]])
    assert np.linalg.det(weighted_rigid_delta(source,-source,[1,1,1,1])[:3,:3])>0


def test_compiled_fit_ranges_are_exact_and_cannot_cross_proxy_domains():
    proxy,physics=fixture()
    fits,reverse=proxy_bone_outputs(proxy,physics)
    assert fits[0]['nNode']==4 and reverse==[]
    physics['m_pFeModel']['m_FitWeights'][0]['nNode']=0
    with pytest.raises(ValueError,match='crosses proxy domains'):
        proxy_bone_outputs(proxy,physics)


def test_proxy_collision_masks_and_named_selection_remain_exact():
    proxy,physics=fixture();fe=physics['m_pFeModel']
    assert len(uniform_proxy_colliders(proxy,physics))==1
    fe['m_SphereRigids'][0]['nCollisionMask']=2
    assert uniform_proxy_colliders(proxy,physics)==[]
    fe['m_SphereRigids'][0].update(nCollisionMask=1,nVertexMapIndex=0)
    fe['m_VertexMaps']=[dict(nVertexBase=1,nVertexCount=3,nMapOffset=0)]
    fe['m_VertexMapValues']=[0,0,0]
    assert uniform_proxy_colliders(proxy,physics)==[]
    fe['m_VertexMapValues']=[255,255,255]
    assert len(uniform_proxy_colliders(proxy,physics))==1
    fe['m_VertexMapValues']=[255,0,255]
    with pytest.raises(ValueError,match='per-vertex'):
        uniform_proxy_colliders(proxy,physics)


def test_proxy_collisions_cannot_broaden_nonuniform_mask_leaves():
    proxy,physics=fixture();physics['m_pFeModel']['m_TreeCollisionMasks'][1]=0
    with pytest.raises(ValueError,match='per-vertex'):
        uniform_proxy_colliders(proxy,physics)


def test_attachment_goals_use_source_inverse_bind_after_importer_relocates_rig():
    rest=np.eye(4);rest[:3,3]=[10.,20.,-30.]
    pose=np.eye(4);pose[:3,3]=[3.,4.,5.]
    proxy=dict(vertices=[[10.,20.,-30.],[11.,20.,-30.]],joint_names=['weapon'],
        joint_rest_transforms=[rest.tolist()],joint_count=1,blend_indices=[0,0],blend_weights=[.98,1.])
    # The bind point follows the relocated imported bone, with no origin/yaw
    # error, and an authored local +X offset remains the same physical length.
    goals=skin_proxy_goals(proxy,{'weapon':pose})
    np.testing.assert_allclose(goals[0],pose[:3,3],atol=1e-12)
    np.testing.assert_allclose(goals[1]-goals[0],[.0254,0,0],atol=1e-12)
    with pytest.raises(ValueError,match='absent'):
        skin_proxy_goals(proxy,{})


def test_authored_proxy_rods_retain_exact_domain_lengths_and_pinned_endpoints():
    proxy=dict(vertices=[[0,0,0],[1,0,0],[2,0,0]],fe_node_indices=[0,1,2])
    physics={'m_pFeModel':dict(m_NodeInvMasses=[0,1,1],m_Rods=[
        dict(nNode=[0,1],flMinDist=1.,flMaxDist=1.,flWeight0=0.,flRelaxationFactor=1.),
        dict(nNode=[1,2],flMinDist=1.,flMaxDist=1.,flWeight0=.5,flRelaxationFactor=1.)])}
    points,residual=project_proxy_rods(proxy,physics,[[0,0,0],[3,0,0],[6,0,0]],[[0,0,0],[1,0,0],[2,0,0]],1.)
    assert residual<1e-9
    np.testing.assert_allclose(points,[[0,0,0],[1,0,0],[2,0,0]],atol=1e-9)
    physics['m_pFeModel']['m_Rods'][0]['nNode']=[0,3]
    with pytest.raises(ValueError,match='crosses'):
        project_proxy_rods(proxy,physics,points,points,1.)
