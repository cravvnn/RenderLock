"""Exact owned flashbang literal-at-birth provider, separate from attachments.

This pure provider adds no clock, event scheduler or persistent state. It reuses
the recorded event compiler/helper acknowledgments and package transaction.
RELEASE removes ownership, not the effect; arbitrary later changes stay refused.
Native allocation, initial Source spawn age and pixel parity are not claimed.
"""
import math
from pathlib import Path
from .particle_control_events import compile_control_events,require_owned_event_provider
from .particle_value_identity import digest,_sha
from .vendor.resource_hash import resource_id
from .asset_converter import sha

POLICY='agr_owned_literal_birth_release_v1'
RESOURCE='particles/abilities/priest/priest_flashbang_explosion.vpcf'
CLIENT_SHA='d0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5'
PARTICLES_SHA='46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f'
_TREE={
 '':'50b91553c88947916b89254784df73852ffbb4539539568b4ba23c93cba0fb1c',
 '_bits':'49afada8ab6e1161267bc4c465d9d6a14ad6256f0c29b7a4a33e891e109311c7',
 '_detail':'f9c50c142f9eeb0f867fa140285a68c2e50fdb6f087a259b4e7e83e8030100c8',
 '_flame':'5ebdcaf09a5f29c3219fe0a0048c839e8e94dca42e29d6c7d35bbad1dfec89c8',
 '_flare':'17b72aec3bf04a8c8a39acfc2973529f546d3fdd2d125284401ef3b481db41c9',
 '_ground_proj':'a64666c537cfaeba6f33d1e5860ed47acc9d1b56f4bdf9fdeb79c9727f92d877',
 '_light':'f1368457bbb7c1789d976fa69bf57c640edec02a4f2ea94ebe4f6d1e2ad2bc23',
 '_rays':'e4ff38da8054da2923cc04c79268b8cb4c12be2f21381810948ae2c3e3451154',
 '_refraction':'8b236af8d8d151048d02aee0f034093b209c270f2f24f7cf951bcc6e4eb0aaaa',
 '_ring':'a0e20dcb3b68bdcc9c45039338579d39b7e607db81ce3d9ca93c6f9e46d01173',
 '_smoke':'43eb2c077e37ada77104eec55bbb0c41d4548be8e7e49478b4a8fb8373257fae',
 '_sparks':'3a811388cedb5f6441782dc015ff2d0298d069df91cefdf795afc493566e29cd',
 '_sphere':'1ead1f8e5e13b2f7506cfae8b5b7d8453addddd82309d635ef1c7e8bebb377c7',
}
DEFINITIONS={RESOURCE.removesuffix('.vpcf')+suffix+'.vpcf':value for suffix,value in _TREE.items()}


def require(ok,message):
    if not ok:raise ValueError('Owned literal particles: '+message)


def bindings(instance,start,end):
    require(isinstance(instance,dict) and instance.get('resource')==RESOURCE,'unqualified resource')
    birth=instance.get('created_tick');handle=instance.get('entity_handle')
    require(all(type(v)is int for v in (birth,start,end)) and 0<=start<=birth<=end<2**31
        and end-birth<=4095,'bounded complete birth history is required')
    require(type(handle)is int and 0<=handle<0xffffff and instance.get('entity_id')==f'{handle&0x3fff}:{handle>>14}'
        and instance.get('selection_scope')is None,'exact valid owner serial is required')
    require(type(instance.get('generation'))is int and 1<=instance['generation']<2**31,'bounded generation is required')
    events=instance.get('events')
    require(isinstance(events,list) and len(events)==4 and all(isinstance(e,dict)for e in events)
        and [e.get('type')for e in events]==['CREATE','UPDATE_TRANSFORM','UPDATE_TRANSFORM','RELEASE']
        and all(type(e.get('tick'))is int and e['tick']==birth for e in events),'requires complete literal birth/release events')
    require(all(type(e.get('order'))is int for e in events)
        and 0<=events[0]['order']<=2**31-4
        and [e['order']for e in events]==list(range(events[0]['order'],events[0]['order']+4)),
        'consecutive source order is required')
    controls=compile_control_events(instance,start,end);require_owned_event_provider(instance,controls,always=True)
    creation=events[0]['data'].get('create_particle')
    require(isinstance(creation,dict) and set(creation)=={'particle_name_index','attach_type','entity_handle','cluster'}
        and type(creation.get('attach_type'))is int and creation['attach_type']==2
        and creation.get('cluster')is False and instance.get('create')==creation
        and creation.get('particle_name_index')==instance.get('resource_hash')==str(resource_id(RESOURCE)),
        'exact original CREATE2 resource/owner fields are required')
    for event,payload in zip(events[:3],('create_particle','update_particle_transform','update_particle_transform')):
        data=event['data']
        require(set(data)=={'type','index',payload} and data['type']=='GAME_PARTICLE_MANAGER_EVENT_'+event['type'],
            'unknown or inconsistent source payload')
    release=events[-1]['data']
    require({'type','index'}<=set(release)<={'type','index','release_particle_index'}
        and release['type']=='GAME_PARTICLE_MANAGER_EVENT_RELEASE' and release.get('release_particle_index',{})=={},
        'unqualified release payload')
    require(controls['destroy']is None and not controls['entity_binding_events']
        and controls['release_ticks']==[birth] and len(controls['literal_transforms'])==2,
        'lifecycle or entity provider differs')
    for cp,(event,literal)in enumerate(zip(events[1:3],controls['literal_transforms'])):
        raw=event['data']['update_particle_transform']
        require(set(raw)=={'control_point','position'} and literal['control_point']==cp
            and isinstance(raw['position'],dict) and set(raw['position'])=={'x','y','z'}
            and literal['rotation']==[0,0,0,1] and all(math.isfinite(v) and abs(v)<=131072 for v in literal['position']),
            'requires explicit birth CP0/1 positions, absent orientation and zero interpolation')
    return [dict(control_point=cp,provider='literal_transform',updates=[literal])
        for cp,literal in enumerate(controls['literal_transforms'])]


