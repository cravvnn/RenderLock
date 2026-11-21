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


def test_parent_offset_is_scaled_rotated_and_normalized_before_mean():
    target=dict(record('a'),m_qOffset=[0,0,1,0],m_vOffset=[2,0,0])
    model=ModelConstraints(NAMES,[-1,-1,-1,2,-1],[dict(_class='CParentConstraint',
        m_slaves=[record('slave')],m_targets=[target])])
    values=pose();values[0]=[4,0,0,2,0,0,1,0]
    result=model.evaluate(values,tick=1,names=NAMES)
    np.testing.assert_allclose(result[2,:4],[0,0,0,2],atol=1e-7)
    np.testing.assert_allclose(result[2,4:],[0,0,0,-1],atol=1e-7)
    np.testing.assert_allclose(result[3,:4],[2,0,0,2],atol=1e-7)


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
    ([[0,0,0,1,0,0,0,1],[0,0,0,1,0,0,0,1]],[3e38,3e38],1)])
def test_parent_invalid_or_overflowing_prepared_targets_refused(targets,weights,slave_weight):
    with pytest.raises(ValueError):
        parent_targets(pose(),2,[3],targets,weights,slave_weight=slave_weight)
