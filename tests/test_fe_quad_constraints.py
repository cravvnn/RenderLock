import copy

import numpy as np
import pytest

from agr.fe_quad_constraints import project_two_pin_quads


def fixture():
    nodes=np.arange(16).reshape(4,4)
    shape=np.zeros((4,3,4));shape[0,0]=-1;shape[1,0]=1
    shape[2,1]=1;shape[3,1]=2
    block=dict(nNode=nodes.tolist(),vShape=shape.reshape(4,12).tolist(),
               f4Weights=[[0.]*4,[0.]*4,[.4]*4,[.6]*4],f4Slack=[0.]*4)
    points=np.stack([shape[i,:,lane] for i in range(4) for lane in range(4)])
    return points,block


def test_rest_shape_and_pinned_nodes_preserved():
    points,block=fixture();original=points.copy();declaration=copy.deepcopy(block)
    result=project_two_pin_quads(points,[block])
    np.testing.assert_allclose(result,points,atol=1e-12)
    np.testing.assert_array_equal(points,original)
    assert block==declaration


def test_rotation_around_pinned_axis_is_preserved_with_explicit_scale():
    points,block=fixture()
    points[8:,1:]=points[8:,1,None]*np.array([2**-.5,2**-.5])
    result=project_two_pin_quads(points,[block],length_scales=[[2]*4])
    np.testing.assert_allclose(result[8:],points[8:]*2,atol=1e-12)
    np.testing.assert_array_equal(result[:8],points[:8])


def test_zero_axis_and_symmetric_offsets_take_native_fallback():
    points,block=fixture();points[:]=0
    result=project_two_pin_quads(points,[block])
    np.testing.assert_allclose(result[8:12],[[0,1,0]]*4)
    np.testing.assert_allclose(result[12:],[[0,2,0]]*4)


@pytest.mark.parametrize('kind',['index','pin','slack','shape','weight'])
def test_unqualified_compiled_layout_rejected(kind):
    points,block=fixture()
    if kind=='index':block['nNode'][0][0]=16
    elif kind=='pin':block['f4Weights'][0][0]=.1
    elif kind=='slack':block['f4Slack'][0]=.1
    elif kind=='shape':block['vShape'][0]=[]
    else:block['f4Weights'][2][0]=float('nan')
    with pytest.raises(ValueError):project_two_pin_quads(points,[block])
