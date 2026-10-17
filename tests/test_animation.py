"""Check identity gates and real recipe/pose behavior with tiny standalone assets."""
import json
import hashlib
from copy import deepcopy
from pathlib import Path
import struct

import numpy as np
import pytest

from agr.animation import AnimationError, _bind, _bone_name_aliases, evaluate_segment
from agr.vendor.resource_hash import resource_id


REGISTRY = ['CNmCachedPoseWriteTask', 'CNmSampleTask', 'CNmScaleTask',
            'CNmReferencePoseTask', 'CNmTwoBoneIKTask', 'CNmCachedPoseReadTask',
            'CNmBlendTask', 'CNmAdditiveBlendTask', 'CNmOverlayBlendTask',
            'CNmModelSpaceBlendTask', 'CNmChainLookatTask', 'CNmFollowBoneTask',
            'CNmZeroPoseTask', 'CNmChainSolverTask']


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def provenance(inputs):
    write(inputs/'animation_provenance.json', {
        'demo_sha256':'a'*64,
        'raw_sha256':hashlib.sha256((inputs/'raw_animation_state.json').read_bytes()).hexdigest(),
        'network_ticks_sha256':hashlib.sha256((inputs/'network_ticks.json').read_bytes()).hexdigest(),
    })


def strings(items):
    return struct.pack('<I', len(items)) + b''.join(x.encode()+b'\0' for x in items)


