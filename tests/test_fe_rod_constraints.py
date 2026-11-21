import copy
import numpy as np
import pytest
from agr.fe_rod_constraints import project_compiled_rods,project_animated_rods,prepare_compiled_rods


def test_prepared_rods_snapshot_metadata_and_do_not_retain_solver_points():
    points,block=fixture();before=points.copy()
    expected=project_compiled_rods(points,[block])
    plan=prepare_compiled_rods(len(points),[block])
    block['nNode'][0][0]=999;block['f4MaxDist'][0]=1000
    np.testing.assert_array_equal(plan.project(points),expected)
    np.testing.assert_array_equal(points,before)
    moved=points+[0,3,0]
    np.testing.assert_array_equal(plan.project(moved),expected+[0,3,0])
    for nodes,values in plan.blocks:
        with pytest.raises(ValueError):nodes.setflags(write=True)
        with pytest.raises(ValueError):values.setflags(write=True)


def test_prepared_rods_refuse_new_dimensions_nonfinite_positions_and_scales():
    points,block=fixture();plan=prepare_compiled_rods(len(points),[block])
    for bad in (points[:-1],np.full_like(points,float('nan'))):
        with pytest.raises(ValueError):plan.project(bad)
    with pytest.raises(ValueError):plan.project(points,length_scales=[[1,1,0,1]])
    block['nNode'][0][0]=999
    with pytest.raises(ValueError):prepare_compiled_rods(len(points),[block])


def test_dependent_blocks_stay_serial_and_independent_blocks_can_share_a_group():
    points=np.array([[0.,0.,0.],[20.,0.,0.],[40.,0.,0.],
                     [0.,10.,0.],[20.,10.,0.]])
    def rod(a,b):
        return dict(nNode=[[a]*4,[b]*4],f4MinDist=[10.]*4,f4MaxDist=[10.]*4,
                    f4Weight0=[0.]*4,f4RelaxationFactor=[.5]*4)
    plan=prepare_compiled_rods(5,[rod(0,1),rod(3,4),rod(1,2)])
    assert [len(group[1]) for group in plan.groups]==[2,1]
    np.testing.assert_array_equal(plan.project(points)[:,0],[0,15,32.5,0,15])


def fixture():
    start=np.array([[0,0,0],[0,10,0],[0,20,0],[0,30,0]],float)
    points=np.r_[start,start+[20,0,0]]
    block=dict(nNode=[list(range(4)),list(range(4,8))],f4MaxDist=[10.]*4,
               f4MinDist=[10.]*4,f4Weight0=[0.,.5,1.,.3],f4RelaxationFactor=[.05,.5,1.,0.])
    return points,block


def test_authored_softness_and_endpoint_weights_remain_independent():
    points,block=fixture();before=points.copy();metadata=copy.deepcopy(block)
    result=project_compiled_rods(points,[block])
    np.testing.assert_allclose(result[:,0],[0,2.5,10,0,19.5,17.5,20,20],atol=1e-12)
    np.testing.assert_array_equal(result[:,1:],points[:,1:])
    np.testing.assert_array_equal(points,before);assert block==metadata
    # The intentionally soft first lane still has9.5Sourceunits residual after
    # its valid pass; it is not evidence of an unexecuted constraint.
    assert np.linalg.norm(result[4]-result[0])-10==9.5


def test_repeated_compiler_pad_lanes_read_the_same_preblock_state():
    points,block=fixture();block['nNode']=[[0]*4,[4]*4]
    block['f4Weight0']=[0.]*4;block['f4RelaxationFactor']=[.5]*4
    result=project_compiled_rods(points,[block])
    assert result[4,0]==15 # four serial applications would instead yield10.625


def test_animated_lengths_follow_recorded_goals_instead_of_rest_or_simulation():
    points,block=fixture();block.pop('f4MaxDist');block.pop('f4MinDist')
    block['f4Weight0']=[0.]*4;block['f4RelaxationFactor']=[1.]*4
    goals=points.copy();goals[4:,:]=goals[:4]+[7,0,0]
    result=project_animated_rods(points,[block],goals)
    np.testing.assert_allclose(result[:4],points[:4],atol=1e-12)
    np.testing.assert_allclose(result[4:],points[:4]+[7,0,0],atol=1e-12)
    goals[4:,:]=goals[:4]+[12,0,0]
    again=project_animated_rods(result,[block],goals)
    np.testing.assert_allclose(again[4:],points[:4]+[12,0,0],atol=1e-12)
