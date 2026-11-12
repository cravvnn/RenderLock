import copy
import struct
import pytest
from agr.particle_silence_capture import (POLICY,RESOURCE,CLIENT_SHA256,PARTICLES_SHA256,IDENTITY,
    bind_silence_capture,validate_silence_capture,request_header,validate_response)
from agr.particle_silence_inputs import scene_point_bindings
from agr.particle_silence_inputs import model_controls,validate_definitions,SILENCE
from agr.particle_client_capture import bind_client_child_capture,CURRENT_CLIENT_SHA256

@pytest.fixture
def source(tmp_path):
    handle=(895<<14)|56;runtime=(895<<15)|56;fallback=dict(x=-1054.90625,y=28.6875,z=621.9375)
    instance=dict(id='5718:1',index=5718,generation=1,entity_id='56:895',entity_handle=handle,resource=RESOURCE,
        resource_hash='123',created_tick=10,destroy_tick=None,events=[
        dict(type='CREATE',tick=10,data=dict(create_particle=dict(entity_handle=handle,attach_type=3,particle_name_index='123'))),
        dict(type='UPDATE_ENT',tick=10,data=dict(update_particle_ent=dict(control_point=0,entity_handle=handle,attach_type=1,attachment=0,include_wearables=True,fallback_position=fallback))),
        dict(type='UPDATE_ENT',tick=10,data=dict(update_particle_ent=dict(control_point=1,entity_handle=handle,attach_type=5,attachment=3,include_wearables=True))),
        dict(type='UPDATE_TRANSFORM',tick=10,data=dict(update_particle_transform=dict(control_point=2,position=dict(x=.87,y=0,z=0))))])
    entity=dict(id='56:895',model_constraints=dict(schema_version=1,resource='models/heroes_wip/priest/priest.vmdl_c',resource_sha256='b'*64,names=['root']),samples=[])
    for name in ('model_file','skeleton_file','pose_file'):
        p=tmp_path/name;p.write_bytes(name.encode());entity[name]=str(p)
    scene=dict(start_tick=10,end_tick=12,tick_rate=64,entities=[entity],particle_instances=[instance],metadata=dict(demo_sha256='a'*64))
    record=bytearray(88);collection=bytearray(0xe0)
    struct.pack_into('<i',record,4,1);struct.pack_into('<12f',record,0x1c,*sum(IDENTITY,[]));struct.pack_into('<3f',record,0x10,*fallback.values())
    struct.pack_into('<Q',record,0x50,runtime);struct.pack_into('<Q',collection,0x60,runtime)
    raw=dict(schema_version=1,policy=POLICY,client_sha256=CLIENT_SHA256,particles_sha256=PARTICLES_SHA256,
        entity_id='56:895',resource=RESOURCE,created_tick=10,tick=10,raw_capture_sha256='c'*64,demo_sha256='a'*64,
        model=entity['model_constraints']['resource'],model_sha256='b'*64,runtime_owner_handle=runtime,collection_owner_handle=runtime,
        coherent=True,identity_flags=0,skeleton_vtable_rva=0x285dc98,model_method='stock_dynamic_component_skeleton_model_getter_v1',
        owner_srt=[1,2,3,1,0,0,0,1],cp0_record=record.hex(),collection_record=collection.hex())
    child=dict(schema_version=1,policy='observed_parent_attachment_selection_v1',client_sha256=CURRENT_CLIENT_SHA256,
        raw_capture_sha256='d'*64,entity_id='56:895',tick=10,model=raw['model'],model_sha256='b'*64,
        graph_coherence_policy='double_read_links_class_flags_v1',graph_coherent=True,eligible_render_children=0,graph_nodes=1,graph_sha256='e'*64)
    return raw,scene,instance,child

def response(instance,header):
    return dict(id=instance['id'],silence_birth_provider=header,control_point_indices=[0,1,2],declared_control_point_inputs=[0,1,2],
        authored_control_point_indices=[3,4],authored_root_position_indices=[3,4],unprovided_control_point_indices=[],
        materialized_control_point_indices=[0,1,2,3,4],control_point_input_policy='agr_authored_root_position_v4')

def test_silence_capture_cannot_grant_child_or_hitbox_evidence(source):
    raw,scene,instance,child=source;before=copy.deepcopy(instance)
    capture=bind_silence_capture(raw,scene,instance)
    with pytest.raises(ValueError):scene_point_bindings(instance,10,12,['56:895'])
    with pytest.raises(ValueError):scene_point_bindings(instance,10,12,['56:895'],silence_birth_capture=capture,scene=scene)
    child=bind_client_child_capture(child,scene,instance)
    with pytest.raises(ValueError):scene_point_bindings(instance,10,12,['56:895'],client_child_capture=child,scene=scene)
    bindings=scene_point_bindings(instance,10,12,['56:895'],client_child_capture=child,silence_birth_capture=capture,scene=scene)
    assert [b['control_point']for b in bindings]==[0,1,2]and instance==before
    assert model_controls(instance,bindings,child,scene,silence_birth_capture=capture)==[0]
    with pytest.raises(ValueError):model_controls(instance,bindings,child,scene)
    assert validate_silence_capture(capture,scene,instance)==raw
    header=request_header(capture,scene,instance);row=response(instance,header)
    validate_response(dict(instances=[row]),instance['id'],header)
    for key,value in [('silence_birth_provider',None),('authored_control_point_indices',[3]),('unprovided_control_point_indices',[4]),('declared_control_point_inputs',[0,1])]:
        bad=copy.deepcopy(row);bad[key]=value
        with pytest.raises(ValueError):validate_response(dict(instances=[bad]),instance['id'],header)
    bad=copy.deepcopy(row);bad['silence_birth_provider']['identity_flags']=False
    with pytest.raises(ValueError):validate_response(dict(instances=[bad]),instance['id'],header)

