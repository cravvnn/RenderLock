"""A model switch preserves one pawn, separate rigs and explicit missing poses."""
import copy
import json
import struct

import numpy as np
import pytest

from agr.animation import AnimationError, evaluate_segment
from agr.blender_scene import visibility_changes
from agr.cli import animation_mode, read_blender_completion
from agr.installed_animation import prepare_animation_assets
from agr.model_tracks import actor_id, expand_model_tracks, logical_entities
from agr.scene_package import SceneError, slice_scene, validate_scene
from agr.vendor.resource_hash import resource_id
from test_animation import workspace, write, strings, provenance, recipe
from test_installed_animation import Provider, Converter
from test_blender_completion_stages import completion


def switching_workspace(workspace):
    root, assets, inputs = workspace
    graph, skel, clip = 'graphs/human.vnmgraph', 'models/human.vnmskel', 'clips/human.vnmclip'
    skeleton = dict(m_boneIDs=['root','human'],m_parentIndices=[-1,0],
                    m_parentSpaceReferencePose=[[0,0,0,1,0,0,0,1]]*2,m_maskDefinitions=[])
    write(assets/'clip_cache'/f'{skel}.json',skeleton)
    write(assets/'clip_cache'/f'{clip}.json',dict(m_skeleton=skel,m_bIsAdditive=False,m_modelSpaceBoneSamplingIndices=[]))
    np.savez_compressed(assets/'clip_cache'/f'{clip}.npz',q=np.tile([0.,0.,0.,1.],(2,2,1)),
                        t=np.full((2,2,3),23.),s=np.ones((2,2)))
    data=json.loads((inputs/'segment.json').read_text());data['end_tick']=13
    e=data['entities'][0];e.update(id='7:2',entity_index=7,entity_serial=2,model_handle='11')
    e['samples'].append(dict(e['samples'][-1],tick=13))
    for s,h in zip(e['samples'],['11','22','22']):s['model_handle']=h
    expand_model_tracks(data);write(inputs/'segment.json',data)
    raw=json.loads((inputs/'raw_animation_state.json').read_text())
    for r in raw:r['fields']['m_hModel']=11
    r=raw[-1];r['fields'].update(m_hModel=22,m_primaryGraphId=resource_id(graph),
        m_nSerializePoseRecipeVersionAG2=0,m_nSerializePoseRecipeSizeAG2=0,
        m_nServerGraphInstanceIteration=2,m_nServerSerializationContextIteration=0)
    r['serialized_pose_recipe_array']=[]
    r=copy.deepcopy(r);r['demo_tick']=13;r['serialized_pose_recipe_array']=recipe(103)
    r['fields'].update(m_nSerializePoseRecipeVersionAG2=2,m_nSerializePoseRecipeSizeAG2=len(r['serialized_pose_recipe_array']),
                       m_nServerSerializationContextIteration=1)
    raw.append(r);write(inputs/'raw_animation_state.json',raw);provenance(inputs)
    table=json.loads((inputs/'animation_tables.json').read_text());table['end_tick']=13
    table['initial_tables']['AnimAssetData'] += [
        dict(index=2,name=graph,data_hex=(struct.pack('<I',1)+strings([clip])).hex()),
        dict(index=3,name=skel,data_hex=(struct.pack('<I',1)+strings(skeleton['m_boneIDs'])+strings([])).hex())]
    write(inputs/'animation_tables.json',table)
    ticks=json.loads((inputs/'network_ticks.json').read_text());ticks.append(dict(demo_tick=13,server_tick=103))
    write(inputs/'network_ticks.json',ticks);provenance(inputs)
    converter=Converter(assets);converter.overrides[graph]=dict(m_skeleton=skel)
    return root,assets,inputs,data,converter


def test_separate_rigs_share_one_full_movement_and_player_identity(workspace):
    _,_,_,data,_=switching_workspace(workspace)
    a,b=data['entities'];logical=logical_entities(data['entities'])
    assert a['id']=='7:2' and b['id']=='7:2@model:12'
    assert actor_id(a)==actor_id(b)=='7:2'
    assert [s['tick'] for s in logical['7:2']['samples']]==[11,12,13]
    assert logical['7:2']['observed_intervals']==[dict(start_tick=11,end_tick_exclusive=14)]
    assert 'model_handle' not in logical['7:2'] and 'pose_file' not in logical['7:2']
    assert [e['model_handle'] for e in data['entities']]==['11','22']
    original=copy.deepcopy(data);expand_model_tracks(data);assert data==original


@pytest.mark.parametrize('stage',['prepare'])
def test_strict_model_switch_reports_the_unrecorded_reset_pose(workspace,stage):
    root,assets,inputs,data,converter=switching_workspace(workspace)
    pack=root/'prepared'
    if stage=='evaluate':prepare_animation_assets(inputs/'segment.json',pack,Provider(),converter,strict=False)
    with pytest.raises(AnimationError) as caught:
        if stage=='prepare':prepare_animation_assets(inputs/'segment.json',pack,Provider(),converter)
        else:evaluate_segment(inputs/'segment.json',root/'output',pack)
    assert caught.value.details['code']=='animation_reset_pose_unavailable'
    assert caught.value.details['demo_tick']==12


