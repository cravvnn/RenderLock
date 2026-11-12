import copy
import pytest
from agr.particle_snapshot_provider import (RESOURCE,POLICY,DEFINITIONS,binding_header,
    bind_provider,validate_provider,request_inputs,validate_response,validate_definitions)


@pytest.fixture
def bound(tmp_path):
    model=dict(schema_version=1,resource='models/heroes_wip/priest/priest.vmdl_c',resource_sha256='b'*64,
               names=['root','crossbow'],parents=[-1,0],definitions=[])
    entity=dict(id='3:1',samples=[dict(tick=t,position=[t,0,0])for t in range(10,13)],model_constraints=model)
    for name in ('model_file','skeleton_file','pose_file'):
        path=tmp_path/name;path.write_bytes(name.encode());entity[name]=str(path)
    instance=dict(id='8:1',index=8,generation=1,entity_id='3:1',entity_handle=16387,resource=RESOURCE,created_tick=10,events=[])
    instance['events']=[dict(tick=10,type='CREATE',data=dict(create_particle=dict(entity_handle=16387,attach_type=2))),
        dict(tick=10,type='UPDATE_ENT',data=dict(update_particle_ent=dict(control_point=0,entity_handle=16387,attach_type=1,attachment=0)))]
    scene=dict(start_tick=10,end_tick=12,tick_rate=64,entities=[entity],particle_instances=[instance],metadata=dict(demo_sha256='a'*64))
    inventory=dict(schema_version=1,policy='agr_authored_model_rest_v1',model=model['resource'].removesuffix('_c'),
        source_sha256=model['resource_sha256'],names=model['names'],parents=model['parents'],
        parent_transforms=[[0,0,0,1,0,0,0,1],[10,0,0,1,0,0,0,1]])
    header=binding_header(entity['id'],model)
    frames=[dict(tick=t,snapshot_model_bones=[dict(control_point=0,bone='crossbow',binding_id=header['binding_id'],
        matrix=[1,0,0,t,0,1,0,0,0,0,1,0])])for t in range(10,13)]
    return scene,instance,bind_provider(scene,instance,model,inventory,'c'*64,frames)


def test_bound_source_rest_transport_is_complete_isolated_and_roundtrips_ack(bound):
    scene,instance,provider=bound;before=copy.deepcopy(provider)
    assert validate_provider(provider,scene,instance)==provider
    points=[dict(tick=t,position=[0,0,0],rotation=[0,0,0,1])for t in range(10,13)]
    request=request_inputs(provider,scene,instance,points)
    assert request['snapshot_model_policy']==POLICY
    assert 'snapshot_model_bones' not in points[0] and provider==before
    assert [p['snapshot_model_bones'][0]['matrix'][3]for p in request['control_points']]==[10,11,12]
    row=dict(id=instance['id'],snapshot_model_input_policy=POLICY,snapshot_model_bindings=provider['bindings'],
             snapshot_model_queried_binding_ids=[provider['bindings'][0]['binding_id']],
             control_point_indices=[0,1,2,5],declared_control_point_inputs=[0,1,2,5],
             authored_control_point_indices=[3],authored_root_position_indices=[],unprovided_control_point_indices=[],
             materialized_control_point_indices=[0,1,2,3,5],control_point_input_policy='agr_authored_child_controls_v2')
    validate_response({'instances':[row]},instance['id'],provider['bindings'])
    bad=copy.deepcopy(row);bad['authored_control_point_indices']=[]
    with pytest.raises(ValueError):validate_response({'instances':[bad]},instance['id'],provider['bindings'])
    bad=copy.deepcopy(row);bad['snapshot_model_bindings'][0]['control_point']=False
    with pytest.raises(ValueError):validate_response({'instances':[bad]},instance['id'],provider['bindings'])
    row['snapshot_model_queried_binding_ids']=[]
    with pytest.raises(ValueError):validate_response({'instances':[row]},instance['id'],provider['bindings'])


@pytest.mark.parametrize('change',[
    lambda p:p.update(policy='unknown'),lambda p:p.update(rig_fingerprint='d'*64),
    lambda p:p.update(schema_version=True),lambda p:p['bindings'][0].update(control_point=False),
    lambda p:p.update(definition_sha256={}),lambda p:p.update(end_tick=13),
    lambda p:p['inventory'].update(source_sha256='d'*64),lambda p:p['inventory'].update(parents=[0,-1]),
    lambda p:p['inventory']['parent_transforms'][1].__setitem__(0,11),
    lambda p:p['frames'].pop(),lambda p:p['frames'][1].update(tick=10),
    lambda p:p['frames'][0]['snapshot_model_bones'][0].update(control_point=1),
    lambda p:p['frames'][0]['snapshot_model_bones'][0].update(binding_id='d'*64),
    lambda p:p['frames'][0]['snapshot_model_bones'][0]['matrix'].__setitem__(0,2),
    lambda p:p['frames'][0]['snapshot_model_bones'][0]['matrix'].__setitem__(3,float('nan')),
])
def test_changed_rest_model_frames_or_self_digest_cannot_be_used(bound,change):
    scene,instance,provider=bound;change(provider)
    with pytest.raises(ValueError):validate_provider(provider,scene,instance)


@pytest.mark.parametrize('change',[
    lambda s:s['metadata'].update(demo_sha256='d'*64),lambda s:s['entities'][0]['samples'][0]['position'].__setitem__(0,999),
    lambda s:s['entities'][0]['model_constraints']['names'].reverse(),
    lambda s:s['particle_instances'][0].update(created_tick=11),
    lambda s:s['metadata'].update(coordinate_system=dict(origin=[1,0,0])),
])
def test_replay_tracks_order_lifecycle_and_coordinate_changes_invalidate_packets(bound,change):
    scene,instance,provider=bound;change(scene)
    with pytest.raises(ValueError):validate_provider(provider,scene,instance)


def test_existing_provider_or_missing_control_frame_cannot_be_overwritten(bound):
    scene,instance,provider=bound;points=[dict(tick=t)for t in range(10,13)]
    points[-1]['snapshot_model_bones']=[];before=copy.deepcopy(points)
    with pytest.raises(ValueError):request_inputs(provider,scene,instance,points)
    assert points==before
    with pytest.raises(ValueError):request_inputs(provider,scene,instance,points[:2])


def test_profile_cannot_silently_accept_missing_or_changed_definitions():
    with pytest.raises(ValueError):validate_definitions({})
    with pytest.raises(ValueError):validate_definitions({name:{}for name in DEFINITIONS})


@pytest.mark.parametrize('change',[
    lambda b:b.update(control_point=1),lambda b:b.update(attach_type=5,attachment=1),
    lambda b:b.update(entity_handle=16388),lambda b:b.update(offset_position=dict(x=1)),
    lambda b:b.update(fallback_position=dict(x=1)),lambda b:b.update(include_wearables=1),
])
def test_snapshot_packet_cannot_infer_its_model_from_a_different_cp0_binding(bound,change):
    scene,instance,provider=bound
    change(instance['events'][1]['data']['update_particle_ent'])
    with pytest.raises(ValueError):validate_provider(provider,scene,instance)
