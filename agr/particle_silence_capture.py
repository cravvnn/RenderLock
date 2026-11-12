"""Observed Silence birth with finite CP0 fallback, separate from Crossbow.

The actual unit-scale/identity-local native mode3/mode1 equivalence uses owner
flags0, for which neither the recorded nor observed fallback is selected. Both
values stay explicit and distinct. Other owners/models and CP0 rebinding remain
outside this evidence.
"""
import copy
import math
import struct
from .captured_slide_binding import _source_scene_identity
from .particle_snapshot_provider import digest,_sha
from .particle_custom_origin import custom_origin_transform
CLIENT_SHA256='2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7'
PARTICLES_SHA256='5d5e29757162d86a4bb940c0f0b0189fbf12598d4238a7d27ec4082e9740ff7b'
IDENTITY=[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.]]

def _scene_identity(scene,instance):
    return _source_scene_identity(dict(scene,particle_instances=[instance]),[instance['entity_id']])

POLICY='observed_silence_birth_fallback_v1'
RESOURCE='particles/modifiers/silenced_debuff.vpcf'

def _cp0(instance,controls):
    from .recorded_effect_simulation import zero_binding_offsets
    rows=[e for e in controls['entity_binding_events']if e.get('data',{}).get('update_particle_ent',{}).get('control_point')==0]
    if len(rows)!=1 or rows[0]['tick']!=instance['created_tick']:
        raise ValueError('Silence requires exactly one birth-time CP0 binding')
    b=rows[0]['data']['update_particle_ent']
    if (type(b.get('control_point')) is not int or type(b.get('attach_type')) is not int or b['attach_type']!=1
            or type(b.get('attachment')) is not int or b['attachment']!=0
            or type(b.get('entity_handle')) is not int or b['entity_handle']!=instance['entity_handle']
            or type(b.get('include_wearables',False)) is not bool
            or set(b)-{'control_point','entity_handle','attach_type','attachment','include_wearables',
                       'offset_position','offset_angles','fallback_position'}):
        raise ValueError('Silence requires its unnamed exact owner CP0 mode1')
    zero_binding_offsets(b)
    fallback=b.get('fallback_position')
    if (not isinstance(fallback,dict) or set(fallback)!=set('xyz')
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in fallback.values())):
        raise ValueError('Silence requires its explicit finite fallback position')
    try:values=struct.unpack('<3f',struct.pack('<3f',*(fallback[k]for k in 'xyz')))
    except (struct.error,OverflowError)as error:raise ValueError('Silence fallback exceeds Source float32')from error
    if any(not math.isfinite(v) or abs(v)>=3e38 for v in values):raise ValueError('Silence requires a finite position, not an unset fallback sentinel')
    if any(row['control_point']==0 for row in controls['literal_transforms']):
        raise ValueError('Silence cannot qualify literal CP0 replacement')
    return list(values)

