"""Incomplete cached poses must be withheld before mutating a Blender rig."""
import json

import numpy as np
import pytest

from agr import blender_scene


@pytest.mark.parametrize('ticks,missing', [([1, 2], 3), ([10, 12], 1)])
def test_cached_pose_gaps_return_unsupported_before_accessing_rig(tmp_path, monkeypatch, ticks, missing):
    skeleton = tmp_path / 'skeleton.json'
    skeleton.write_text(json.dumps(dict(names=['root'], parents=[-1])))
    poses = tmp_path / 'poses.npz'
    np.savez(poses, q=np.tile([[[0., 0., 0., 1.]]], (len(ticks), 1, 1)),
             t=np.zeros((len(ticks), 1, 3)), s=np.ones((len(ticks), 1)), ticks=np.array(ticks))
    monkeypatch.setattr(blender_scene, 'np', np, raising=False)
    # None deliberately cannot be animated: this check must happen before rig access.
    result = blender_scene._bake_pose(None, dict(skeleton_file=str(skeleton), pose_file=str(poses)),
        dict(start_tick=10, end_tick=12), {}, 64, requested_ticks=[10, 11, 12])
    assert result['status'] == 'unsupported' and result['sample_count'] == 0
    assert result['missing_sample_count'] == missing
    assert result['reason_code'] == 'missing_selected_pose_samples'


def test_pose_coverage_requires_observed_output_ticks_without_inventing_pvs_samples():
    assert blender_scene.pose_sample_coverage([1, 10, 12, 14], [10, 12], 10, 12) is None
    assert blender_scene.pose_sample_coverage([1, 10, 12], [10, 11, 12], 10, 12)['first_missing_tick'] == 11


@pytest.mark.parametrize('mode',['valid','ambiguous_model','ambiguous_source','parent_mismatch','unmatched_parent'])
def test_source_model_casing_requires_one_name_and_matching_parent(mode):
    from types import SimpleNamespace
    head=SimpleNamespace(name='head',parent=None)
    bones={'head':head,'ear_l':SimpleNamespace(name='ear_l',parent=head)}
    names=['head','ear_L'];parents=[-1,0]
    if mode=='ambiguous_model':bones['EAR_L']=SimpleNamespace(name='EAR_L',parent=head)
    elif mode=='ambiguous_source':names.append('EAR_L');parents.append(0)
    elif mode=='parent_mismatch':bones['ear_l'].parent=None
    elif mode=='unmatched_parent':names[0]='missing'
    if mode=='valid':
        mapping,aliases=blender_scene.source_rig_mapping(names,parents,bones)
        assert mapping==dict(head='head',ear_L='ear_l')
        assert aliases==[dict(index=1,source='ear_L',model='ear_l')]
    else:
        with pytest.raises(ValueError):blender_scene.source_rig_mapping(names,parents,bones)
