import numpy as np
from agr.vendor.decode_pose_recipes import parse
from agr.vendor.evaluate_recipes import Evaluator


def recipe():
    return dict(payload_hex='00000000',candidate_task_data_start_bit=32,
                candidate_dependency_bits=1,candidate_task_type_ids_4bit=[3],
                topology_tasks=[dict(type_id=3,dependencies=[])],hero_id=88,
                demo_tick=1,recipe_age_ticks=0)


def test_reference_pose_has_no_dynamic_values_or_dependencies():
    result=parse(recipe(),bone_bits=1)
    assert result['complete_layout'] and result['consumed_bits']==32
    assert result['tasks'][0]['dependencies']==[] and result['error'] is None and result['unsupported'] is None


def test_reference_pose_with_forged_dependency_is_rejected():
    row=recipe();row['topology_tasks'][0]['dependencies']=[0]
    result=parse(row,bone_bits=1)
    assert not result['complete_layout'] and 'cannot have dependencies' in result['error']


def test_reference_pose_is_the_skeleton_reference_pose(tmp_path):
    skeleton=dict(m_boneIDs=['root','child'],m_parentIndices=[-1,0],
                  m_parentSpaceReferencePose=[[4,5,6,1,0,0,0,1],[1,2,3,2,0,0,.6,.8]],m_maskDefinitions=[])
    ev=Evaluator('fixture',skeleton,tmp_path)
    out=ev.evaluate(dict(demo_tick=1,tasks=[dict(type_id=3,task_class='CNmReferencePoseTask',dependencies=[])]))
    assert not out.additive and np.allclose(out.q,[[0,0,0,1],[0,0,.6,.8]])
    assert np.array_equal(out.t,[[4,5,6],[1,2,3]]) and np.array_equal(out.s,[1,2])