def _validate(capture,scene,instance):
    from .particle_control_events import compile_control_events
    from .particle_history import history_start
    birth=instance.get('created_tick');handle=instance.get('entity_handle')
    if (not isinstance(capture,dict) or type(capture.get('schema_version')) is not int or capture['schema_version']!=1
            or capture.get('policy')!=POLICY or capture.get('client_sha256')!=CLIENT_SHA256
            or capture.get('particles_sha256')!=PARTICLES_SHA256 or instance.get('resource')!=RESOURCE
            or capture.get('resource')!=RESOURCE or type(birth) is not int
            or any(type(capture.get(k)) is not int or capture[k]!=birth for k in ('created_tick','tick'))
            or capture.get('entity_id')!=instance.get('entity_id') or type(handle) is not int or not 0<handle<0xffffffff
            or instance.get('entity_id')!=f'{handle&0x3fff}:{handle>>14}'
            or not _sha(capture.get('raw_capture_sha256')) or not _sha(capture.get('demo_sha256'))
            or capture['demo_sha256']!=scene.get('metadata',{}).get('demo_sha256')):
        raise ValueError('Silence birth requires its exact qualified replay, resource and birth capture')
    entities=[e for e in scene['entities']if e.get('id')==instance['entity_id']]
    if len(entities)!=1:raise ValueError('Silence owner is missing or ambiguous')
    model=entities[0].get('model_constraints')or{}
    if (entities[0].get('model_tracks') or model.get('resource')!='models/heroes_wip/priest/priest.vmdl_c'
            or not _sha(model.get('resource_sha256')) or capture.get('model')!=model['resource']
            or capture.get('model_sha256')!=model['resource_sha256']):
        raise ValueError('Silence birth requires the exact stable observed body model')
    runtime=(handle>>14)<<15|(handle&0x3fff)
    if (capture.get('coherent') is not True or type(capture.get('identity_flags')) is not int or capture['identity_flags']!=0
            or type(capture.get('runtime_owner_handle')) is not int or capture['runtime_owner_handle']!=runtime
            or type(capture.get('collection_owner_handle')) is not int or capture['collection_owner_handle']!=runtime
            or type(capture.get('skeleton_vtable_rva')) is not int or capture['skeleton_vtable_rva']not in(0x285dc98,0x2c20ef8)
            or capture.get('model_method')!='stock_dynamic_component_skeleton_model_getter_v1'):
        raise ValueError('Silence birth requires coherent native owner/model selection with flags0')
    srt=capture.get('owner_srt')
    if (not isinstance(srt,list) or len(srt)!=8 or any(type(v)not in(int,float)or not math.isfinite(v)for v in srt)
            or srt[3]!=1 or abs(sum(v*v for v in srt[4:])-1)>2e-5):
        raise ValueError('Silence birth requires explicit unit Source owner scale')
    custom_origin_transform(srt,IDENTITY)
    controls=compile_control_events(instance,history_start(scene),scene['end_tick']);fallback=_cp0(instance,controls)
    try:raw=bytes.fromhex(capture['cp0_record']);collection=bytes.fromhex(capture['collection_record'])
    except (KeyError,ValueError,TypeError)as error:raise ValueError('Silence native CP0 record is missing')from error
    if (len(raw)!=88 or len(collection)!=0xe0 or struct.unpack_from('<h',raw)[0]!=0
            or struct.unpack_from('<i',raw,4)[0]!=1 or struct.unpack_from('<Q',raw,0x50)[0]!=runtime
            or struct.unpack_from('<Q',collection,0x60)[0]!=runtime
            or list(struct.unpack_from('<12f',raw,0x1c))!=sum(IDENTITY,[])):
        raise ValueError('Silence requires observed CP0 mode1 and identity local matrix')
    observed=struct.unpack_from('<3f',raw,0x10)
    if any(not math.isfinite(v)or abs(v)>=3e38 for v in observed):
        raise ValueError('Silence requires an explicit finite observed fallback')
    creation=instance['events'][0]['data'].get('create_particle')or{}
    if (type(creation.get('attach_type')) is not int or creation['attach_type']!=3
            or type(creation.get('entity_handle')) is not int or creation['entity_handle']!=handle
            or creation.get('cluster',False) is not False
            or set(creation)-{'attach_type','entity_handle','cluster','particle_name_index'}
            or creation.get('particle_name_index')!=instance.get('resource_hash')):
        raise ValueError('Silence birth requires unchanged owned creation mode3 without local overrides')
    return capture

def bind_silence_capture(capture,scene,instance):
    _validate(capture,scene,instance)
    value=dict(schema_version=1,policy=POLICY,instance_id=instance['id'],scene_identity=_scene_identity(scene,instance),capture=copy.deepcopy(capture))
    return dict(value,content_sha256=digest(value))

def validate_silence_capture(package,scene,instance):
    if (not isinstance(package,dict) or type(package.get('schema_version'))is not int or package['schema_version']!=1
            or package.get('policy')!=POLICY or package.get('instance_id')!=instance.get('id')
            or package.get('scene_identity')!=_scene_identity(scene,instance)
            or package.get('content_sha256')!=digest({k:v for k,v in package.items()if k!='content_sha256'})):
        raise ValueError('Silence birth replay, rig, event history or contents changed')
    return _validate(package.get('capture'),scene,instance)

def request_header(package,scene,instance):
    from .particle_control_events import compile_control_events
    from .particle_history import history_start
    capture=validate_silence_capture(package,scene,instance)
    fallback=_cp0(instance,compile_control_events(instance,history_start(scene),scene['end_tick']))
    observed=list(struct.unpack_from('<3f',bytes.fromhex(capture['cp0_record']),0x10))
    return dict(policy=POLICY,client_sha256=CLIENT_SHA256,particles_sha256=PARTICLES_SHA256,
        entity_id=instance['entity_id'],resource=RESOURCE,created_tick=instance['created_tick'],model=capture['model'],
        model_sha256=capture['model_sha256'],owner_scale=1,identity_flags=0,authored_matrix=copy.deepcopy(IDENTITY),
        fallback_position=fallback,observed_fallback_position=observed,creation_mode=3,observed_mode=1,capture_sha256=package['content_sha256'])

def validate_response(simulation,instance_id,header):
    rows=simulation.get('instances')
    if (not isinstance(rows,list)or len(rows)!=1 or rows[0].get('id')!=instance_id
            or digest(rows[0].get('silence_birth_provider'))!=digest(header)):
        raise ValueError('Simulator Silence birth acknowledgement differs from its observed provider')
    from .particle_control_provenance import confirmed_points
    expected=dict(control_point_indices=[0,1,2],declared_control_point_inputs=[0,1,2],
        authored_control_point_indices=[3,4],authored_root_position_indices=[3,4],
        unprovided_control_point_indices=[],materialized_control_point_indices=[0,1,2,3,4])
    row=rows[0]
    if (any(not confirmed_points(row,k,v)for k,v in expected.items())
            or row.get('control_point_input_policy')!='agr_authored_root_position_v4'
            or row.get('native_static_controls') is not None):
        raise ValueError('Silence response lacks exact recorded and authored control-point provenance')
