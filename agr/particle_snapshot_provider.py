"""Replay-bound Source-rest snapshot model transport for the Crossbow profile.

This validates supplied bone frames and immutable model/rest/scene identity.
Creation admission and client attachment selection remain separate gates; a
valid packet cannot qualify a different native creation or attachment mode.
"""
import copy
import hashlib
import json
import math
import re
import numpy as np

POLICY='agr_explicit_snapshot_bone_frames_v1'
PROFILE='source_rest_crossbow_snapshot_cp0_v1'
RESOURCE='particles/abilities/priest/priest_crossbow_buff.vpcf'
DEFINITIONS={
    RESOURCE:'c2260de83f1e117439356dd743a8f6e35c495cf7d22ba78bbdc74ec2ecfc2b91',
    'particles/abilities/priest/priest_crossbow_buff_beam.vpcf':'a7369d3a5de294cda26bd91b2d9bad7423486e3eac0f341194a536a371ce192a',
    'particles/abilities/priest/priest_crossbow_buff_beams.vpcf':'563e9ececf748e476f464e3687a429d5656769e06631b5d6413d31a26e26450c',
    'particles/abilities/priest/priest_crossbow_buff_crosshair.vpcf':'927062251790c72e3cd12db75c8c95467dd8dbfa6c3ef9ada427f5cc42676551',
    'particles/abilities/priest/priest_crossbow_buff_halo.vpcf':'0a52f4ab66e4d98ee5eea5041fa7cbb02d2a2f1ad38d7861aeea89df79ee682d',
    'particles/abilities/priest/priest_crossbow_buff_l_trace.vpcf':'27db69229d62ad66a3f95c013a391728cb8519014f690dfabe1799f591f00c78',
    'particles/abilities/priest/priest_crossbow_buff_r_trace.vpcf':'38565ec55a63091b252a1e99c598d318ba4bafdb5e0b2b3a77465eb7cef2b2b8',
    'particles/abilities/priest/priest_crossbow_buff_ring.vpcf':'8701b91f26616c7534ae74d19e3d0d8ce1d4ff2d63b33ae05c331caf547ddf32',
}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def _model_binding(scene,instance):
    from .particle_control_events import compile_control_events
    from .particle_history import history_start
    from .recorded_effect_simulation import zero_binding_offsets
    controls=compile_control_events(instance,history_start(scene),scene['end_tick'])
    rows=[e for e in controls['entity_binding_events']if e.get('data',{}).get('update_particle_ent',{}).get('control_point')==0]
    if len(rows)!=1 or rows[0]['tick']!=instance['created_tick']:
        raise ValueError('Snapshot requires exactly one creation-time owner CP0 binding')
    binding=rows[0]['data']['update_particle_ent']
    if (type(binding.get('control_point')) is not int or type(binding.get('attach_type')) is not int
            or type(binding.get('attachment')) is not int or binding['attach_type']!=1 or binding['attachment']!=0
            or type(binding.get('entity_handle')) is not int or binding['entity_handle']!=instance['entity_handle']
            or type(binding.get('include_wearables',False)) is not bool
            or set(binding)-{'control_point','entity_handle','attach_type','attachment','include_wearables',
                            'offset_position','offset_angles','fallback_position'}
            or any(row['control_point']==0 for row in controls['literal_transforms'])):
        raise ValueError('Snapshot requires the explicit unnamed owner CP0 model binding')
    zero_binding_offsets(binding)
    fallback=binding.get('fallback_position',{})
    if (not isinstance(fallback,dict) or set(fallback)-set('xyz')
            or any(type(v) not in (int,float) or not math.isfinite(v) or v!=0 for v in fallback.values())):
        raise ValueError('Snapshot owner CP0 fallback is unqualified')


def validate_definitions(definitions):
    if not isinstance(definitions,dict) or set(definitions)!=set(DEFINITIONS):
        raise ValueError('Snapshot effect definition tree is incomplete or changed')
    for name,value in definitions.items():
        if digest(value)!=DEFINITIONS[name]:raise ValueError('Snapshot definition changed: '+name)


