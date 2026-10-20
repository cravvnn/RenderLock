import copy

import numpy as np
import pytest

from agr.fe_triangle_constraints import project_compiled_triangles,project_mass_weighted_triangles


def fixture(pins):
    origin=np.array([[0,0,0],[0,10,0],[0,20,0],[0,30,0]],float)
    points=np.r_[origin,origin+[3,0,0],origin+[1,4,0]]
    row=dict(nNode=[list(range(4)),list(range(4,8)),list(range(8,12))],
        w1=[.3 if pins==0 else .4]*4,w2=[.2 if pins==0 else .6]*4,
        v1x=[2.]*4,v2=dict(x=[1.]*4,y=[.5]*4))
    return points,row


@pytest.mark.parametrize('pins',[0,1])
def test_projection_restores_shape_and_preserves_rigid_world_transform(pins):
    points,row=fixture(pins);before=points.copy();metadata=copy.deepcopy(row)
    result=project_mass_weighted_triangles(points,[row],pinned_nodes=pins)
    np.testing.assert_allclose(np.linalg.norm(result[4:8]-result[:4],axis=1),2,atol=1e-12)
    np.testing.assert_allclose(np.linalg.norm(result[8:]-result[:4],axis=1),np.sqrt(1.25),atol=1e-12)
    np.testing.assert_allclose(np.sum((result[4:8]-result[:4])*(result[8:]-result[:4]),axis=1),2,atol=1e-12)
    rotation=np.array([[0,0,1],[1,0,0],[0,1,0]],float);translation=[15,3,-8]
    moved=project_mass_weighted_triangles(points@rotation.T+translation,[row],pinned_nodes=pins)
    np.testing.assert_allclose(moved,result@rotation.T+translation,atol=1e-12)
    np.testing.assert_array_equal(points,before);assert row==metadata


def test_free_triangle_preserves_the_compiled_mass_center():
    points,row=fixture(0)
    result=project_mass_weighted_triangles(points,[row],pinned_nodes=0)
    weights=[.5,.3,.2]
    before=sum(points[side*4:side*4+4]*weight for side,weight in enumerate(weights))
    after=sum(result[side*4:side*4+4]*weight for side,weight in enumerate(weights))
    np.testing.assert_allclose(after,before,atol=1e-12)


def test_one_pin_is_exact_and_scale_changes_only_authored_shape():
    points,row=fixture(1)
    result=project_mass_weighted_triangles(points,[row],pinned_nodes=1,length_scales=[[2]*4])
    np.testing.assert_array_equal(result[:4],points[:4])
    np.testing.assert_allclose(np.linalg.norm(result[4:8]-result[:4],axis=1),4,atol=1e-12)


@pytest.mark.parametrize('pins',[0,1])
def test_rest_shape_is_fixed_and_zero_input_fallback_remains_finite(pins):
    points,row=fixture(pins);points[4:8]=points[:4]+[2,0,0];points[8:]=points[:4]+[1,.5,0]
    np.testing.assert_allclose(project_mass_weighted_triangles(points,[row],pinned_nodes=pins),points,atol=1e-12)
    assert np.isfinite(project_mass_weighted_triangles(np.zeros_like(points),[row],pinned_nodes=pins)).all()


@pytest.mark.parametrize('pins',[0,1])
def test_padded_lanes_gather_before_writes(pins):
    points,row=fixture(pins);row['nNode']=[[0]*4,[4]*4,[8]*4]
    # The last lane wins for every node; it must still use the original inputs.
    row['v1x']=[1,2,3,4];row['v2']['x']=[.5,1,1.5,2]
    last=copy.deepcopy(row);last['v1x']=[4]*4;last['v2']['x']=[2]*4
    actual=project_mass_weighted_triangles(points,[row],pinned_nodes=pins)
    expected=project_mass_weighted_triangles(points,[last],pinned_nodes=pins)
    np.testing.assert_array_equal(actual,expected)


@pytest.mark.parametrize('pins,bad',[(-1,None),(2,None),(True,None),(0,{'w2':[.8]*4}),
    (1,{'w1':[.1]*4}),(0,{'v1x':[0]*4}),(1,{'v2':{'x':[0]*4,'y':[-1]*4}})])
def test_invalid_mass_layouts_and_geometry_are_rejected(pins,bad):
    points,row=fixture(0 if pins==0 else 1)
    if bad:row.update(bad)
    with pytest.raises(ValueError):project_mass_weighted_triangles(points,[row],pinned_nodes=pins)


@pytest.mark.parametrize('two,one',[(1,0),(-1,0),(0,2),(True,1)])
def test_dispatch_rejects_invalid_cumulative_prefixes(two,one):
    points,row=fixture(0)
    with pytest.raises(ValueError):project_compiled_triangles(points,[row],two_pin_count=two,one_pin_count=one)
