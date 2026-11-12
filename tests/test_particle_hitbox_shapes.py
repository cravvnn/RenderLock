import math
import numpy as np
import pytest
from agr.particle_hitbox_shapes import hitbox_bounds
from agr.particle_hitbox_shapes import prepare_hitbox_shapes


def box(**changes):
    return dict(shape='Capsule',min=[5,-1,3],max=[-2,8,3],radius=2,**changes)


def test_capsule_uses_both_endpoints_and_radius_even_when_reversed():
    result=hitbox_bounds(box())
    np.testing.assert_array_equal(result['min'],[-4,-3,1])
    np.testing.assert_array_equal(result['max'],[7,10,5])
    assert result['min'].dtype==np.float32


def test_sphere_uses_first_endpoint_and_box_retains_authored_bounds():
    result=hitbox_bounds(dict(box(),shape='Sphere'))
    np.testing.assert_array_equal(result['min'],[3,-3,1]);np.testing.assert_array_equal(result['max'],[7,1,5])
    result=hitbox_bounds(dict(box(),shape='Box',min=[-2,-1,3],max=[5,8,3]))
    np.testing.assert_array_equal(result['min'],[-2,-1,3]);np.testing.assert_array_equal(result['max'],[5,8,3])


def test_zero_radius_and_degenerate_endpoints_are_finite():
    result=hitbox_bounds(dict(box(),min=[3,2,1],max=[3,2,1],radius=0))
    np.testing.assert_array_equal(result['min'],result['max'])


@pytest.mark.parametrize('changes',[dict(shape='Unknown'),dict(min=[1,2]),dict(min=[True,0,0]),
    dict(max=[math.nan,0,0]),dict(radius=math.inf),dict(radius=-1),dict(radius=True),
    dict(min=[1e30,0,0]),dict(shape='Box')])
def test_missing_invalid_and_reversed_bounds_remain_refused(changes):
    with pytest.raises(ValueError):hitbox_bounds(dict(box(),**changes))


def test_paired_shapes_preserve_pose_order_and_compute_local_volume():
    poses=np.array([[1,2,3,2,0,0,0,1],[4,5,6,.5,0,0,0,1]],dtype='f4')
    shapes=[dict(box(),shape='Box',min=[0,0,0],max=[2,3,4]),dict(box(),shape='Sphere',min=[0,0,0],radius=1)]
    result=prepare_hitbox_shapes(shapes,poses,volume_weighted=True)
    np.testing.assert_array_equal(result['weights'],[24,8])
    assert result['total_weight']==32
    np.testing.assert_array_equal(result['transforms'],poses)
    poses[0,0]=900
    assert result['transforms'][0,0]==1
    assert 'mapping' not in result


def test_uniform_weight_and_zero_volume_are_distinct():
    item=dict(box(),min=[0,0,0],max=[0,0,0],radius=0)
    pose=[[0,0,0,1,0,0,0,1]]
    assert prepare_hitbox_shapes([item],pose)['total_weight']==1
    assert prepare_hitbox_shapes([item],pose,volume_weighted=True)['total_weight']==0


@pytest.mark.parametrize('boxes,poses,weighted', [([],[],False),([box()],[],False),
    ([box()],[[0]*7],False),([box()],[[math.nan]*8],False),([box()],[[0]*8],1)])
def test_unpaired_or_invalid_prepared_shapes_refused(boxes,poses,weighted):
    with pytest.raises(ValueError):prepare_hitbox_shapes(boxes,poses,volume_weighted=weighted)
