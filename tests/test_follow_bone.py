"""Regress the model-space intermediate used by the supplied FollowBone task."""
import numpy as np
import pytest
from agr.vendor.pose_math import Pose, follow_bone, globals_for


def z_rotation(degrees):
    angle=np.radians(degrees)/2
    return np.array([0.,0.,np.sin(angle),np.cos(angle)])


def test_mode_two_uses_local_orientation_before_parent_inverse():
    parents=np.array([-1,0,0])
    p=Pose(np.array([z_rotation(90),z_rotation(45),z_rotation(20)]),
           np.array([[10.,20.,30.],[1.,2.,3.],[4.,6.,8.]]),np.array([2.,.7,1.25]))
    original=p.copy()
    result=follow_bone(p,parents,1,2,2)
    # The target supplies model-space location/scale; the driven LOCAL 45-degree
    # orientation becomes a -45-degree local result beneath its 90-degree parent.
    np.testing.assert_allclose(result.q[1],z_rotation(-45),atol=1e-12)
    np.testing.assert_allclose(result.t[1],[4,6,8],atol=1e-12)
    np.testing.assert_allclose(result.s[1],1.25,atol=1e-12)
    actual_global=globals_for(result,parents)
    np.testing.assert_allclose(actual_global.q[1],z_rotation(45),atol=1e-12)
    for field in ['q','t','s']:
        np.testing.assert_array_equal(getattr(result,field)[[0,2]],getattr(p,field)[[0,2]])
        np.testing.assert_array_equal(getattr(p,field),getattr(original,field))


def test_mode_zero_still_copies_entire_target_transform():
    parents=np.array([-1,0,0])
    p=Pose(np.array([z_rotation(90),z_rotation(45),z_rotation(20)]),
           np.array([[10.,20.,30.],[1.,2.,3.],[4.,6.,8.]]),np.array([2.,.7,1.25]))
    result=follow_bone(p,parents,1,2,0)
    for field in ['q','t','s']:
        np.testing.assert_allclose(getattr(result,field)[1],getattr(p,field)[2],atol=1e-12)


@pytest.mark.parametrize('mode',[1,3,-1,4])
def test_unvalidated_follow_modes_still_fail(mode):
    p=Pose(np.tile([0.,0.,0.,1.],(2,1)),np.zeros((2,3)),np.ones(2))
    with pytest.raises(ValueError,match='Unsupported FollowBone mode'):
        follow_bone(p,np.array([-1,0]),1,0,mode)
