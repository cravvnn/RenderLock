import copy
import math
import numpy as np
import pytest
from agr.fe_node_rotations import apply_twist_constraints,apply_parent_locks


def setup_nodes():
    rest=np.zeros((3,8));rest[:,3]=1;rest[:,7]=1
    rest[:,0]=[0,1,2]
    return rest,rest.copy(),rest.copy(),rest[:,:3].copy()


def declaration(a=0,b=1,twist=1,swing=1):
    return dict(nNodeOrient=a,nNodeEnd=b,flTwistRelax=twist,flSwingRelax=swing)


def x_rotation(degrees):
    angle=math.radians(degrees)/2
    return np.array([math.sin(angle),0,0,math.cos(angle)])


@pytest.mark.parametrize('twist,swing,expected',[(1,0,0),(0,1,90),(.5,.5,45)])
def test_axial_twist_and_swing_are_independent(twist,swing,expected):
    rest,previous,current,positions=setup_nodes()
    current[0,4:]=x_rotation(90)
    result=apply_twist_constraints(rest,previous,current,positions,[declaration(twist=twist,swing=swing)])
    np.testing.assert_allclose(result[0,4:],x_rotation(expected),atol=1e-12)


@pytest.mark.parametrize('twist,swing,expected',[(1,0,[0,0,0,1]),(0,1,[0,0,2**-.5,2**-.5])])
def test_transverse_motion_uses_swing(twist,swing,expected):
    rest,previous,current,positions=setup_nodes()
    positions[1]=[0,1,0]
    result=apply_twist_constraints(rest,previous,current,positions,[declaration(twist=twist,swing=swing)])
    np.testing.assert_allclose(result[0,4:],expected,atol=1e-12)


def test_native_antiparallel_axis_retains_interpolation_direction():
    rest,previous,current,positions=setup_nodes()
    rest[1,:3]=[0,0,1];current[:]=rest;previous[:]=rest;positions[:]=rest[:,:3]
    positions[1]=[0,0,-1]
    result=apply_twist_constraints(rest,previous,current,positions,[declaration(twist=.5,swing=.5)])
    np.testing.assert_allclose(result[0,4:],[0,2**-.5,0,2**-.5],atol=1e-12)


def test_order_and_repeated_directed_declarations_are_preserved():
    rest,previous,current,positions=setup_nodes()
    current[2,4:]=x_rotation(90)
    ordered=[declaration(1,2),declaration(0,1)]
    forward=apply_twist_constraints(rest,previous,current,positions,ordered)
    reverse=apply_twist_constraints(rest,previous,current,positions,ordered[::-1])
    np.testing.assert_allclose(forward[0,4:],x_rotation(90),atol=1e-12)
    np.testing.assert_allclose(reverse[0,4:],x_rotation(0),atol=1e-12)
    current[1,4:]=x_rotation(90)
    repeated=apply_twist_constraints(rest,previous,current,positions,[declaration(twist=.5,swing=.5)]*2)
    np.testing.assert_allclose(repeated[0,4:],x_rotation(67.5),atol=1e-12)


def test_inputs_and_unselected_nodes_are_preserved_and_source_scale_is_used():
    values=setup_nodes();rest,previous,current,positions=values
    positions[0]=[5,6,7];previous[0,3]=1.2;current[0,3]=1.8
    snapshots=[v.copy() for v in values]
    rows=[declaration()];original=copy.deepcopy(rows)
    result=apply_twist_constraints(*values,rows)
    np.testing.assert_array_equal(result[0,:4],[5,6,7,1.2])
    np.testing.assert_array_equal(result[1:],current[1:])
    for value,before in zip(values,snapshots):np.testing.assert_array_equal(value,before)
    assert rows==original


@pytest.mark.parametrize('key,value',[('nNodeOrient',-1),('nNodeEnd',3),('nNodeEnd',True),
    ('flTwistRelax',float('nan')),('flSwingRelax',1.01),('flTwistRelax',True)])
def test_invalid_declarations_fail_before_modification(key,value):
    values=setup_nodes();row=declaration();row[key]=value
    with pytest.raises(ValueError):apply_twist_constraints(*values,[row])


def test_degenerate_rotation_and_nonfinite_position_are_rejected():
    rest,previous,current,positions=setup_nodes();current[0,4:]=0
    with pytest.raises(ValueError):apply_twist_constraints(rest,previous,current,positions,[declaration()])
    current[:]=rest;positions[0,0]=float('inf')
    with pytest.raises(ValueError):apply_twist_constraints(rest,previous,current,positions,[declaration()])


def test_parent_lock_uses_current_scaled_frame_and_recorded_parent_scale():
    rest,previous,current,_=setup_nodes()
    current[0,:4]=[1,2,3,2]
    current[0,4:]=[0,0,2**-.5,2**-.5]
    previous[0,3]=.7;previous[1,3]=1.9
    original=current.copy()
    result=apply_parent_locks(current,previous,[dict(nCtrlParent=0,nCtrlChild=1,vOffset=[1,0,0])])
    np.testing.assert_allclose(result[1,:4],[1,4,3,.7],atol=1e-12)
    np.testing.assert_array_equal(result[1,4:],original[1,4:])
    np.testing.assert_array_equal(current,original)
    np.testing.assert_array_equal(result[[0,2]],original[[0,2]])


def test_parent_lock_chain_uses_earlier_position_writes_and_inherited_scale():
    rest,previous,current,_=setup_nodes()
    current[0,3]=2;previous[0,3]=3
    rows=[dict(nCtrlParent=0,nCtrlChild=1,vOffset=[1,0,0]),
          dict(nCtrlParent=1,nCtrlChild=2,vOffset=[1,0,0])]
    result=apply_parent_locks(current,previous,rows)
    np.testing.assert_allclose(result[:,0],[0,2,5],atol=1e-12)
    np.testing.assert_allclose(result[:,3],[2,3,1],atol=1e-12)


@pytest.mark.parametrize('row',[dict(nCtrlParent=3,nCtrlChild=0,vOffset=[0,0,0]),
    dict(nCtrlParent=0,nCtrlChild=True,vOffset=[0,0,0]),
    dict(nCtrlParent=0,nCtrlChild=1,vOffset=[0,float('nan'),0])])
def test_parent_lock_identity_and_offset_bounds_are_strict(row):
    rest,previous,current,_=setup_nodes()
    with pytest.raises(ValueError):apply_parent_locks(current,previous,[row])
