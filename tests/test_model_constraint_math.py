import numpy as np
import pytest
from agr.model_constraint_math import (aim_look_rotation, aim_single, orient_single,
    orient_two_targets, quaternion_two_target_average, tilt_twist, twist_angle, twist_history)
from agr.vendor.pose_math import rotate


def pose():
    return np.array([[0,0,0,1,0,0,0,1], [1,0,0,1,0,0,0,1]], dtype=float)


def test_near_identity_moves_slave_but_leaves_descendant():
    values = pose()
    target = np.array([0,0,np.sin(.05),np.cos(.05)])
    result = orient_single(values,0,[1],target)
    np.testing.assert_allclose(result[0,4:],target)
    np.testing.assert_array_equal(result[1],values[1])
    np.testing.assert_array_equal(values,pose())


def test_large_rotation_carries_descendant_and_preserves_scale():
    result = orient_single(pose(),0,[1],[0,0,1,0])
    np.testing.assert_allclose(result[1,:4],[-1,0,0,1],atol=1e-12)
    np.testing.assert_allclose(result[1,4:],[0,0,1,0])


def test_zero_total_weight_skips_without_mutation():
    values = pose()
    result = orient_single(values,0,[1],[0,0,1,0],target_weight=0)
    np.testing.assert_array_equal(result,values)
    assert not np.shares_memory(result,values)


@pytest.mark.parametrize('weights',[(0,0),(-.5,.5)])
def test_cancelling_orientation_strength_leaves_original_pose(weights):
    values=pose()
    result=orient_two_targets(values,0,[1],[[0,0,1,0],[0,1,0,0]],weights)
    np.testing.assert_array_equal(result,values)
    assert not np.shares_memory(result,values)


@pytest.mark.parametrize('sign',[-1,1])
def test_opposite_quaternion_representations_keep_first_hemisphere(sign):
    a=np.array([0,0,0,sign])
    average,total=quaternion_two_target_average([a,-a],[.6,.4])
    # Original float32 log/exp leaves this tiny +Z-axis half-turn residue.
    expected=[0,0,-3.2584136988589307e-7,-1] if sign<0 else a
    np.testing.assert_allclose(average,expected,atol=1e-8)
    assert total==pytest.approx(1)


@pytest.mark.parametrize('rotations,weights',[
    ([[0,0,0,1]],[1]),([[0,0,0,1],[0,0,0,0]],[.5,.5]),
    ([[0,0,0,1],[0,0,0,float('nan')]],[.5,.5]),
    ([[0,0,0,1],[0,0,0,1]],[.5,float('inf')]),
    ([[0,0,0,1],[0,0,0,1]],[3e38,3e38]),
    ([[0,0,0,1.000006],[0,0,0,1.000006]],[-1e38,-1e38]),
    ([[0,0,0,.999994],[0,0,0,.999994]],[-1e38,-1e38])])
def test_invalid_or_overflowing_quaternion_average_is_refused(rotations,weights):
    with pytest.raises(ValueError):quaternion_two_target_average(rotations,weights)


@pytest.mark.parametrize('indices', [[0], [1,1], [-1], [2], [True]])
def test_invalid_descendant_binding_fails(indices):
    with pytest.raises(ValueError,match='indices'):
        orient_single(pose(),0,indices,[0,0,0,1])


def test_nonunit_target_and_nonpositive_scale_fail():
    with pytest.raises(ValueError,match='target'):
        orient_single(pose(),0,[1],[0,0,0,0])
    values = pose(); values[0,3] = 0
    with pytest.raises(ValueError,match='scales'):
        orient_single(values,0,[1],[0,0,0,1])


def test_twist_history_keeps_forward_turn_continuous():
    previous = twist_history(3.,1)
    result = twist_history(-3.,1,previous)
    assert result == pytest.approx(2*np.pi-3,abs=1e-6)


def test_twist_uses_parent_local_rotation_and_preserves_input():
    a=.4
    q=np.array([np.sin(a/2),0,0,np.cos(a/2)])
    values=np.array([[0,0,0,1,*q],[0,0,0,1,*q],[1,0,0,1,*q]],dtype=float)
    before=values.copy()
    result,angle=tilt_twist(values,2,[],1,target_parent=0,bone_parent=0)
    assert angle == pytest.approx(0,abs=1e-7)
    np.testing.assert_allclose(result[2,4:],q,atol=1e-7)
    np.testing.assert_array_equal(values,before)


@pytest.mark.parametrize('axis',[0,1,2])
def test_zero_twist_quaternion_uses_identity(axis):
    assert twist_angle([0,0,0,0],axis)==0


def test_invalid_twist_axis_and_history_fail():
    with pytest.raises(ValueError):
        twist_angle([0,0,0,1],3)
    with pytest.raises(ValueError):
        twist_history(0,1,float('nan'))


def test_aim_canonical_and_half_turn():
    np.testing.assert_array_equal(aim_look_rotation([0,1,0],[0,0,1]),[0,0,0,1])
    q=aim_look_rotation([0,-1,0],[0,0,-1])
    assert abs(q[0])==pytest.approx(1)
    np.testing.assert_allclose(q[1:],0,atol=1e-7)


@pytest.mark.parametrize('forward,up', [([0,0,0],[0,0,1]),([0,1,0],[0,1,0]),
                                      ([0,1,0],[0,float('nan'),1])])
def test_aim_degenerate_or_nonfinite_inputs_fail(forward,up):
    with pytest.raises(ValueError):
        aim_look_rotation(forward,up)


@pytest.mark.parametrize('up_type', [0,1,2,3])
def test_resolved_aim_points_forward_and_carries_child(up_type):
    values = np.array([[10,20,30,2,0,0,0,1],
                       [10,22,30,2,0,0,0,1],
                       [10,24,30,2,0,0,0,1]], dtype=float)
    before = values.copy()
    up_target = np.array([10,22,40,1,0,0,0,1])
    result = aim_single(values,1,[2],[20,22,30],bone_parent=0,
                        up_type=up_type,up_target=up_target)
    np.testing.assert_allclose(rotate(result[1,4:],[0,1,0]),[1,0,0],atol=1e-6)
    np.testing.assert_allclose(result[2,:3],[12,22,30],atol=1e-6)
    np.testing.assert_array_equal(result[1,:4],values[1,:4])
    np.testing.assert_array_equal(values,before)


def test_zero_weight_aim_skips_degenerate_direction():
    values=pose()
    result=aim_single(values,1,[],values[1,:3],bone_parent=0,slave_weight=0)
    np.testing.assert_array_equal(result,values)


@pytest.mark.parametrize('kwargs', [{'bone_parent':-1}, {'bone_parent':1},
    {'bone_parent':0,'up_type':2}, {'bone_parent':0,'up_type':4},
    {'bone_parent':0,'aim_offset':[0,0,0,0]}])
def test_invalid_resolved_aim_binding_fails(kwargs):
    with pytest.raises(ValueError):
        aim_single(pose(),1,[],[0,1,0],**kwargs)