def partial_result(workspace):
    root,assets,inputs,data,converter=switching_workspace(workspace)
    report=prepare_animation_assets(inputs/'segment.json',root/'prepared',Provider(),converter,strict=False)
    assert report['status']=='partial' and len(report['catalog'])==2
    path=evaluate_segment(inputs/'segment.json',root/'output',root/'prepared',strict=False)
    return json.loads(path.read_text())


def test_partial_evaluates_each_real_rig_and_omits_only_the_unavailable_pose(workspace):
    data=partial_result(workspace);a,b=data['entities'];validate_scene(data)
    with np.load(a['pose_file']) as p:
        assert p['q'].shape==(1,3,4) and p['ticks'].tolist()==[11]
        assert p['t'][0,0,0]==10  # Observed stale recipe holds its own earlier seed.
    with np.load(b['pose_file']) as p:
        assert p['q'].shape==(1,2,4) and p['ticks'].tolist()==[13]
        assert p['t'][0,0,0]==23
    assert b['pose_unavailable_ticks']==[12] and [s['pose_available'] for s in b['samples']]==[False,True]
    assert data['coverage']['skeletal_animation']['all_requested_entities'] is False
    assert visibility_changes(a,a['samples'],data,64)==[(1,False),(2,True)]
    assert visibility_changes(b,b['samples'],data,64)==[(1,True),(3,False)]
    assert animation_mode(data,partial=True)=='cached'
    with pytest.raises(ValueError,match='unavailable reset'):animation_mode(data)
    assert animation_mode(data,motion_only=True)=='motion'
    assert visibility_changes(b,b['samples'],data,64)==[(1,True),(2,False)]


def test_saved_slice_after_reset_uses_only_human_pose_and_one_canonical_follow_target(workspace):
    data=partial_result(workspace);after=slice_scene(data,13,13)
    assert len(after['entities'])==1 and after['entities'][0]['id']=='7:2@model:12'
    assert 'pose_unavailable_ticks' not in after['entities'][0]
    assert animation_mode(after,saved_package=True)=='cached'
    logical=logical_entities(after['entities'])
    assert list(logical)==['7:2'] and logical['7:2']['pose_file']==after['entities'][0]['pose_file']
    assert after['coverage']['skeletal_animation']['unsupported_entities']==0
    assert after['animation']['diagnostics']==[]
    assert after['source_animation']['diagnostics']==data['animation']['diagnostics']
    assert slice_scene(data,11,13)['coverage']['skeletal_animation']['unsupported_entities']==1
    bad=copy.deepcopy(data);bad['entities'][1]['samples'][0]['pose_available']=True
    with pytest.raises(SceneError,match='unavailable pose ticks'):validate_scene(bad)


def test_reset_only_form_never_becomes_an_imported_rest_pose(workspace):
    root,assets,inputs,data,converter=switching_workspace(workspace)
    clipped=slice_scene(data,11,12);write(inputs/'segment.json',clipped)
    prepare_animation_assets(inputs/'segment.json',root/'prepared',Provider(),converter,strict=False)
    with pytest.raises(AnimationError) as caught:
        evaluate_segment(inputs/'segment.json',root/'output',root/'prepared',strict=False)
    assert caught.value.details==dict(code='animation_reset_only_range',ticks=[12])
    saved=copy.deepcopy(data)
    saved['entities'][1]['pose_unavailable_ticks']=[12]
    for s in saved['entities'][1]['samples']:s['pose_available']=s['tick']!=12
    with pytest.raises(ValueError,match='only an unavailable reset pose'):
        animation_mode(slice_scene(saved,12,12),partial=True)


@pytest.mark.parametrize('mutation',['wrong_track'])
def test_completion_requires_exact_partial_gap_and_model_identity(workspace,tmp_path,mutation):
    data=partial_result(workspace);path,job=completion(tmp_path)
    job['data']=data;job['fps']=64
    document=json.loads(path.read_text());document.update(start_tick=11,end_tick=13,tick_rate=64,fps=64)
    document['entities']=[]
    for e in data['entities']:
        missing=e.get('pose_unavailable_ticks',[])
        document['entities'].append(dict(id=e['id'],model_status='imported',model_track=e['model_track'],
            pose=dict(status='baked_partial' if missing else 'baked',sample_count=1,unavailable_pose_ticks=missing),
            visibility=dict(keys=[dict(frame=f,hidden=h) for f,h in visibility_changes(e,e['samples'],data,64)])))
    row=document['entities'][1]
    if mutation=='full_success':row['pose']['status']='baked'
    elif mutation=='visible_gap':row['visibility']['keys'][0]['hidden']=False
    elif mutation=='wrong_track':row['model_track']=dict(row['model_track'],model_handle='11')
    elif mutation=='missing_ticks':row['pose']['unavailable_pose_ticks']=[]
    path.write_text(json.dumps(document))
    if mutation is None:
        result=read_blender_completion(path,**job,partial=True)
        assert result['coverage']['skeletal_animation']['all_requested_entities'] is False
    else:
        with pytest.raises(RuntimeError):read_blender_completion(path,**job,partial=True)
