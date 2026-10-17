import numpy as np
import pytest
from agr.vendor.decode_pose_recipes import parse
from agr.vendor.evaluate_recipes import Evaluator
from agr.vendor.pose_math import Pose


def recipe():
    return dict(payload_hex='00000000',candidate_task_data_start_bit=32,
                candidate_dependency_bits=1,candidate_task_type_ids_4bit=[12],
                topology_tasks=[dict(type_id=12,dependencies=[])],hero_id=78,
                demo_tick=1,recipe_age_ticks=0)


def test_zero_pose_has_no_dynamic_values_or_dependencies():
    result=parse(recipe(),bone_bits=1)
    assert result['complete_layout'] and result['consumed_bits']==32
    assert result['tasks'][0]['dependencies']==[]
    assert result['error'] is None and result['unsupported'] is None


def test_zero_pose_with_forged_dependency_is_rejected():
    row=recipe();row['topology_tasks'][0]['dependencies']=[0]
    result=parse(row,bone_bits=1)
    assert not result['complete_layout'] and 'cannot have dependencies' in result['error']


def evaluator(tmp_path):
    skeleton=dict(m_boneIDs=['root','child'],m_parentIndices=[-1,0],
                  m_parentSpaceReferencePose=[[4,5,6,1,0,0,0,1],[1,2,3,1,0,0,0,1]],
                  m_maskDefinitions=[])
    return Evaluator('fixture',skeleton,tmp_path)


@pytest.mark.parametrize('weight',[0,127,255])
def test_zero_pose_is_neutral_in_additive_blend_at_every_weight(tmp_path,weight):
    ev=evaluator(tmp_path)
    primary=Pose(np.array([[0,0,0,1],[0,0,.6,.8]]),np.array([[3,4,5],[6,7,8]]),np.array([1.,2.]))
    ev.sample=lambda resource,phase:primary.copy()
    row=dict(demo_tick=1,context=[0,0],tasks=[
        dict(type_id=1,task_class='CNmSampleTask',clip_resource='fixture',phase_u16=0),
        dict(type_id=12,task_class='CNmZeroPoseTask',dependencies=[]),
        dict(type_id=7,task_class='CNmAdditiveBlendTask',dependencies=[0,1],weight_u8=weight,masks=[])])
    observed=[]
    result=ev.evaluate(row,observer=lambda evaluator,row,index,task,outputs,out:observed.append(out.copy()))
    zero=observed[1]
    assert zero.additive and np.array_equal(zero.q,np.tile([0,0,0,1],(2,1)))
    assert np.array_equal(zero.t,np.zeros((2,3))) and np.array_equal(zero.s,np.zeros(2))
    assert np.allclose(result.q,primary.q) and np.array_equal(result.t,primary.t) and np.array_equal(result.s,primary.s)


def test_zero_pose_cannot_be_published_as_final_nonadditive_pose(tmp_path):
    ev=evaluator(tmp_path)
    with pytest.raises(ValueError,match='Final pose is an additive layer'):
        ev.evaluate(dict(demo_tick=1,tasks=[dict(type_id=12,task_class='CNmZeroPoseTask',dependencies=[])]))