def header(scene,instance):
    bindings(instance,scene['start_tick'],scene['end_tick'])
    require(type(scene.get('tick_rate'))is int and scene['tick_rate']==64,'qualified 64Hz replay is required')
    metadata=scene.get('metadata',{});coordinates=metadata.get('coordinate_system',{})
    require(_sha(metadata.get('demo_sha256')),'replay digest is required')
    require(coordinates.get('unit_scale',.0254)==.0254 and coordinates.get('origin',[0,0,0])==[0,0,0]
        and coordinates.get('world_yaw_degrees',0)==0,'original Source coordinates are required')
    return dict(policy=POLICY,client_sha256=CLIENT_SHA,particles_sha256=PARTICLES_SHA,
        instance_id=instance['id'],source_sha256=digest(instance),events_sha256=digest(instance['events']),
        demo_sha256=metadata['demo_sha256'],start_tick=scene['start_tick'],end_tick=scene['end_tick'],
        tick_rate=64,definitions_sha256=digest(DEFINITIONS))


def validate_definitions(definitions):
    require(isinstance(definitions,dict) and set(definitions)==set(DEFINITIONS)
        and all(digest(definitions[name])==value for name,value in DEFINITIONS.items()),'authored definition tree changed')


def collect(scene,instance,provider):
    result=header(scene,instance)
    for relative,expected in (('game/citadel/bin/win64/client.dll',CLIENT_SHA),('game/bin/win64/particles.dll',PARTICLES_SHA)):
        path=Path(provider.install_path)/relative
        require(path.is_file() and sha(path)==expected,'installed native profile changed')
    return result


def validate_package(package,scene):
    from .recorded_effect_simulation import validate_event_response,validate_control_response
    rows=package.get('simulation',{}).get('instances',[])
    require(len(rows)==1 and isinstance(rows[0],dict),'one exact original generation is required')
    originals=[i for i in scene.get('particle_instances',[])if i.get('id')==rows[0].get('id')]
    require(len(originals)==1,'original replay generation is missing or ambiguous')
    original=originals[0]
    require(package.get('owned_literal_provider')==header(scene,original)
        and package.get('entity_ids')==[original['entity_id']] and 'model_provider'not in package,
        'literal source/range/native provider differs')
    validate_definitions(package.get('assets',{}).get('definitions'))
    require(type(rows[0].get('model_bone_count'))is int and rows[0]['model_bone_count']==0
        and not any(rows[0].get(field)is not None for field in ('custom_origin_provider','world_origin_provider',
            'birth_override_provider','silence_birth_provider','model_hitbox_input_policy','model_hitbox_bindings',
            'snapshot_model_input_policy','snapshot_model_bindings','native_static_controls')),
        'literal inputs cannot inherit another model/world provider')
    validate_event_response(package['simulation'],original,scene['start_tick'],scene['end_tick'])
    validate_control_response(package['simulation'],original['id'],bindings(original,scene['start_tick'],scene['end_tick']))