def binding_header(entity_id,model,bone='crossbow',control_point=0):
    names=model.get('names')
    if (not isinstance(entity_id,str) or re.fullmatch(r'[0-9]+:[0-9]+',entity_id) is None
            or model.get('resource')!='models/heroes_wip/priest/priest.vmdl_c'
            or not _sha(model.get('resource_sha256')) or not isinstance(names,list)
            or not names or len(names)>4096 or any(not isinstance(n,str) or not n for n in names)
            or len(set(names))!=len(names) or bone!='crossbow' or bone not in names
            or type(control_point) is not int or control_point!=0):
        raise ValueError('Snapshot requires the exact selected Crossbow CP0 model and bone order')
    order=hashlib.sha256(json.dumps(names,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    header=dict(control_point=control_point,entity_id=entity_id,model=model['resource'],
                model_sha256=model['resource_sha256'],bone_order_sha256=order,bone=bone)
    canonical='\n'.join(str(header[k])for k in ('control_point','entity_id','model','model_sha256','bone_order_sha256','bone'))
    header['binding_id']=hashlib.sha256(canonical.encode()).hexdigest()
    return header


def rigid_matrix(values):
    if (not isinstance(values,list) or len(values)!=12
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in values)):
        raise ValueError('Snapshot requires a finite flat Source3x4 matrix')
    with np.errstate(over='ignore'):
        matrix=np.asarray(values,dtype=np.float32).reshape(3,4)
    linear=matrix[:,:3].astype(float)
    if (not np.isfinite(matrix).all() or np.max(abs(linear.T@linear-np.eye(3)))>2e-4
            or abs(np.linalg.det(linear)-1)>2e-4):
        raise ValueError('Snapshot requires a proper rigid Source transform')
    return matrix.reshape(-1).tolist()


def _frames(frames,header,birth,end):
    if (any(type(t) is not int for t in (birth,end)) or not 0<=birth<=end
            or end-birth>=100000 or not isinstance(frames,list) or len(frames)!=end-birth+1):
        raise ValueError('Snapshot frames require complete bounded creation-time history')
    result=[]
    for tick,frame in zip(range(birth,end+1),frames):
        if not isinstance(frame,dict) or set(frame)!={'tick','snapshot_model_bones'} or type(frame.get('tick')) is not int or frame['tick']!=tick:
            raise ValueError('Snapshot frame tick/order/fields changed')
        rows=frame['snapshot_model_bones']
        if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):
            raise ValueError('Snapshot frame requires exactly one qualified CP0/bone')
        row=rows[0]
        if (set(row)!={'control_point','bone','binding_id','matrix'} or type(row.get('control_point')) is not int
                or any(row.get(k)!=header[k] for k in ('control_point','bone','binding_id'))):
            raise ValueError('Snapshot frame changed its model/CP/bone binding')
        result.append(dict(tick=tick,snapshot_model_bones=[dict(row,matrix=rigid_matrix(row['matrix']))]))
    return result


