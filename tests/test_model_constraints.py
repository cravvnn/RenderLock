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


@pytest.mark.parametrize('weights,slave_weight,expected_x',[
    ([.6,.4],1,6),([.6,.4],.5,3),([.3,.2],1,3),
    ([-.5,1.5],1,17),([-.5,.5],1,0),([0,0],1,0),([.6,.4],0,0)])
def test_two_point_targets_normalize_then_apply_total_and_slave_weights(weights,slave_weight,expected_x):
    names=['target_a','target_b','slave','child','unrelated']
    solver=ModelConstraints(names,[-1,-1,-1,2,-1],[dict(_class='CPointConstraint',
        m_slaves=[record('slave',slave_weight)],m_targets=[record('target_a',weights[0]),record('target_b',weights[1])])])
    pose=np.array([[2,0,0,1,0,0,0,1],[12,0,0,1,0,0,0,1],[0,0,0,1,0,0,0,1],
        [0,3,0,1,0,0,0,1],[9,8,7,1,0,0,0,1]],dtype=float)
    before=pose.copy();result=solver.evaluate(pose,tick=1,names=names)
    np.testing.assert_allclose(result[2:4,0],expected_x,atol=2e-6)
    np.testing.assert_array_equal(result[:,3:],pose[:,3:])
    np.testing.assert_array_equal(result[[0,1,4]],before[[0,1,4]])
    np.testing.assert_array_equal(pose,before)


def test_two_point_target_offsets_observe_exact_bound_transforms():
    solver=ModelConstraints(['a','b','slave'],[-1,-1,-1],[dict(_class='CPointConstraint',
        m_slaves=[record('slave')],m_targets=[dict(record('a',.5),m_vOffset=[2,0,0]),record('b',.5)])])
    pose=np.array([[4,0,0,2,0,0,1,0],[8,0,0,1,0,0,0,1],[0,0,0,1,0,0,0,1]],dtype=float)
    np.testing.assert_allclose(solver.evaluate(pose,tick=1,names=['a','b','slave'])[2,:3],[4,0,0],atol=1e-7)


@pytest.mark.parametrize('count',[0,3])
def test_unqualified_point_target_count_still_refused(count):
    with pytest.raises(ValueError,match='count'):
        ModelConstraints(['a'],[-1],[dict(_class='CPointConstraint',m_slaves=[record('a')],m_targets=[record('a')]*count)])


def test_two_point_attachment_target_remains_strictly_refused():
    with pytest.raises(ValueError,match='attachment'):
        ModelConstraints(['a','b'],[-1,-1],[dict(_class='CPointConstraint',m_slaves=[record('a')],
            m_targets=[record('a',.5),dict(record('b',.5),m_bIsAttachment=True)])])


@pytest.mark.parametrize('points,weights',[
    ([[0,0,0],[float('nan'),0,0]],[.5,.5]),([[0,0,0],[1,0,0]],[1,float('inf')]),
    ([[1e38,0,0],[1e38,0,0]],[1e38,1e38]),([[0,0,0]],[1])])
def test_two_point_target_invalid_or_overflowing_inputs_refused(points,weights):
    from agr.model_constraint_math import point_two_targets
    with pytest.raises(ValueError):
        point_two_targets([[0,0,0,1,0,0,0,1]],0,[],points,weights)


def test_two_orientation_targets_use_log_mean_and_total_slave_strength():
    names=['a','b','slave','child','unrelated']
    solver=ModelConstraints(names,[-1,-1,-1,2,-1],[dict(_class='COrientConstraint',
        m_slaves=[record('slave',.5)],m_targets=[record('a',.5),record('b',.5)])])
    q=np.sqrt(.5)
    pose=np.array([[0,0,0,1,q,0,0,q],[0,0,0,1,0,q,0,q],[0,0,0,1,0,0,0,1],
        [1,0,0,1,0,0,0,1],[7,8,9,1,0,0,0,1]],dtype=float)
    before=pose.copy();result=solver.evaluate(pose,tick=1,names=names)
    # The log mean has a pi/(2sqrt(2)) rotation, then the .5 slave strength.
    # Its quaternion differs from normalized linear blending of the targets.
    angle=np.pi/(8*np.sqrt(2))
    expected=[np.sin(angle)/np.sqrt(2),np.sin(angle)/np.sqrt(2),0,np.cos(angle)]
    np.testing.assert_allclose(result[2,4:],expected,atol=3e-6)
    np.testing.assert_array_equal(result[[0,1,4]],before[[0,1,4]])
    np.testing.assert_array_equal(pose,before)
    assert solver.affected_names=={'slave','child'}
    assert solver.required_names==set(names)-{'unrelated'}


def test_two_target_offset_extrapolation_matches_original_native_regression():
    # Synthetic RNG2026101903 case10, original current COrient Evaluate.
    # A double-precision blend polynomial diverged by about 2e-5 here.
    names=['a','slave','b']
    qa=[.3059106171131134,.6925615072250366,.3800508379936218,.5313555002212524]
    old=[-.35963818430900574,.15476840734481812,-.7164747714996338,-.5773828029632568]
    qb=[.5570469498634338,.5012766718864441,.6072612404823303,.2639206349849701]
    targets=[dict(record('a',-1),m_qOffset=[0,0,1,0]),dict(record('b',-.5),m_qOffset=[1,0,0,0])]
    solver=ModelConstraints(names,[-1,-1,-1],[dict(_class='COrientConstraint',
        m_slaves=[record('slave',2)],m_targets=targets)])
    pose=np.array([[0,0,0,1,*qa],[0,0,0,1,*old],[0,0,0,1,*qb]])
    result=solver.evaluate(pose,tick=1,names=names)
    expected=[-.5830185413360596,.5829595327377319,-.2656966745853424,-.49965283274650574]
    np.testing.assert_allclose(result[1,4:],expected,atol=1e-7,rtol=0)
    np.testing.assert_array_equal(result[[0,2]],pose[[0,2]])


def test_target_offset_compose_normalizes_admitted_unit_roundoff():
    solver=ModelConstraints(['target','slave'],[-1,-1],[dict(_class='COrientConstraint',
        m_slaves=[record('slave')],m_targets=[dict(record('target'),m_qOffset=[0,0,1.000006,0])])])
    pose=np.array([[0,0,0,1,0,0,1.000006,0],[0,0,0,1,0,0,0,1]])
    result=solver.evaluate(pose,tick=1,names=['target','slave'])
    np.testing.assert_array_equal(result[1,4:],[0,0,0,-1])


@pytest.mark.parametrize('count',[0,3])
def test_unqualified_orientation_count_still_refused(count):
    with pytest.raises(ValueError,match='count'):
        ModelConstraints(['a'],[-1],[dict(_class='COrientConstraint',m_slaves=[record('a')],m_targets=[record('a')]*count)])


def test_two_orientation_target_attachment_still_refused():
    with pytest.raises(ValueError,match='attachment'):
        ModelConstraints(['a','b'],[-1,-1],[dict(_class='COrientConstraint',m_slaves=[record('a')],
            m_targets=[record('a',.5),dict(record('b',.5),m_bIsAttachment=True)])])


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
