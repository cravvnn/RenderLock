"""Unowned, literal-at-birth bat explosions; no entity stop policy is inherited.

Current native CREATE forwarding, mode8 initialization and no-aux RELEASE unlink
are recorded in the private world-origin qualification. This profile admits only
CREATE, CP0 literal and RELEASE at one birth tick. Seeds/simulation remain an AGR
approximation. It does not establish exact Source spawn age or decal parity.
"""
import math
from .particle_events import WORLD_CUSTOM_ORIGIN_SCOPE,is_world_custom_origin_candidate
from .particle_control_events import compile_control_events
from .particle_value_identity import digest,_sha
from .vendor.resource_hash import resource_id

POLICY='agr_world_literal_birth_release_v1'
BACKEND='simulated_world_origin_v1'
RESOURCE='particles/abilities/vampirebat/vampirebat_batswarm_explosion.vpcf'
CLIENT_SHA256='2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7'
DEFINITIONS={
 RESOURCE:'4f7919166937fb6ae394fd2193389c2c709e8b5304860301bc000188105d52a4',
 RESOURCE.replace('.vpcf','_proj.vpcf'):'23ba75f3b7be4b67ae7d888441575745751d03487e44aa9aee4b9ad54f70aff8',
 RESOURCE.replace('.vpcf','_ring.vpcf'):'ff27f49027555be8ef3520e6fc151716e1add19d56692c573999bb618937661f',
 RESOURCE.replace('.vpcf','_core.vpcf'):'108fe8e331aecb08faddfa7c3b3c2bf59c1b7544464934a789a901fe8dcf8fba',
 RESOURCE.replace('.vpcf','_blood.vpcf'):'d593eb4e59ab5895c20de9e3f58cef3f449e222b34564b9ad816873da7935277',
}

def require(ok,message):
 if not ok:raise ValueError('World-origin particles: '+message)

def literal_birth(scene,instance):
 """Validate a complete narrow generation; return its only recorded literal CP."""
 require(isinstance(instance,dict) and instance.get('selection_scope')==WORLD_CUSTOM_ORIGIN_SCOPE
         and is_world_custom_origin_candidate(instance),'explicit unattached creation provenance is required')
 require(instance.get('resource')==RESOURCE,'resource is outside the qualified birth/release profile')
 start,end,birth,rate=(scene.get('start_tick'),scene.get('end_tick'),instance.get('created_tick'),scene.get('tick_rate'))
 require(all(type(v)is int for v in (start,end,birth,rate)) and rate==64 and 0<=start<=birth<=end<2**31-1
         and end-birth<=4095,'requires an in-range birth and bounded inclusive 64Hz output')
 require(_sha(scene.get('metadata',{}).get('demo_sha256')),'replay SHA256 is required')
 coordinates=scene.get('metadata',{}).get('coordinate_system',{})
 require(coordinates.get('unit_scale',.0254)==.0254 and coordinates.get('origin',[0,0,0])==[0,0,0]
         and coordinates.get('world_yaw_degrees',0)==0,'original Source coordinates are required')
 index,generation=instance.get('index'),instance.get('generation')
 require(type(index)is int and 0<=index<2**31 and type(generation)is int and 1<=generation<2**31
         and instance.get('id')==f'{index}:{generation}' and instance.get('entity_handle')==0xffffff,
         'exact generation and the qualified invalid owner handle are required')
 events=instance.get('events')
 require(isinstance(events,list) and len(events)==3 and all(isinstance(e,dict)for e in events)
         and [e.get('type')for e in events]==['CREATE','UPDATE_TRANSFORM','RELEASE']
         and all(type(e.get('tick'))is int and e['tick']==birth for e in events),
         'requires CREATE, CP0 literal and RELEASE at birth; destruction and later changes are unqualified')
 require(all(type(e.get('order'))is int for e in events) and 0<=events[0]['order'] and events[2]['order']<2**31
         and [e['order']for e in events]==list(range(events[0]['order'],events[0]['order']+3)),
         'complete consecutive source event order is required')
 creation=events[0].get('data',{}).get('create_particle')
 require(creation==instance.get('create') and isinstance(creation,dict)
         and set(creation)=={'particle_name_index','attach_type','entity_handle','cluster'}
         and creation.get('cluster')is False and creation.get('particle_name_index')==str(resource_id(RESOURCE))
         and instance.get('resource_hash')==creation['particle_name_index'],'creation fields or resource identity differ')
 require(all(isinstance(e.get('data'),dict) and set(e['data'])=={'type','index',payload}
             and e['data']['type']=='GAME_PARTICLE_MANAGER_EVENT_'+e['type'] and type(e['data']['index'])is int
             and e['data']['index']==index for e,payload in zip(events[:2],('create_particle','update_particle_transform'))),
         'unknown or inconsistent recorded event payload')
 release=events[2].get('data',{})
 require(isinstance(release,dict) and {'type','index'}<=set(release)<={'type','index','release_particle_index'}
         and release.get('type')=='GAME_PARTICLE_MANAGER_EVENT_RELEASE'
         and type(release.get('index'))is int and release['index']==index
         and release.get('release_particle_index',{})=={},'release carries unqualified fields')
 controls=compile_control_events(instance,birth,end)
 require(controls['destroy']is None and not controls['entity_binding_events'] and controls['release_ticks']==[birth]
         and len(controls['literal_transforms'])==1,'world controls or lifetime differ')
 literal=controls['literal_transforms'][0]
 raw=events[1]['data']['update_particle_transform']
 require(literal['control_point']==0 and literal['rotation']==[0,0,0,1]
         and isinstance(raw.get('position'),dict) and set(raw['position'])=={'x','y','z'}
         and all(math.isfinite(v) and abs(v)<=131072 for v in literal['position']),
         'explicit finite CP0 position and identity orientation are required')
 return literal

