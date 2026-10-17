import numpy as np
import pytest
from agr.model_constraints import ModelConstraints


def record(name, weight=1):
    return dict(m_sName=name,m_flWeight=weight,m_qOffset=[0,0,0,1],m_vOffset=[0,0,0])


def test_point_binding_offset_weight_and_descendant_writeback():
    names=['target','slave','child','unrelated']
    target=dict(record('target', .5),m_vOffset=[2,0,0])
    solver=ModelConstraints(names,[-1,-1,1,-1],
        [dict(_class='CPointConstraint',m_slaves=[record('slave')],m_targets=[target])])
    pose=np.array([[4,0,0,2,0,0,1,0],[0,0,0,1,0,0,0,1],
                   [0,3,0,1,0,0,0,1],[7,8,9,1,0,0,0,1]],dtype=float)
    before=pose.copy()
    # Rotated, scaled target offset cancels target translation.
    np.testing.assert_array_equal(solver.evaluate(pose,tick=1,names=names),pose)
    pose[0,0]=8
    result=solver.evaluate(pose,tick=2,names=names)
    np.testing.assert_allclose(result[1:3,:3],[[2,0,0],[2,3,0]])
    np.testing.assert_array_equal(result[:,3:],pose[:,3:])
    np.testing.assert_array_equal(result[3],before[3])


@pytest.mark.parametrize('weight,expected',[(0,0),(.000099,0),(.000101,.000202),(.5,1),(1,2),(-1,-2),(2,4)])
def test_point_weights(weight,expected):
    from agr.model_constraint_math import point_single
    pose=np.array([[0,0,0,1,0,0,0,1],[0,3,0,1,0,0,0,1]],dtype=float)
    before=pose.copy()
    result=point_single(pose,0,[1],[2,0,0],slave_weight=weight)
    np.testing.assert_allclose(result[:,0],[expected,expected],atol=1e-8)
    np.testing.assert_array_equal(pose,before)


@pytest.mark.parametrize('target,weight',[([float('nan'),0,0],1),([0,0],1),([0,0,0],float('inf')),([0,0,0],1e300)])
def test_point_invalid_inputs_rejected(target,weight):
    from agr.model_constraint_math import point_single
    with pytest.raises(ValueError):
        point_single([[0,0,0,1,0,0,0,1]],0,[],target,slave_weight=weight)


def test_ordered_constraints_observe_previous_writeback():
    names=['target','middle','last']
    definitions=[dict(_class='COrientConstraint',m_slaves=[record(slave)],
                      m_targets=[record(target)]) for slave,target in [('middle','target'),('last','middle')]]
    solver=ModelConstraints(names,[-1,-1,-1],definitions)
    pose=np.array([[0,0,0,1,0,0,1,0],[0,0,0,1,0,0,0,1],[0,0,0,1,0,0,0,1]],dtype=float)
    before=pose.copy()
    result=solver.evaluate(pose,tick=10,names=names)
    np.testing.assert_array_equal(result[:,4:],np.tile([0,0,1,0],(3,1)))
    np.testing.assert_array_equal(pose,before)
    with pytest.raises(ValueError,match='increasing'):
        solver.evaluate(pose,tick=10,names=names)


def test_failed_input_does_not_advance_stream():
    solver=ModelConstraints(['a'],[-1],[])
    pose=np.array([[0,0,0,1,0,0,0,1]],dtype=float)
    with pytest.raises(ValueError,match='identity'):
        solver.evaluate(pose,tick=10,names=['wrong'])
    np.testing.assert_array_equal(solver.evaluate(pose,tick=10,names=['a']),pose)


@pytest.mark.parametrize('names,parents', [(['a','a'],[-1,-1]),(['a','b'],[1,0]),
                                          (['a'],[1]),(['a'],[True])])
def test_invalid_hierarchy_or_identity_rejected(names,parents):
    with pytest.raises(ValueError):
        ModelConstraints(names,parents,[])


def test_attachment_and_unknown_bone_rejected():
    for target in [dict(record('a'),m_bIsAttachment=True),record('missing')]:
        with pytest.raises(ValueError):
            ModelConstraints(['a'],[-1],[dict(_class='COrientConstraint',
                             m_slaves=[record('a')],m_targets=[target])])


def test_entity_constraint_spec_survives_asset_resolution(tmp_path):
    from agr.blender_scene import asset_spec
    spec = {'schema_version':1,'names':['a'],'parents':[-1],'definitions':[]}
    assert asset_spec({'model_constraints':spec},None,tmp_path)['model_constraints'] == spec


def test_affected_bones_include_descendants_only():
    solver=ModelConstraints(['target','slave','child','unrelated'],[-1,-1,1,-1],
        [dict(_class='COrientConstraint',m_slaves=[record('slave')],m_targets=[record('target')])])
    assert solver.affected_names == {'slave','child'}
    assert solver.required_names == {'target','slave','child'}


def test_required_bones_include_target_and_slave_parents():
    solver=ModelConstraints(['root','target','parent','slave','cloth'],[-1,0,0,2,-1],
        [dict(_class='CPointConstraint',m_slaves=[record('slave')],m_targets=[record('target')])])
    assert solver.required_names == {'root','target','parent','slave'}
    assert solver.affected_names == {'slave'}