@pytest.mark.parametrize('field,value',[
    ('schema_version',True),('client_sha256','f'*64),('particles_sha256','f'*64),('policy','observed_crossbow_birth_cp0_equivalence_v1'),
    ('entity_id','56:896'),('created_tick',11),('tick',11),('tick',True),('demo_sha256','f'*64),
    ('resource','particles/abilities/priest/priest_crossbow_buff.vpcf'),('model','models/other.vmdl_c'),('model_sha256','f'*64),
    ('coherent',False),('identity_flags',128),('identity_flags',False),('skeleton_vtable_rva',0),
    ('runtime_owner_handle',56),('collection_owner_handle',56),('model_method','guessed'),
    ('owner_srt',[1,2,3,2,0,0,0,1]),('owner_srt',[1,2,3,1,0,0,0,2]),('owner_srt',[1,2,3,True,0,0,0,1]),
    ('cp0_record','00'),('cp0_record',None),('collection_record','00'),
])
def test_unqualified_observation_cannot_be_rebound(source,field,value):
    raw,scene,instance,_=source;raw[field]=value
    with pytest.raises(ValueError):bind_silence_capture(raw,scene,instance)

@pytest.mark.parametrize('offset,fmt,value',[(0,'<h',1),(4,'<i',3),(0x50,'<Q',56),(0x10,'<f',float('inf')),(0x1c,'<f',2.),(0x28,'<f',10.)])
def test_actual_cp_record_and_fallback_must_match(source,offset,fmt,value):
    raw,scene,instance,_=source;record=bytearray.fromhex(raw['cp0_record']);struct.pack_into(fmt,record,offset,value);raw['cp0_record']=record.hex()
    with pytest.raises(ValueError):bind_silence_capture(raw,scene,instance)

def test_observed_and_recorded_fallbacks_are_not_silently_overwritten(source):
    raw,scene,instance,_=source;original=copy.deepcopy(instance)
    record=bytearray.fromhex(raw['cp0_record']);struct.pack_into('<3f',record,0x10,-1058.7778,28.571655,621.6479);raw['cp0_record']=record.hex()
    capture=bind_silence_capture(raw,scene,instance);header=request_header(capture,scene,instance)
    assert header['fallback_position']==[-1054.90625,28.6875,621.9375]
    assert header['observed_fallback_position']==list(struct.unpack_from('<3f',record,0x10))
    assert header['observed_fallback_position']!=header['fallback_position']and instance==original

@pytest.mark.parametrize('kind',['late','duplicate','missing_fallback','different_fallback','unset_fallback','offset','creation','file','pose','digest','combined'])
def test_source_and_lifecycle_changes_are_refused(source,kind):
    raw,scene,instance,_=source;capture=bind_silence_capture(raw,scene,instance)
    binding=instance['events'][1]['data']['update_particle_ent']
    if kind=='late':instance['events'][1]['tick']=11;instance['events']=instance['events'][:2]
    if kind=='duplicate':instance['events'].append(copy.deepcopy(instance['events'][1]));instance['events'][-1]['tick']=11
    if kind=='missing_fallback':binding.pop('fallback_position')
    if kind=='different_fallback':binding['fallback_position']['x']=99
    if kind=='unset_fallback':binding['fallback_position']=dict.fromkeys('xyz',float('inf'))
    if kind=='offset':binding['offset_angles']=dict(z=1)
    if kind=='creation':instance['events'][0]['data']['create_particle']['attach_type']=2
    if kind=='file':
        from pathlib import Path
        Path(scene['entities'][0]['pose_file']).write_bytes(b'edited')
    if kind=='pose':scene['entities'][0]['samples']=[dict(tick=10,position=[9,9,9])]
    if kind=='digest':capture['capture']['identity_flags']=128
    if kind=='combined':
        with pytest.raises(ValueError,match='cannot be combined'):
            scene_point_bindings(instance,10,12,['56:895'],silence_birth_capture=capture,birth_override_capture={},scene=scene)
        return
    with pytest.raises(ValueError):validate_silence_capture(capture,scene,instance)

def test_exact_definition_tree_required():
    with pytest.raises(ValueError):validate_definitions({},SILENCE)