def request_header(scene,instance):
 literal_birth(scene,instance)
 return dict(policy=POLICY,client_sha256=CLIENT_SHA256,instance_id=instance['id'],resource=RESOURCE,
  created_tick=instance['created_tick'],start_tick=scene['start_tick'],end_tick=scene['end_tick'],tick_rate=scene['tick_rate'],
  demo_sha256=scene['metadata']['demo_sha256'],events_sha256=digest(instance['events']))

def control_points(scene,instance):
 literal=literal_birth(scene,instance)
 return [dict(tick=tick,position=list(literal['position']),rotation=list(literal['rotation']))
         for tick in range(instance['created_tick'],scene['end_tick']+1)]

def world_identity(scene,instance):
 return digest(dict(header=request_header(scene,instance),source_instance=instance,
                    coordinates=scene['metadata'].get('coordinate_system')))

def validate_response(simulation,scene,instance):
 rows=simulation.get('instances')
 require(isinstance(rows,list) and len(rows)==1 and rows[0].get('id')==instance['id']
         and rows[0].get('world_origin_provider')==request_header(scene,instance),
         'simulator did not acknowledge this exact world provider; rebuild ParticleProbe')

def validate_definitions(definitions):
 require(isinstance(definitions,dict) and set(definitions)==set(DEFINITIONS)
         and all(digest(definitions[name])==sha for name,sha in DEFINITIONS.items()),
         'authored explosion tree differs from the qualified definitions')

def validate_package(package,scene):
 """Separate package identity: never pass world input through the owned gate."""
 from .recorded_effect_simulation import (validate_request_response,validate_seed_response,
     validate_event_response,validate_control_response,substitute_seed)
 require(type(package.get('schema_version'))is int and package['schema_version']==1 and package.get('backend')==BACKEND
         and package.get('entity_ids')==[] and isinstance(package.get('coverage_scope'),str)
         and bool(package['coverage_scope'].strip()),'requires a separate unowned package and declared scope')
 require(set(package)<= {'schema_version','backend','entity_ids','scene_identity','world_origin_provider',
         'simulation','assets','simulation_request_sha256','coverage_scope'},'owned or unknown package provider is forbidden')
 simulation=package.get('simulation',{})
 require(isinstance(simulation,dict),'simulation envelope is invalid')
 rows=simulation.get('instances',[])
 require(isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],dict),'one exact world generation is required')
 originals=[i for i in scene.get('particle_instances',[])if i.get('id')==rows[0].get('id')]
 require(len(originals)==1,'recorded source generation is missing or ambiguous')
 original=originals[0];header=request_header(scene,original)
 require(package.get('scene_identity')==world_identity(scene,original)
         and package.get('world_origin_provider')==header and rows[0].get('entity_id')is None,
         'source world generation, replay, range or coordinates changed')
 request_sha=package.get('simulation_request_sha256')
 require(_sha(request_sha),'exact simulator request digest is required')
 require(simulation.get('backend') and not simulation.get('warnings'),'unresolved simulation diagnostics')
 validate_request_response(simulation,original,scene['start_tick'],scene['end_tick'],scene['tick_rate'],request_sha)
 validate_seed_response(simulation,substitute_seed(scene,original))
 validate_response(simulation,scene,original)
 validate_event_response(simulation,original,original['created_tick'],scene['end_tick'])
 validate_control_response(simulation,original['id'],[dict(control_point=0)])
 require(not any(rows[0].get(field) is not None for field in ('custom_origin_provider','birth_override_provider',
         'silence_birth_provider','model_hitbox_input_policy','model_hitbox_bindings','snapshot_model_input_policy',
         'snapshot_model_bindings','snapshot_model_queried_binding_ids','native_static_controls'))
         and type(rows[0].get('model_bone_count'))is int and rows[0]['model_bone_count']==0
         and package.get('assets',{}).get('models')=={},
         'world and entity/model/default providers cannot be combined')
 validate_definitions(package.get('assets',{}).get('definitions'))
