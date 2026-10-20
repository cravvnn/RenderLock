import copy

import numpy as np
import pytest

from agr.fe_quad_constraints import _basis,project_compiled_quads,project_free_quads


def fixture():
    points=np.array([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],float)
    basis=_basis(points[2]-points[0],points[3]-points[1])
    shape=points@basis.T
    points=np.repeat(points,4,axis=0)
    row=dict(nNode=np.arange(16).reshape(4,4).tolist(),vShape=np.repeat(shape[:,:,None],4,axis=2).reshape(4,12).tolist(),
             f4Weights=[[.25]*4]*4,f4Slack=[0.]*4)
    return points,row


def test_rest_shape_is_fixed_and_inputs_are_unchanged():
    points,row=fixture();before=points.copy();metadata=copy.deepcopy(row)
    result=project_free_quads(points,[row])
    np.testing.assert_allclose(result,points,atol=1e-12)
    np.testing.assert_array_equal(points,before);assert row==metadata


def test_rigid_world_transform_and_mass_center_are_preserved():
    points,row=fixture();points[8:12]+=[.4,.3,.2]
    rotation=np.array([[0,0,1],[1,0,0],[0,1,0]],float);translation=[15,3,-8]
    result=project_free_quads(points,[row])
    moved=project_free_quads(points@rotation.T+translation,[row])
    np.testing.assert_allclose(moved,result@rotation.T+translation,atol=1e-12)
    np.testing.assert_allclose(result.reshape(4,4,3).mean(axis=0),points.reshape(4,4,3).mean(axis=0),atol=1e-12)


def test_relaxation_controls_the_projection_amount():
    points,row=fixture();points[8:12]+=[.4,.3,.2]
    full=project_free_quads(points,[row]);zero=project_free_quads(points,[row],relaxation=[[0]*4])
    half=project_free_quads(points,[row],relaxation=[[.5]*4])
    np.testing.assert_allclose(zero,points,atol=1e-12)
    np.testing.assert_allclose(half,(full+points)/2,atol=1e-12)


def test_degenerate_inertia_has_finite_source_fallback():
    points,row=fixture();points[:]=0
    result=project_free_quads(points,[row])
    assert np.isfinite(result).all() and np.linalg.norm(result)>0


def test_padding_lanes_gather_original_points_then_last_write_wins():
    points,row=fixture();row['nNode']=[[0]*4,[4]*4,[8]*4,[12]*4]
    scales=[[.5,1,1.5,2]]
    actual=project_free_quads(points,[row],length_scales=scales)
    expected=project_free_quads(points,[row],length_scales=[[2]*4])
    np.testing.assert_array_equal(actual,expected)


@pytest.mark.parametrize('key,bad',[('f4Slack',[.1]*4),('f4Weights',[[0]*4,[.5]*4,[.25]*4,[.25]*4]),
    ('nNode',[[0]*4,[0]*4,[8]*4,[12]*4])])
def test_other_layouts_invalid_indices_and_slack_are_rejected(key,bad):
    points,row=fixture();row[key]=bad
    with pytest.raises(ValueError):project_free_quads(points,[row])


def test_dispatch_rejects_one_pin_layout_until_qualified():
    points,row=fixture()
    with pytest.raises(ValueError,match='one-pinned'):
        project_compiled_quads(points,[row],two_pin_count=0,one_pin_count=1)
