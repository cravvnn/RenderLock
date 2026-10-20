import numpy as np
import pytest

from agr.fe_control_offsets import (prepare_control_offsets, validate_preparation_layout,
    control_transform_rows, control_transform_matrices)
from agr.fe_bone_motion import simulate_node_motion


def model():
    return {'m_pFeModel':dict(m_nNodeCount=3,m_nStaticNodes=2,
        m_CtrlName=['root_a','root_b','cloth'],m_InitPose=[
            [0,0,0,1,0,0,0,1],[3,0,0,1,0,0,0,1],[1,0,0,1,0,0,0,1]],
        m_NodeInvMasses=[0,0,1],m_NodeIntegrator=[dict(flPointDamping=0.,
            flAnimationForceAttraction=0.,flAnimationVertexAttraction=0.,flGravity=1.) for _ in range(3)],
        m_Rods=[],m_CtrlOffsets=[dict(nCtrlParent=0,nCtrlChild=2,vOffset=[1,0,0])],
        m_CtrlSoftOffsets=[dict(nCtrlParent=1,nCtrlChild=2,vOffset=[0,0,0],flAlpha=.5)])}


def test_hard_baseline_removes_animated_child_noise_before_soft_blend():
    source=model();fe=source['m_pFeModel'];rows=np.asarray(fe['m_InitPose'],dtype=float)
    rows[2,:3]=[999,-100,7];rows[2,3]=2;rows[0,4:]=[0,0,np.sqrt(.5),np.sqrt(.5)]
    actual=prepare_control_offsets(rows,source)
    np.testing.assert_allclose(actual[2,:3],[1.5,.5,0],atol=1e-12)
    assert actual[2,3]==1
    np.testing.assert_array_equal(actual[2,4:],rows[0,4:])
    np.testing.assert_array_equal(actual[:2],rows[:2])
    np.testing.assert_array_equal(rows[2,:3],[999,-100,7])


def test_guarded_full_preparation_closes_repeat_passes_without_squaring_alpha():
    source=model();rows=np.asarray(source['m_pFeModel']['m_InitPose'],dtype=float)
    assert validate_preparation_layout(source)==[0,1]
    first=prepare_control_offsets(rows,source)
    np.testing.assert_array_equal(first[2,:3],[2,0,0])
    for _ in range(4):
        np.testing.assert_array_equal(prepare_control_offsets(first,source),first)


@pytest.mark.parametrize('mutation,message',[
    (lambda f:f['m_CtrlOffsets'].append(dict(f['m_CtrlOffsets'][0])),'unique'),
    (lambda f:f['m_CtrlOffsets'].insert(0,dict(nCtrlParent=2,nCtrlChild=1,vOffset=[0,1,0])),'dependency'),
    (lambda f:f['m_CtrlSoftOffsets'][0].update(nCtrlChild=1),'baseline'),
    (lambda f:f.update(m_MorphLayers=[{}]),'morph'),
    (lambda f:f.update(m_DynKinLinks=[{}]),'DynKin'),
    (lambda f:f.update(m_CtrlOsOffsets=[{}]),'Os'),
])
def test_phase_contract_rejects_unqualified_layouts(mutation,message):
    source=model();mutation(source['m_pFeModel'])
    with pytest.raises(ValueError,match=message):validate_preparation_layout(source)


def test_matrix_adapter_preserves_world_rotation_and_source_unit_offsets():
    rows=np.asarray(model()['m_pFeModel']['m_InitPose'],dtype=float)
    rows[:,:3]+=[10,20,30];rows[:,4:]=[.5,.5,.5,.5]
    matrices=control_transform_matrices(rows,.0254)
    actual=control_transform_rows(matrices,.0254)
    np.testing.assert_allclose(actual[:,:4],rows[:,:4],atol=1e-12)
    np.testing.assert_allclose(control_transform_matrices(actual,.0254),matrices,atol=1e-12)
    matrices[0,:3,:3]*=2
    with pytest.raises(ValueError,match='unit'):control_transform_rows(matrices,.0254)


def test_native_animation_gather_discards_uniform_bone_scale_but_keeps_xyz_and_rotation():
    rows=np.asarray(model()['m_pFeModel']['m_InitPose'],dtype=float);rows[:,4:]=[.5,.5,.5,.5]
    original=control_transform_matrices(rows,.0254)
    for scale in (.01,1,2):
        raw=original.copy();raw[:,:3,:3]*=scale
        gathered=control_transform_rows(raw,.0254,discard_animation_scale=True)
        np.testing.assert_allclose(control_transform_matrices(gathered,.0254),original,atol=1e-12)
        np.testing.assert_array_equal(gathered[:,3],np.ones(3))
    raw=original.copy();raw[0,:3,0]*=.01
    with pytest.raises(ValueError,match='uniform'):control_transform_rows(raw,.0254,discard_animation_scale=True)
    raw=original.copy();raw[0,:3,0]*=-1
    with pytest.raises(ValueError):control_transform_rows(raw,.0254,discard_animation_scale=True)


def test_soft_goal_preparation_does_not_clamp_away_simulated_gravity():
    source=model();rows=np.asarray(source['m_pFeModel']['m_InitPose'],dtype=float)
    goals=np.broadcast_to(control_transform_matrices(rows,1.),(3,3,4,4)).copy()
    goals[:,2,:3,3]=[900,800,700]
    actual,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_array_equal(actual[0,2],[2,0,0])
    assert actual[-1,2,2]<0
    assert actual[-1,2,0]==2
    np.testing.assert_array_equal(actual[:,:2],goals[:,:2,:3,3])
    assert report['native_qualified_soft_control_offsets']==1
    assert report['native_qualified_hard_control_offsets']==1
    assert report['source_solver_parity'] is False


def test_preparation_follows_rotating_root_inside_substep_clock():
    source=model();fe=source['m_pFeModel'];fe['m_NodeInvMasses'][2]=0
    rows=np.asarray(fe['m_InitPose'],dtype=float);goals=np.broadcast_to(control_transform_matrices(rows,1.),(2,3,4,4)).copy()
    end=rows.copy();end[0,4:]=[0,0,np.sqrt(.5),np.sqrt(.5)]
    goals[1]=control_transform_matrices(end,1.)
    actual,report=simulate_node_motion(source,goals,64,1.)
    np.testing.assert_allclose(actual[-1,2],[1.5,.5,0],atol=1e-12)
    assert report['substeps']==4 and report['control_preparation_repeat_closure']
