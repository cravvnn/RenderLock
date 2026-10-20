"""A transformation must never silently reuse the first form's model or rig."""
import copy
import json
import struct

import pytest

from agr.animation import AnimationError, evaluate_segment
from agr.installed_animation import prepare_animation_assets
from agr.installed_assets import bind_models
from agr.model_states import model_states
from agr.replay import ReplayError, _entities_from_raw
from agr.scene_package import SceneError, slice_scene, validate_scene
from agr.vendor.resource_hash import resource_id
from test_animation import workspace, write, provenance, strings
from test_installed_animation import Converter, Provider
from test_replay import PREFIX, row


def model_rows():
    values = [row(tick) for tick in (10, 11, 12, 13)]
    for sample, handle in zip(values, (2**64 - 1, 2**64 - 1, 23, 23)):
        sample['fields'][PREFIX+'m_modelState.m_hModel'] = handle
    return values


def test_extraction_keeps_both_forms_without_splitting_network_identity():
    entities, _ = _entities_from_raw(model_rows(), 10, 13, None)
    assert len(entities) == 1 and entities[0]['id'] == '7:2'
    assert entities[0]['model_handle'] == str(2**64-1)
    assert [sample['model_handle'] for sample in entities[0]['samples']] == [str(2**64-1)]*2+['23']*2
    assert model_states(entities[0]) == [
        {'start_tick':10,'end_tick_exclusive':12,'model_handle':str(2**64-1)},
        {'start_tick':12,'end_tick_exclusive':14,'model_handle':'23'}]


@pytest.mark.parametrize('bad', [None, True, -1, 2**64, 1.5, '23'])
def test_native_model_handle_is_not_coerced_or_truncated(bad):
    values = model_rows()
    values[1]['fields'][PREFIX+'m_modelState.m_hModel'] = bad
    with pytest.raises(ReplayError, match='Invalid recorded model resource ID'):
        _entities_from_raw(values, 10, 13, None)


def test_observation_gaps_do_not_imply_a_continuous_model_state():
    assert model_states({'samples':[{'tick':10,'model_handle':'23'},
                                     {'tick':12,'model_handle':'23'}]}) == [
        {'start_tick':10,'end_tick_exclusive':11,'model_handle':'23'},
        {'start_tick':12,'end_tick_exclusive':13,'model_handle':'23'}]


def test_static_importer_refuses_both_forms_even_when_both_resources_exist():
    a, b = 'models/a.vmdl', 'models/b.vmdl'
    handles = [str(resource_id(a)), str(resource_id(b))]
    entity = {'id':'7:2','hero_id':80,'model_handle':handles[0],
              'samples':[{'tick':10+i,'model_handle':handle} for i,handle in enumerate(handles)]}
    bindings, failures = bind_models({'entities':[entity]}, [a+'_c',b+'_c'])
    assert bindings == [] and len(failures) == 1
    assert failures[0]['code'] == 'recorded_model_change'
    assert failures[0]['demo_tick'] == 11
    assert failures[0]['previous_model_handle'] == handles[0]
    assert failures[0]['model_handle'] == handles[1]


def test_same_form_and_legacy_absence_still_bind_exact_resource():
    name = 'models/a.vmdl'; handle = str(resource_id(name))
    entity = {'id':'7:2','hero_id':80,'model_handle':handle,
              'samples':[{'tick':10,'model_handle':handle},{'tick':12,'model_handle':handle}]}
    assert len(bind_models({'entities':[entity]}, [name+'_c'])[0]) == 1
    for sample in entity['samples']:
        sample.pop('model_handle')
    assert len(bind_models({'entities':[entity]}, [name+'_c'])[0]) == 1


@pytest.mark.parametrize('bad', [None, True, 23, '-1', '01', '1.0', str(2**64), '\u0661'])
def test_portable_model_timeline_rejects_invalid_or_missing_ids(bad):
    entities, _ = _entities_from_raw(model_rows(), 10, 13, None)
    entities[0]['samples'][1]['model_handle'] = bad
    data={'schema_version':1,'tick_rate':64,'start_tick':10,'end_tick':13,'entities':entities}
    with pytest.raises(SceneError, match='recorded model resource ID'):
        validate_scene(data)


def test_slice_in_another_form_rebinds_and_drops_the_old_rig_and_derived_assets():
    entities, _ = _entities_from_raw(model_rows(), 10, 13, None)
    entity=entities[0]
    derived=('model_file','pose_file','skeleton_file','secondary_motion_file','rig_object',
             'model_constraints','model_material_groups','model_mesh_groups')
    for key in derived:
        entity[key]='old-form'
    data={'schema_version':1,'tick_rate':64,'start_tick':10,'end_tick':13,'entities':entities}
    original = copy.deepcopy(data)
    sliced = slice_scene(data, 12, 13)
    assert sliced['entities'][0]['model_handle'] == '23'
    assert all(key not in sliced['entities'][0] for key in derived)
    assert data == original
    before = slice_scene(data, 10, 11)
    assert before['entities'][0]['pose_file'] == 'old-form'


def changing_graph(workspace):
    root, source, inputs = workspace
    graph = 'graphs/transformed.vnmgraph'
    table = json.loads((inputs/'animation_tables.json').read_text())
    table['initial_tables']['AnimAssetData'].append(
        {'index':2,'name':graph,'data_hex':(struct.pack('<I',1)+strings(['clips/test.vnmclip'])).hex()})
    write(inputs/'animation_tables.json',table)
    raw = json.loads((inputs/'raw_animation_state.json').read_text())
    raw[-1]['fields']['m_primaryGraphId']=resource_id(graph)
    raw[-1]['serialized_pose_recipe_array']=[]
    raw[-1]['fields']['m_nSerializePoseRecipeVersionAG2']=0
    raw[-2]['fields']['m_hModel']=11
    raw[-1]['fields']['m_hModel']=22
    write(inputs/'raw_animation_state.json',raw);provenance(inputs)
    return root, source, inputs, graph, raw


@pytest.mark.parametrize('stage', ['prepare','evaluate'])
def test_both_animation_paths_preserve_transition_details_and_missing_pose(workspace,stage):
    root, source, inputs, graph, raw = changing_graph(workspace)
    if stage=='prepare':
        converter=Converter(source)
        invoke=lambda:prepare_animation_assets(inputs/'segment.json',root/'prepared',Provider(),converter)
    else:
        # The transition must remain explicit even when the aborted preparation
        # never added the second graph to the catalog.
        invoke=lambda:evaluate_segment(inputs/'segment.json',root/'evaluated',source)
    with pytest.raises(AnimationError,match='replay tick 12') as caught:
        invoke()
    details=caught.value.details
    assert details['code']=='animation_graph_transition'
    assert details['previous_graph']=='graphs/test.vnmgraph' and details['graph']==graph
    assert details['previous_model_handle']=='11' and details['model_handle']=='22'
    assert details['transition_has_recorded_recipe'] is False
    assert 'no recorded pose recipe' in str(caught.value)
    assert 'ending at tick 11' in str(caught.value)
    if stage=='prepare':
        report=json.loads((root/'prepared/animation_asset_report.json').read_text())
        assert report['diagnostics'][0]['details']==details
        assert not converter.decoded
    else:
        assert not (root/'evaluated/animated_segment.json').exists()