def bind_provider(scene,instance,model,inventory,rig_fingerprint,frames):
    from .particle_model_rest import model_inverse_rest
    from .simulated_effect_package import scene_identity,model_constraints_identity
    handle=instance.get('entity_handle')
    originals=[i for i in scene.get('particle_instances',[])if i.get('id')==instance.get('id')]
    if (instance.get('resource')!=RESOURCE or not _sha(rig_fingerprint)
            or not _sha(scene.get('metadata',{}).get('demo_sha256'))
            or len(originals)!=1 or originals[0]!=instance or type(handle) is not int
            or not 0<=handle<0xffffffff or instance.get('entity_id')!=f'{handle&0x3fff}:{handle>>14}'):
        raise ValueError('Unqualified snapshot effect or rig fingerprint')
    _model_binding(scene,instance)
    entities=[e for e in scene['entities']if e.get('id')==instance.get('entity_id')]
    if not isinstance(model,dict) or len(entities)!=1 or entities[0].get('model_constraints')!=model:
        raise ValueError('Snapshot model differs from its exact source entity')
    header=binding_header(instance['entity_id'],model)
    coordinates=scene.get('metadata',{}).get('coordinate_system')or{}
    if (coordinates.get('unit_scale',.0254)!=.0254 or coordinates.get('origin',[0,0,0])!=[0,0,0]
            or coordinates.get('world_yaw_degrees',0)!=0):
        raise ValueError('Snapshot source transforms require unshifted Source coordinates')
    model_inverse_rest(inventory,resource=model['resource'],resource_sha256=model['resource_sha256'],names=model['names'])
    if inventory['parents']!=model.get('parents'):raise ValueError('Snapshot authored and rig parent orders differ')
    payload=dict(schema_version=1,profile=PROFILE,policy=POLICY,instance_id=instance['id'],entity_id=instance['entity_id'],
        scene_identity=scene_identity(scene,[instance['entity_id']]),resource=model['resource'],resource_sha256=model['resource_sha256'],
        model_constraints_sha256=model_constraints_identity(model),rig_fingerprint=rig_fingerprint,
        inventory=copy.deepcopy(inventory),bindings=[header],definition_sha256=dict(DEFINITIONS),
        start_tick=instance['created_tick'],end_tick=scene['end_tick'],
        frames=_frames(frames,header,instance['created_tick'],scene['end_tick']))
    return dict(payload,content_sha256=digest(payload))


def validate_provider(provider,scene,instance):
    if (not isinstance(provider,dict)
            or provider.get('content_sha256')!=digest({k:v for k,v in provider.items()if k!='content_sha256'})):
        raise ValueError('Missing or changed snapshot model provider contents')
    entities=[e for e in scene['entities']if e.get('id')==instance.get('entity_id')]
    if len(entities)!=1:raise ValueError('Missing or ambiguous snapshot owner')
    expected=bind_provider(scene,instance,entities[0].get('model_constraints')or{},provider.get('inventory'),
                           provider.get('rig_fingerprint'),provider.get('frames'))
    if provider!=expected:raise ValueError('Snapshot replay/model/rest/rig/frames/provider contents changed')
    return expected


def request_inputs(provider,scene,instance,points):
    value=validate_provider(provider,scene,instance)
    if not isinstance(points,list) or [p.get('tick')for p in points]!=[f['tick']for f in value['frames']]:
        raise ValueError('Snapshot and control-point frame ranges differ')
    if any('snapshot_model_bones' in point for point in points):
        raise ValueError('Snapshot frames cannot replace an existing provider')
    return dict(snapshot_model_policy=POLICY,snapshot_model_bindings=value['bindings'],
        control_points=[dict(point,snapshot_model_bones=copy.deepcopy(frame['snapshot_model_bones']))
                        for point,frame in zip(points,value['frames'])])


def validate_response(simulation,instance_id,headers):
    rows=simulation.get('instances')
    if (not isinstance(rows,list) or len(rows)!=1 or rows[0].get('id')!=instance_id
            or rows[0].get('snapshot_model_input_policy')!=POLICY or digest(rows[0].get('snapshot_model_bindings'))!=digest(headers)
            or rows[0].get('snapshot_model_queried_binding_ids')!=sorted(h['binding_id']for h in headers)):
        raise ValueError('Simulator snapshot model acknowledgement differs from its bound request')
    row=rows[0]
    from .particle_control_provenance import confirmed_points
    expected=dict(control_point_indices=[0,1,2,5],declared_control_point_inputs=[0,1,2,5],
        authored_control_point_indices=[3],authored_root_position_indices=[],unprovided_control_point_indices=[],
        materialized_control_point_indices=[0,1,2,3,5])
    if (any(not confirmed_points(row,k,value) for k,value in expected.items())
            or row.get('control_point_input_policy')!='agr_authored_child_controls_v2'
            or row.get('native_static_controls') is not None):
        raise ValueError('Crossbow snapshot response lacks the exact external/authored control-point provenance')