def recipe(server_tick, type_id=1, phase=0):
    # version 2 payload: u32 tick, u4 dependency-width, count, task IDs,
    # sample resource slot and phase; little endian bit packing throughout.
    bits = [(server_tick,32), (1,4), (1,1), (type_id,4), (0,1), (phase,16)]
    value = 0; position = 0
    for item, width in bits:
        value |= item << position; position += width
    return list(value.to_bytes((position+7)//8, 'little'))


def test_identical_bone_names_need_no_aliases():
    assert _bone_name_aliases(['root', 'ear_0_L'], ['root', 'ear_0_L']) == []


def test_silver_case_aliases_preserve_recorded_bone_indices():
    recorded = [f'bone_{index}' for index in range(144)]
    recorded[19] = 'ear_0_L'; recorded[21] = 'ear_0_R'
    installed = recorded.copy()
    installed[19] = 'ear_0_l'; installed[21] = 'ear_0_r'
    original = deepcopy((recorded, installed))
    assert _bone_name_aliases(recorded, installed) == [
        {'index':19, 'recorded':'ear_0_L', 'asset':'ear_0_l'},
        {'index':21, 'recorded':'ear_0_R', 'asset':'ear_0_r'},
    ]
    assert (recorded, installed) == original


@pytest.mark.parametrize(('recorded', 'installed'), [
    (['root', 'arm'], ['arm', 'root']),
    (['root', 'arm'], ['root', 'hand']),
    (['root', 'arm'], ['root']),
    (['ear_L', 'ear_l'], ['ear_L', 'ear_l']),
    (['ear_L', 'ear_l'], ['ear_l', 'ear_L']),
    (['root', 'arm'], ['root', 'ROOT']),
    (['root', 'ROOT'], ['root', 'arm']),
    (['root', 'root'], ['root', 'root']),
    (['root', '\u00c4rm'], ['root', '\u00e4rm']),
    (['root', ''], ['root', '']),
    (['root', None], ['root', None]),
    (['root', 1], ['root', 1]),
])
def test_bone_aliases_reject_ambiguous_or_changed_identity(recorded, installed):
    with pytest.raises(AnimationError, match='bone or mask order'):
        _bone_name_aliases(recorded, installed)


def silver_binding_inputs():
    """Minimal recipe with Silver's observed 144-bone casing differences."""
    bones = [f'bone_{index}' for index in range(144)]
    bones[19] = 'ear_0_L'; bones[21] = 'ear_0_R'
    installed = bones.copy()
    installed[19] = 'ear_0_l'; installed[21] = 'ear_0_r'
    graph = 'graphs/silver.vnmgraph'; skeleton_path = 'models/silver.vnmskel'
    masks = ['upper_body', 'lower_body']
    recorded = {
        graph: {'resources':['clips/silver.vnmclip']},
        skeleton_path: {'bones':bones, 'masks':masks},
    }
    skeleton = {'m_boneIDs':installed,
                'm_maskDefinitions':[{'m_ID':name} for name in masks]}
    entry = {'graph':graph, 'skeleton':skeleton_path}
    payload = recipe(100)
    raw = {'demo_tick':10, 'hero_id':80, 'serialized_pose_recipe_array':payload,
           'fields':{'m_primaryGraphId':resource_id(graph),
                     'm_nSerializePoseRecipeSizeAG2':len(payload),
                     'm_nSerializePoseRecipeVersionAG2':2,
                     'm_nServerGraphInstanceIteration':1,
                     'm_nServerSerializationContextIteration':2}}
    return raw, recorded, entry, skeleton


def test_bind_records_case_aliases_without_changing_source_skeleton():
    raw, recorded, entry, skeleton = silver_binding_inputs()
    before = deepcopy((raw, recorded, entry, skeleton))
    row = _bind(raw, 100, recorded, REGISTRY, entry, skeleton)
    assert row['bone_name_aliases'] == [
        {'index':19, 'recorded':'ear_0_L', 'asset':'ear_0_l'},
        {'index':21, 'recorded':'ear_0_R', 'asset':'ear_0_r'},
    ]
    assert row['tasks'][0]['clip_resource'] == 'clips/silver.vnmclip'
    assert skeleton['m_boneIDs'][19] == 'ear_0_l'
    assert (raw, recorded, entry, skeleton) == before


def test_bind_still_rejects_mask_reordering_with_valid_bone_aliases():
    raw, recorded, entry, skeleton = silver_binding_inputs()
    skeleton['m_maskDefinitions'].reverse()
    with pytest.raises(AnimationError, match='bone or mask order'):
        _bind(raw, 100, recorded, REGISTRY, entry, skeleton)


@pytest.fixture
def workspace(tmp_path):
    assets = tmp_path/'assets'; inputs = tmp_path/'inputs'
    graph = 'graphs/test.vnmgraph'; skel = 'models/test.vnmskel'; clip = 'clips/test.vnmclip'
    skeleton = {'m_boneIDs':['root','child','tip'], 'm_parentIndices':[-1,0,1],
                'm_parentSpaceReferencePose':[[0,0,0,1,0,0,0,1]]*3,
                'm_maskDefinitions':[]}
    entry = {'hero_id':99, 'graph':graph, 'skeleton':skel,
             'bones':skeleton['m_boneIDs'], 'masks':[]}
    write(assets/'animation_catalog.json', {'Tester':entry})
    write(assets/'clip_cache'/f'{skel}.json', skeleton)
    write(assets/'clip_cache'/f'{clip}.json', {
        'm_skeleton':skel, 'm_modelSpaceBoneSamplingIndices':[], 'm_bIsAdditive':False})
    q = np.tile([0.,0.,0.,1.], (2,3,1)); t = np.zeros((2,3,3)); t[1,:,0] = 10.
    np.savez_compressed(assets/'clip_cache'/f'{clip}.npz', q=q,t=t,s=np.ones((2,3)))
    digest = 'a'*64
    segment = {'schema_version':1,'metadata':{'demo_sha256':digest},
               'tick_rate':64,'start_tick':11,'end_tick':12,
               'entities':[{'id':'7','hero_id':99,'hero_name':'Tester',
                            'samples':[{'tick':tick,'position':[1,2,3],
                                        'body_angles':[0,90,0],'health':100,'alive':True}
                                       for tick in [11,12]]}]}
    write(inputs/'segment.json', segment)
    raw = []
    for demo_tick, server_tick, recipe_tick, phase in [(10,100,100,65535),(11,101,100,65535),(12,102,102,0)]:
        payload = recipe(recipe_tick, phase=phase)
        raw.append({'demo_tick':demo_tick,'entity_index':7,'hero_id':99,
                    'serialized_pose_recipe_array':payload,
                    'fields':{'m_primaryGraphId':resource_id(graph),
                              'm_nSerializePoseRecipeSizeAG2':len(payload),
                              'm_nSerializePoseRecipeVersionAG2':2,
                              'm_nServerGraphInstanceIteration':1,
                              'm_nServerSerializationContextIteration':2}})
    write(inputs/'raw_animation_state.json', raw)
    write(inputs/'network_ticks.json', [{'demo_tick':x, 'server_tick':x+90} for x in [10,11,12]])
    asset_rows = [
        {'index':0,'name':graph,'data_hex':(struct.pack('<I',1)+strings([clip])).hex()},
        {'index':1,'name':skel,'data_hex':(struct.pack('<I',1)+strings(skeleton['m_boneIDs'])+strings([])).hex()},
    ]
    table = {'demo_sha256':digest,'end_tick':12,
             'snapshot_checks':[{'matches_sequential_stream':True}],
             'initial_tables':{'AnimAssetData':asset_rows,
                               'AnimTaskTypes':[{'index':i,'name':name,'data_hex':''}
                                                for i,name in enumerate(REGISTRY)]},
             'animation_table_updates':[], 'table_clears':[-1]}
    write(inputs/'animation_tables.json', table)
    provenance(inputs)
    return tmp_path, assets, inputs


def test_fresh_decode_carries_prior_pose_into_stale_range_start(workspace):
    root, assets, inputs = workspace
    path = evaluate_segment(inputs/'segment.json', root/'output', assets)
    result = json.loads(path.read_text()); entity = result['entities'][0]
    assert entity['samples'][0]['position'] == [1,2,3]
    assert result['animation']['entities'][0]['held'] == 1
    with np.load(entity['pose_file']) as poses:
        assert poses['ticks'].tolist() == [11,12]
        assert poses['fresh'].tolist() == [False,True]
        np.testing.assert_array_equal(poses['t'][:,0,0], [10,0])


def test_registry_reordered_dispatches_by_recorded_name(workspace):
    root, assets, inputs = workspace
    table = json.loads((inputs/'animation_tables.json').read_text())
    registry = table['initial_tables']['AnimTaskTypes']
    registry[1]['name'], registry[2]['name'] = registry[2]['name'], registry[1]['name']
    write(inputs/'animation_tables.json', table)
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    for row in raw:
        row['serialized_pose_recipe_array'] = recipe(
            102 if row['demo_tick']==12 else 100, type_id=2,
            phase=0 if row['demo_tick']==12 else 65535)
    write(inputs/'raw_animation_state.json', raw)
    provenance(inputs)
    assert evaluate_segment(inputs/'segment.json', root/'output', assets).is_file()


def test_missing_prehistory_does_not_invent_first_pose(workspace):
    root, assets, inputs = workspace
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    write(inputs/'raw_animation_state.json', raw[1:])
    provenance(inputs)
    with pytest.raises(AnimationError, match='stale'):
        evaluate_segment(inputs/'segment.json', root/'output', assets)


def test_stale_pose_cannot_cross_serialization_context(workspace):
    root, assets, inputs = workspace
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    raw[1]['fields']['m_nServerSerializationContextIteration'] = 3
    write(inputs/'raw_animation_state.json', raw)
    provenance(inputs)
    with pytest.raises(AnimationError, match='stale'):
        evaluate_segment(inputs/'segment.json', root/'output', assets)


def test_unneeded_old_recipes_do_not_block_selected_range(workspace):
    root, assets, inputs = workspace
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    old = json.loads(json.dumps(raw[0])); old['demo_tick'] = 9
    old['serialized_pose_recipe_array'] = recipe(99, type_id=2)
    write(inputs/'raw_animation_state.json', [old]+raw)
    ticks = json.loads((inputs/'network_ticks.json').read_text())
    write(inputs/'network_ticks.json', [{'demo_tick':9,'server_tick':99}]+ticks)
    provenance(inputs)
    assert evaluate_segment(inputs/'segment.json', root/'output', assets).is_file()


def test_unknown_task_is_reported_and_not_sampled(workspace):
    root, assets, inputs = workspace
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['initial_tables']['AnimTaskTypes'][1]['name'] = 'CNmFutureUnknownTask'
    write(inputs/'animation_tables.json', table)
    with pytest.raises(AnimationError, match='CNmFutureUnknownTask'):
        evaluate_segment(inputs/'segment.json', root/'strict', assets)
    result = json.loads(evaluate_segment(inputs/'segment.json', root/'partial', assets,
                                        strict=False).read_text())
    assert result['entities'][0]['animation_status'] == 'unsupported'
    assert 'pose_file' not in result['entities'][0]
    assert result['animation']['diagnostics'][0]['code'] == 'unsupported_animation'


def test_tables_from_another_replay_are_rejected(workspace):
    root, assets, inputs = workspace
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['demo_sha256'] = 'b'*64
    write(inputs/'animation_tables.json', table)
    with pytest.raises(AnimationError, match='different replay'):
        evaluate_segment(inputs/'segment.json', root/'output', assets)


def test_altered_raw_sidecar_is_rejected(workspace):
    root, assets, inputs = workspace
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    write(inputs/'raw_animation_state.json', raw[1:])
    with pytest.raises(AnimationError, match='checksum mismatch'):
        evaluate_segment(inputs/'segment.json', root/'output', assets)


def test_verified_cache_is_explicit_and_identity_bound(workspace):
    root, assets, inputs = workspace
    segment = json.loads((inputs/'segment.json').read_text())
    segment['metadata']['animation_source'] = 'verified-cache'
    write(inputs/'segment.json', segment)
    write(assets/'mapping/mapping_validation.json', {
        'mapping_validation':'PASS','demo_sha256':'b'*64,'start_tick':10,'end_tick':12})
    with pytest.raises(AnimationError, match='SHA256'):
        evaluate_segment(inputs/'segment.json', root/'output', assets)


def test_verified_cache_cannot_be_used_for_a_new_range(workspace):
    root, assets, inputs = workspace
    segment = json.loads((inputs/'segment.json').read_text())
    segment['metadata']['animation_source'] = 'verified-cache'
    write(inputs/'segment.json', segment)
    write(assets/'mapping/mapping_validation.json', {
        'mapping_validation':'PASS','demo_sha256':'a'*64,'start_tick':10,'end_tick':11})
    with pytest.raises(AnimationError, match='outside'):
        evaluate_segment(inputs/'segment.json', root/'output', assets)


def test_earlier_table_snapshot_used_before_later_update(workspace):
    root, assets, inputs = workspace
    table = json.loads((inputs/'animation_tables.json').read_text())
    changed = dict(table['initial_tables']['AnimAssetData'][0])
    changed['data_hex'] = (struct.pack('<I',1)+strings(['clips/missing.vnmclip'])).hex()
    table['animation_table_updates'] = [{'tick':12,'table':'AnimAssetData',**changed}]
    write(inputs/'animation_tables.json', table)
    segment = json.loads((inputs/'segment.json').read_text())
    segment['end_tick'] = 11; segment['entities'][0]['samples'] = segment['entities'][0]['samples'][:1]
    write(inputs/'segment.json', segment)
    path = evaluate_segment(inputs/'segment.json', root/'output', assets)
    with np.load(json.loads(path.read_text())['entities'][0]['pose_file']) as poses:
        assert poses['t'][0,0,0] == 10.
