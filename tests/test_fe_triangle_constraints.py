import copy
import numpy as np
import pytest
from agr.fe_triangle_constraints import project_two_pin_triangles


def fixture():
    a=np.array([[0,0,0],[0,10,0],[0,20,0],[0,30,0]],float)
    points=np.r_[a,a+[2,0,0],a+[1,5,0]]
    row=dict(nNode=[list(range(4)),list(range(4,8)),list(range(8,12))],
        w1=[0.]*4,w2=[1.]*4,v1x=[2.]*4,v2=dict(x=[1.]*4,y=[.5]*4))
    return points,row


def test_two_pins_remain_exact_and_free_node_retains_its_current_side():
    points,row=fixture();points[9,1]=5 # opposite side of this lane's axis
    before=points.copy();metadata=copy.deepcopy(row)
    result=project_two_pin_triangles(points,[row])
    np.testing.assert_array_equal(result[:8],points[:8])
    np.testing.assert_allclose(result[8],points[0]+[1,.5,0],atol=1e-12)
    np.testing.assert_allclose(result[9],points[1]+[1,-.5,0],atol=1e-12)
    np.testing.assert_array_equal(points,before);assert row==metadata


def test_axis_length_change_uses_compiled_midpoint_response():
    points,row=fixture();points[4:8]=points[:4]+[4,0,0]
    result=project_two_pin_triangles(points,[row])
    np.testing.assert_allclose(result[8:],points[:4]+[2,.5,0],atol=1e-12)


def test_nondegenerate_projection_is_invariant_under_rigid_world_transform():
    points,row=fixture();rotation=np.array([[0,0,1],[1,0,0],[0,1,0]],float);translation=[12,3,-9]
    baseline=project_two_pin_triangles(points,[row])
    moved=project_two_pin_triangles(points@rotation.T+translation,[row])
    np.testing.assert_allclose(moved,baseline@rotation.T+translation,atol=1e-12)


def test_degenerate_fallback_is_finite_and_matches_authored_axis_contract():
    points,row=fixture();points[:]=0
    result=project_two_pin_triangles(points,[row])
    assert np.isfinite(result).all()
    np.testing.assert_allclose(result[8:],[[.5,.5,0]]*4,atol=1e-12)


@pytest.mark.parametrize('key,value',[('w1',[.5]*4),('w2',[.5]*4),('v1x',[0.]*4),
    ('nNode',[[0,1,2,3],[4,5,6,7],[8,9,10,12]])])
def test_other_mass_layouts_and_invalid_nodes_fail_before_mutation(key,value):
    points,row=fixture();row[key]=value
    with pytest.raises(ValueError):project_two_pin_triangles(points,[row])
