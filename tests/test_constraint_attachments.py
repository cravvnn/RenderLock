from copy import deepcopy
import numpy as np
import pytest

from agr.installed_map import _entity_key_hash
from agr.model_constraints import ModelConstraints
from agr.simulated_effect_package import model_constraints_identity


def record(name, attachment=False):
    return dict(m_sName=name, m_bIsAttachment=attachment, m_flWeight=1.,
                m_qOffset=[0,0,0,1], m_vOffset=[0,0,0])


def attachment():
    return dict(m_name='socket', m_nInfluences=1, m_influenceNames=['driver','',''],
                m_vInfluenceRotations=[[0,0,0,1]]*3, m_vInfluenceOffsets=[[2,0,0],[0,0,0],[0,0,0]],
                m_influenceWeights=[1.,0.,0.], m_bInfluenceRootTransform=[False]*3,
                m_bIgnoreRotation=False)


def definition(kind='CPointConstraint'):
    return dict(_class=kind, m_slaves=[record('slave')], m_targets=[record('socket',True)])


def test_named_and_hashed_attachment_uses_influence_and_current_writeback():
    names=['source','driver','slave','child','unrelated']
    parents=[-1,-1,-1,2,-1]
    first=dict(_class='CPointConstraint',m_slaves=[record('driver')],m_targets=[record('source')])
    second=definition()
    second['m_targets'][0].pop('m_sName')
    second['m_targets'][0]['m_nBoneHash']=_entity_key_hash('socket')
    solver=ModelConstraints(names,parents,[first,second],attachments=[attachment()])
    assert solver.required_names==set(names)-{'unrelated'}
    pose=np.tile([0.,0,0,1,0,0,0,1],(5,1));pose[0,0]=10;pose[3,1]=3
    before=pose.copy();result=solver.evaluate(pose,tick=1,names=names)
    np.testing.assert_array_equal(result[1:4,:3],[[10,0,0],[12,0,0],[12,3,0]])
    np.testing.assert_array_equal(result[4],before[4])
    np.testing.assert_array_equal(pose,before)


def test_attachment_quaternion_hemisphere_and_identity_target_normalization():
    table=[attachment()];table[0]['m_vInfluenceRotations'][0]=[0,0,0,-1.000004]
    solver=ModelConstraints(['driver','slave'],[-1,-1],[definition('COrientConstraint')],attachments=table)
    pose=np.array([[0.,0,0,1,0,0,0,1.000004],[0,0,0,1,0,0,0,1]])
    result=solver.evaluate(pose,tick=1,names=['driver','slave'])
    np.testing.assert_array_equal(result[1,4:],[0,0,0,1])


@pytest.mark.parametrize('change',[
    {'m_nInfluences':2},{'m_nInfluences':True},{'m_bIgnoreRotation':True},
    {'m_bInfluenceRootTransform':[True,False,False]},
    {'m_influenceWeights':[.5,.5,0]}, {'m_influenceNames':['missing','','']},
    {'m_influenceNames':['driver','extra','']}, {'m_vInfluenceOffsets':[[float('nan'),0,0]]*3},
    {'m_vInfluenceRotations':[[0,0,0,2]]*3}, {'m_influenceNames':[]},
])
def test_unqualified_attachment_modes_refuse(change):
    table=attachment();table.update(change)
    with pytest.raises(ValueError):
        ModelConstraints(['driver','slave'],[-1,-1],[definition()],attachments=[table])


@pytest.mark.parametrize('table',[None,[],[attachment(),attachment()]])
def test_missing_or_ambiguous_attachment_has_no_identity_fallback(table):
    with pytest.raises(ValueError,match='attachment'):
        ModelConstraints(['driver','slave'],[-1,-1],[definition()],attachments=table)


@pytest.mark.parametrize('kind',['CParentConstraint','CTiltTwistConstraint'])
def test_unqualified_attachment_operation_stays_refused(kind):
    with pytest.raises(ValueError,match='attachment'):
        ModelConstraints(['driver','slave'],[-1,-1],[definition(kind)],attachments=[attachment()])


def test_attachment_slave_refused():
    d=definition();d['m_slaves']=[record('socket',True)]
    with pytest.raises(ValueError,match='attachment'):
        ModelConstraints(['driver','slave'],[-1,-1],[d],attachments=[attachment()])


def test_attachment_table_participates_in_particle_model_identity():
    spec=dict(schema_version=1,names=['driver','slave'],parents=[-1,-1],definitions=[definition()],
              attachments=[attachment()])
    before=model_constraints_identity(spec)
    spec['attachments'][0]['m_vInfluenceOffsets'][0][0]=4
    assert model_constraints_identity(spec)!=before
