import numpy as np
import pytest

from agr.fe_node_rotations import apply_goal_locks,apply_parent_locks


def arrays():
    current=np.zeros((3,8));current[:,3]=2;current[:,7]=1
    previous=current.copy();previous[:,3]=[.5,.75,1]
    positions=np.array([[1.,2,3],[4,5,6],[7,8,9]])
    return current,previous,positions


def test_goal_lock_uses_final_positions_and_recorded_scale_with_current_rotation():
    current,previous,positions=arrays();original=current.copy()
    current[1,4:]=[0,1,0,0];original=current.copy()
    result=apply_goal_locks(current,previous,positions,[1,1])
    np.testing.assert_array_equal(result[1,:3],positions[1])
    assert result[1,3]==.75
    np.testing.assert_array_equal(result[:,4:],current[:,4:])
    np.testing.assert_array_equal(result[[0,2]],current[[0,2]])
    np.testing.assert_array_equal(current,original)


def test_goal_then_parent_tail_order_keeps_recorded_parent_scale():
    current,previous,positions=arrays()
    goal=apply_goal_locks(current,previous,positions,[0])
    result=apply_parent_locks(goal,previous,[dict(nCtrlParent=0,nCtrlChild=1,vOffset=[2,0,0])])
    np.testing.assert_array_equal(result[1,:3],[2,2,3])
    assert result[1,3]==.5


@pytest.mark.parametrize('indices',[[-1],[3],[True]])
def test_invalid_goal_indices_rejected(indices):
    with pytest.raises(ValueError,match='outside'):apply_goal_locks(*arrays(),indices)


def test_bad_goal_positions_rejected():
    current,previous,positions=arrays();positions[0,0]=float('nan')
    with pytest.raises(ValueError,match='positions'):apply_goal_locks(current,previous,positions,[1])
