import copy
import numpy as np
import pytest
from agr.fe_rod_constraints import project_compiled_rods,project_animated_rods


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


def test_scaled_intervals_and_zero_length_stay_finite():
    points,block=fixture()
    result=project_compiled_rods(points,[block],length_scales=[[2.]*4])
    np.testing.assert_array_equal(result,points)
    points[:]=0;result=project_compiled_rods(points,[block])
    np.testing.assert_array_equal(result,points);assert np.isfinite(result).all()


@pytest.mark.parametrize('field,value',[('nNode',[[0,1,2,3],[4,5,6,9]]),
    ('f4MinDist',[11.]*4),('f4Weight0',[0.,0.,0.,1.01]),
    ('f4RelaxationFactor',[0.,0.,float('nan'),0.])])
def test_invalid_compiled_fields_are_rejected_before_mutation(field,value):
    points,block=fixture();block[field]=value
    with pytest.raises(ValueError):project_compiled_rods(points,[block])


def test_nonpositive_scales_and_bad_dimensions_are_rejected():
    points,block=fixture()
    for scales in ([[1,1,0,1]],[[1,1,1]],[[float('inf')]*4]):
        with pytest.raises(ValueError):project_compiled_rods(points,[block],length_scales=scales)


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


def test_animated_goal_bounds_and_nonfinite_values_are_rejected():
    points,block=fixture();goals=points.copy();goals[0,0]=float('nan')
    with pytest.raises(ValueError):project_animated_rods(points,[block],goals)
    with pytest.raises(ValueError):project_animated_rods(points,[block],points[:-1])
