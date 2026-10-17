import numpy as np
import pytest

from agr.vendor.evaluate_recipes import Evaluator
from agr.vendor.pose_math import Pose, solve_ik


def evaluator(tmp_path):
    sk={'m_boneIDs':['root'], 'm_parentIndices':[-1],
        'm_parentSpaceReferencePose':[[0,0,0,1,0,0,0,1]], 'm_maskDefinitions':[]}
    e=Evaluator('fixture',sk,tmp_path)
    e.sample=lambda resource,phase: Pose(np.array([[0.,0.,0.,1.]]),np.array([[phase,0.,0.]]),np.ones(1))
    return e


def task(kind, **kwargs):
    return dict(type_id=kind,task_class=str(kind),**kwargs)


def test_cache_read_copies_recorded_write_and_context_change_invalidates(tmp_path):
    e=evaluator(tmp_path)
    written=e.evaluate({'demo_tick':1,'context':[1,1],'tasks':[
        task(1,clip_resource='x',phase_u16=65535),task(0,dependencies=[0],cache_id=7)]})
    written.t[:]=9
    read={'demo_tick':2,'context':[1,1],'tasks':[task(5,cache_id=7)]}
    result=e.evaluate(read)
    np.testing.assert_array_equal(result.t,[[1,0,0]])
    result.t[:]=8
    np.testing.assert_array_equal(e.evaluate(read).t,[[1,0,0]])
    with pytest.raises(ValueError,match='no recorded write'):
        e.evaluate(dict(read,context=[1,2]))


def test_missing_cached_pose_never_invents_reference_animation(tmp_path):
    with pytest.raises(ValueError,match='no recorded write'):
        evaluator(tmp_path).evaluate({'demo_tick':1,'tasks':[task(5,cache_id=24)]})


@pytest.mark.parametrize('lengths',[(0.,2.),(2.,0.),(.0005,2.),(.001,2.)])
def test_native_degenerate_ik_guard_preserves_every_transform(lengths):
    p=Pose(np.tile([0.,0.,0.,1.],(4,1)),
           np.array([[0.,0.,0.],[lengths[0],0.,0.],[lengths[1],0.,0.],[2.,1.,0.]]),np.ones(4))
    result=solve_ik(p,np.array([-1,0,1,0]),p,2,3)
    for field in ('q','t','s'):
        np.testing.assert_array_equal(getattr(result,field),getattr(p,field))
        assert not np.shares_memory(getattr(result,field),getattr(p,field))
