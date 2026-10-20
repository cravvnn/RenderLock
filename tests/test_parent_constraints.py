"""Authored parent constraints preserve the native combined-transform contract."""
import numpy as np
import pytest

from agr.model_constraints import ModelConstraints
from agr.model_constraint_math import parent_targets


NAMES=['a','b','slave','child','unrelated']


def record(name,weight=1):
    return dict(m_sName=name,m_flWeight=weight,m_qOffset=[0,0,0,1],m_vOffset=[0,0,0])


def solver(weights=(.6,.4),slave_weight=1):
    return ModelConstraints(NAMES,[-1,-1,-1,2,-1],[dict(_class='CParentConstraint',
        m_slaves=[record('slave',slave_weight)],m_targets=[record(n,w) for n,w in zip(NAMES,weights)])])


def pose():
    q=np.sqrt(.5)
    return np.array([[2,0,0,1,0,0,q,q],[12,0,0,3,0,0,q,q],[0,0,0,1,0,0,0,1],
        [1,0,0,1,0,0,0,1],[9,8,7,1,0,0,0,1]],dtype=float)


def test_parent_mean_moves_rotates_and_scales_descendants_once():
    before=pose();result=solver().evaluate(before,tick=1,names=NAMES)
    np.testing.assert_allclose(result[2,:4],[6,0,0,1.8],atol=1e-6)
    np.testing.assert_allclose(result[3,:4],[6,1.8,0,1.8],atol=2e-6)
    np.testing.assert_allclose(result[2:4,4:],np.tile(before[0,4:],(2,1)),atol=1e-7)
    np.testing.assert_array_equal(result[[0,1,4]],before[[0,1,4]])
    np.testing.assert_array_equal(before,pose())


def test_parent_intermediate_uses_raw_slave_weight_after_total_weight_gate():
    # Total .5 enables interpolation; the native interpolation receives raw1,
    # so this still reaches the normalized target instead of its halfway point.
    result=solver((.3,.2),1).evaluate(pose(),tick=1,names=NAMES)
    np.testing.assert_allclose(result[2,:4],[6,0,0,1.8],atol=1e-6)
    half=solver((.3,.2),.5).evaluate(pose(),tick=1,names=NAMES)
    np.testing.assert_allclose(half[2,:4],[3,0,0,1.4],atol=1e-6)
    np.testing.assert_allclose(half[3,:4],[3+1.4/np.sqrt(2),1.4/np.sqrt(2),0,1.4],atol=2e-6)


def test_parent_combined_weight_one_uses_full_target_before_raw_slave_blend():
    result=solver((1,1),.5).evaluate(pose(),tick=1,names=NAMES)
    np.testing.assert_allclose(result[2,:4],[7,0,0,2],atol=1e-6)


def test_parent_rotation_uses_normalized_linear_blend_not_orient_polynomial():
    values=pose();values[0,4:]=[0,0,np.sqrt(.75),.5]
    result=solver((1,),.25).evaluate(values,tick=1,names=NAMES)
    expected=[0,0,np.sqrt(3/52),7/(2*np.sqrt(13))]
    np.testing.assert_allclose(result[2,4:],expected,atol=1e-7)


@pytest.mark.parametrize('weights',[(0,),(-.5,.5)])
def test_zero_parent_strength_preserves_original_without_alias(weights):
    values=pose();result=solver(weights).evaluate(values,tick=1,names=NAMES)
    np.testing.assert_array_equal(result,values)
    assert not np.shares_memory(result,values)


def test_parent_scale_near_gate_and_full_propagation():
    values=np.array([[0,0,0,1,0,0,0,1],[2,0,0,1,0,0,0,1]],dtype=float)
    target=[0,0,0,1.00005,0,0,0,1]
    near=parent_targets(values,0,[1],[target],[1])
    np.testing.assert_array_equal(near[1],values[1])
    assert near[0,3]==pytest.approx(1.00005,abs=1e-7)
    target[3]=2
    moved=parent_targets(values,0,[1],[target],[1])
    np.testing.assert_array_equal(moved[1,:4],[4,0,0,2])


def test_parent_rotation_near_gate_uses_native_float32_boundary():
    values=np.array([[0,0,0,1,0,0,0,1],[0,2,0,1,0,0,0,1]],dtype=float)
    target=[0,0,0,1,np.float32(.1),0,0,np.sqrt(.99)]
    near=parent_targets(values,0,[1],[target],[1])
    np.testing.assert_array_equal(near[1],values[1])
    target[4]=.10001;target[7]=np.sqrt(1-target[4]**2)
    moved=parent_targets(values,0,[1],[target],[1])
    assert moved[1,2]>.39


def test_parent_offset_is_scaled_rotated_and_normalized_before_mean():
    target=dict(record('a'),m_qOffset=[0,0,1,0],m_vOffset=[2,0,0])
    model=ModelConstraints(NAMES,[-1,-1,-1,2,-1],[dict(_class='CParentConstraint',
        m_slaves=[record('slave')],m_targets=[target])])
    values=pose();values[0]=[4,0,0,2,0,0,1,0]
    result=model.evaluate(values,tick=1,names=NAMES)
    np.testing.assert_allclose(result[2,:4],[0,0,0,2],atol=1e-7)
    np.testing.assert_allclose(result[2,4:],[0,0,0,-1],atol=1e-7)
    np.testing.assert_allclose(result[3,:4],[2,0,0,2],atol=1e-7)


@pytest.mark.parametrize('count',[0,3])
def test_parent_unqualified_target_count_is_refused(count):
    with pytest.raises(ValueError,match='count'):
        ModelConstraints(['a'],[-1],[dict(_class='CParentConstraint',m_slaves=[record('a')],m_targets=[record('a')]*count)])


def test_parent_attachments_and_missing_binding_remain_refused():
    for target in (dict(record('a'),m_bIsAttachment=True),record('missing')):
        with pytest.raises(ValueError):
            ModelConstraints(['a'],[-1],[dict(_class='CParentConstraint',m_slaves=[record('a')],m_targets=[target])])


def test_parent_nonpositive_result_is_refused_without_input_mutation():
    values=pose();before=values.copy()
    with pytest.raises(ValueError,match='positive-uniform'):
        solver((-1,)).evaluate(values,tick=1,names=NAMES)
    np.testing.assert_array_equal(values,before)


@pytest.mark.parametrize('targets,weights,slave_weight',[
    ([[0,0,0,0,0,0,0,1]],[1],1),([[0,0,0,1,0,0,0,0]],[1],1),
    ([[0,0,0,1,0,0,0,float('nan')]],[1],1),([[0,0,0,1,0,0,0,1]],[float('inf')],1),
    ([[0,0,0,1,0,0,0,1]],[1],float('nan')),
    ([[0,0,0,1,0,0,0,1],[0,0,0,1,0,0,0,1]],[3e38,3e38],1)])
def test_parent_invalid_or_overflowing_prepared_targets_refused(targets,weights,slave_weight):
    with pytest.raises(ValueError):
        parent_targets(pose(),2,[3],targets,weights,slave_weight=slave_weight)
