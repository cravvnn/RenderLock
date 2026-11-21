import copy

import numpy as np
import pytest

from agr.fe_kelager_bends import project_kelager_bends


def fixture():
    return np.array([[0,2,0],[-1,0,0],[1,0,0],[2,1,1]],float),dict(
        nNode=[0,1,2],flWeight=[-1.,.5,.5],flHeight0=.5,nReserved=0)


def test_height_projection_preserves_center_and_signed_distribution():
    points,row=fixture();before=points.copy();metadata=copy.deepcopy(row)
    actual=project_kelager_bends(points,[row])
    np.testing.assert_allclose(actual[:3].mean(axis=0),points[:3].mean(axis=0),atol=1e-12)
    assert abs(np.linalg.norm(actual[0]-actual[:3].mean(axis=0))-.5)<1e-6
    np.testing.assert_allclose(actual[1]-points[1],-(actual[0]-points[0])*.5,atol=1e-12)
    np.testing.assert_array_equal(actual[3],points[3])
    np.testing.assert_array_equal(points,before);assert row==metadata


def test_height_is_an_upper_bound_and_zero_coefficients_keep_nodes_pinned():
    points,row=fixture();row['flHeight0']=10
    np.testing.assert_array_equal(project_kelager_bends(points,[row]),points)
    row['flHeight0']=.1;row['flWeight']=[0,3,0]
    actual=project_kelager_bends(points,[row])
    np.testing.assert_array_equal(actual[[0,2,3]],points[[0,2,3]])
    assert np.linalg.norm(actual[1]-points[1])>0


def test_ordered_constraints_preserve_sequential_shared_node_updates():
    points,row=fixture();other=copy.deepcopy(row);other['nNode']=[1,2,3]
    forward=project_kelager_bends(points,[row,other]);reverse=project_kelager_bends(points,[other,row])
    assert np.max(abs(forward-reverse))>.01


@pytest.mark.parametrize('key,value',[('nNode',[0,0,1])])
def test_malformed_or_unqualified_declarations_reject(key,value):
    points,row=fixture();row[key]=value
    with pytest.raises(ValueError):project_kelager_bends(points,[row])
