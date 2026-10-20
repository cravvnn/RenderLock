from copy import deepcopy
import numpy as np
import pytest

from agr.installed_map import _entity_key_hash
from agr.model_constraints import ModelConstraints

POLICY='native_disabled_missing_slave_v1'


def record(name):
    return dict(m_sName=name,m_flWeight=1.,m_qOffset=[0,0,0,1],m_vOffset=[0,0,0])


def missing(kind='COrientConstraint'):
    return dict(_class=kind,m_slaves=[dict(record(''),m_nBoneHash=_entity_key_hash('absent'))],
                m_targets=[record('target')])


@pytest.mark.parametrize('kind',['COrientConstraint','CAimConstraint','CTiltTwistConstraint'])
def test_qualified_missing_slave_is_retained_reported_pose_preserving_noop(kind):
    definitions=[missing(kind)];before=deepcopy(definitions)
    solver=ModelConstraints(['target'],[-1],definitions,binding_policy=POLICY)
    assert definitions==before and solver.active_constraint_count==0
    assert solver.affected_names==solver.required_names==frozenset()
    assert solver.disabled_constraints[0]['reason_code']=='native_disabled_missing_slave'
    assert solver.disabled_constraints[0]['index']==0
    assert solver.disabled_constraints[0]['bone_hash']==_entity_key_hash('absent')
    pose=np.array([[4.,5,6,2,0,0,0,1]])
    np.testing.assert_array_equal(solver.evaluate(pose,tick=1,names=['target']),pose)


def test_default_policy_and_unqualified_modes_still_refuse_missing_bones():
    for kwargs,definition in [({},missing()),({'binding_policy':POLICY},missing('CPointConstraint')),
                              ({'binding_policy':POLICY},missing('CParentConstraint'))]:
        with pytest.raises(ValueError,match='bone hash'):
            ModelConstraints(['target'],[-1],[definition],**kwargs)


def test_missing_target_and_named_slave_are_not_inferred_disabled():
    definitions=[dict(_class='COrientConstraint',m_slaves=[record('target')],m_targets=[record('absent')]),
                 dict(_class='COrientConstraint',m_slaves=[record('absent')],m_targets=[record('target')])]
    for definition in definitions:
        with pytest.raises(ValueError,match='bone hash'):
            ModelConstraints(['target'],[-1],[definition],binding_policy=POLICY)


def test_active_order_and_history_index_survive_reported_disabled_definitions():
    definitions=[missing(),dict(_class='COrientConstraint',m_slaves=[record('middle')],m_targets=[record('target')]),
                 missing('CAimConstraint'),dict(_class='COrientConstraint',m_slaves=[record('last')],m_targets=[record('middle')])]
    solver=ModelConstraints(['target','middle','last'],[-1,-1,-1],definitions,binding_policy=POLICY)
    assert solver.active_constraint_count==2 and [r['index'] for r in solver.disabled_constraints]==[0,2]
    pose=np.array([[0.,0,0,1,0,0,1,0],[0,0,0,1,0,0,0,1],[0,0,0,1,0,0,0,1]])
    result=solver.evaluate(pose,tick=1,names=solver.names)
    np.testing.assert_array_equal(result[:,4:],np.tile([0,0,1,0],(3,1)))
    with pytest.raises(ValueError,match='increasing'):
        solver.evaluate(pose,tick=1,names=solver.names)


@pytest.mark.parametrize('hash_value',[-1,2**32,True])
def test_bad_hash_width_is_not_a_native_disabled_binding(hash_value):
    definition=missing();definition['m_slaves'][0]['m_nBoneHash']=hash_value
    with pytest.raises(ValueError,match='bone hash'):
        ModelConstraints(['target'],[-1],[definition],binding_policy=POLICY)


def test_disabled_policy_does_not_hide_nonfinite_weights_or_unknown_operations():
    definition=missing();definition['m_slaves'][0]['m_flWeight']=float('nan')
    with pytest.raises(ValueError,match='Nonfinite'):
        ModelConstraints(['target'],[-1],[definition],binding_policy=POLICY)
    with pytest.raises(ValueError,match='Unsupported model constraint'):
        ModelConstraints(['target'],[-1],[missing('UnknownConstraint')],binding_policy=POLICY)
    with pytest.raises(ValueError,match='policy'):
        ModelConstraints(['target'],[-1],[],binding_policy='guess')
