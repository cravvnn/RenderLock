"""Input-only tests; fixtures are not live capture or emitted-particle proof."""
import copy
import pytest

from agr import particle_silence_inputs as inputs
from agr.particle_silence_capture import bind_silence_capture
from agr.particle_client_capture import bind_client_child_capture
from test_particle_silence_capture import source
from test_particle_hitbox_transport import inputs as hitbox_fixture


def test_pair_keeps_independent_envelopes_and_original_events(source):
    raw,scene,instance,child=source;before=copy.deepcopy(instance)
    birth=bind_silence_capture(raw,scene,instance);child=bind_client_child_capture(child,scene,instance)
    result=inputs.prepare_observed_inputs(scene,instance,birth,child)
    assert result['birth']==birth and result['child']==child and result['model_controls']==[0]
    assert result['birth_header']['fallback_position']==[-1054.90625,28.6875,621.9375]
    assert instance==before
    result['birth']['capture']['identity_flags']=128
    assert birth['capture']['identity_flags']==0


@pytest.mark.parametrize('which',['birth','child','child_schema','current_native'])
def test_one_observation_cannot_substitute_for_another_or_a_new_native_profile(source,which):
    raw,scene,instance,child=source
    birth=bind_silence_capture(raw,scene,instance);child=bind_client_child_capture(child,scene,instance)
    if which=='birth':birth=None
    if which=='child':child=None
    if which=='child_schema':child['capture']['schema_version']=True
    if which=='current_native':birth['capture']['client_sha256']='d0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5'
    with pytest.raises(ValueError):inputs.prepare_observed_inputs(scene,instance,birth,child)


def test_dense_hitbox_frame_additions_preserve_header_and_source_pose():
    inventory,pose=hitbox_fixture();pose['control_point']=0
    a=copy.deepcopy(pose);b=dict(copy.deepcopy(pose),tick=11);b['transforms'][0][0]=9
    before=copy.deepcopy((inventory,a,b))
    result=inputs.prepare_hitbox_frames(inventory,[a,b],entity_id=a['entity_id'],start_tick=10,end_tick=11)
    assert len(result['bindings'])==1 and [r['tick'] for r in result['frames']]==[10,11]
    assert result['frames'][0]['model_hitboxes'][0]['binding_id']==result['frames'][1]['model_hitboxes'][0]['binding_id']
    assert result['frames'][1]['model_hitboxes'][0]['sets']['core'][0]['transform'][0]==9
    assert (inventory,a,b)==before


@pytest.mark.parametrize('kind',['missing','duplicate_tick','wrong_cp','bool_pose','bool_schema','oversize'])
def test_incomplete_or_untyped_hitbox_input_never_becomes_empty_history(kind):
    inventory,pose=hitbox_fixture();pose['control_point']=0
    poses=[pose,dict(copy.deepcopy(pose),tick=11)]
    if kind=='missing':poses.pop()
    if kind=='duplicate_tick':poses[1]['tick']=10
    if kind=='wrong_cp':poses[0]['control_point']=1
    if kind=='bool_pose':poses[0]['transforms'][0][3]=True
    if kind=='bool_schema':inventory['schema_version']=True
    if kind=='oversize':inventory['hitbox_sets'][0]['boxes']*=4097
    with pytest.raises(ValueError):inputs.prepare_hitbox_frames(inventory,poses,entity_id=pose['entity_id'],start_tick=10,end_tick=11)


def test_hitbox_acknowledgement_keeps_integer_cp_distinct_from_boolean():
    headers=[dict(control_point=0)]
    row=dict(id='s:1',model_hitbox_input_policy=inputs.HITBOX_POLICY,model_hitbox_bindings=copy.deepcopy(headers))
    inputs.validate_hitbox_response(dict(instances=[row]),'s:1',headers)
    row['model_hitbox_bindings'][0]['control_point']=False
    with pytest.raises(ValueError):inputs.validate_hitbox_response(dict(instances=[row]),'s:1',headers)
