"""Silence-only immutable creation, body-selection and hitbox admission.

No game reader, pose acquisition, simulator, cache or writer lives here. The
retained native profiles stay distinct; a valid birth never implies hitboxes.
"""
import copy
import hashlib
import json

from .particle_silence_capture import RESOURCE as SILENCE, validate_silence_capture, request_header, validate_response
from .particle_client_capture import validate_client_child_capture
from .particle_hitbox_transport import POLICY as HITBOX_POLICY, prepare_hitbox_input
from .particle_control_events import compile_control_events
from .particle_snapshot_provider import digest

PROFILE='observed_silence_cp0_hitboxes_v1'
DEFINITIONS={
    SILENCE:'29e2a4d7c87a358176ea31b3fe9c8160f05a78e162b2098e5b840e798090da2b',
    'particles/modifiers/silenced_debuff_body.vpcf':'cc2bd41c142280ea6397fff50d027df30d4b60df9170ec993df5f6ca80ddbebf',
    'particles/modifiers/silenced_debuff_start.vpcf':'37fddf2a5369619a2578aa7844334513cdd8b6e0fc4dd65d2dbf4363288a2160',
    'particles/modifiers/silenced_debuff_swirls.vpcf':'42bcce9cd7debb3cbebba2c6ad8a781789f6aedefc46b1d42c87a0a7844e54af',
}


def require(ok,message):
    if not ok:raise ValueError('Silence inputs: '+message)


def validate_definitions(definitions,resource=SILENCE):
    require(resource==SILENCE and isinstance(definitions,dict) and set(definitions)==set(DEFINITIONS),
            'complete four-system definition tree required')
    require(all(digest(value)==DEFINITIONS[name] for name,value in definitions.items()), 'definition changed')


def scene_point_bindings(instance,start,end,entity_ids,*,client_child_capture=None,
        silence_birth_capture=None,birth_override_capture=None,custom_origin_capture=None,scene=None):
    require(birth_override_capture is None and custom_origin_capture is None,
            'Silence captures cannot be combined with other creation profiles')
    require(isinstance(scene,dict) and instance.get('resource')==SILENCE, 'exact source scene/resource required')
    validate_silence_capture(silence_birth_capture,scene,instance)
    validate_client_child_capture(client_child_capture,scene,instance)
    require(type(start)is int and type(end)is int and start<=instance['created_tick']<=end,
            'birth must be inside available input history')
    require(instance['entity_id'] in entity_ids, 'owner must be explicitly exported')
    controls=compile_control_events(instance,start,end)
    from .recorded_effect_simulation import zero_binding_offsets
    rows={}
    for event in controls['entity_binding_events']:
        b=event.get('data',{}).get('update_particle_ent')
        require(isinstance(b,dict) and event['tick']==instance['created_tick'], 'birth-time entity bindings required')
        cp=b.get('control_point')
        require(type(cp)is int and cp in (0,1) and cp not in rows
            and set(b)<={'control_point','entity_handle','attach_type','attachment','include_wearables',
                         'offset_position','offset_angles','fallback_position'}, 'entity binding coverage differs')
        require(type(b.get('entity_handle'))is int and b['entity_handle']==instance['entity_handle']
            and type(b.get('attach_type'))is int and type(b.get('attachment'))is int
            and type(b.get('include_wearables',False))is bool, 'exact owner/typed attachment required')
        provider='root_origin' if cp==0 else 'point_follow'
        require((b['attach_type'],b['attachment'])==(1,0) if cp==0 else b['attach_type']==5 and b['attachment']>0,
                'CP0 owner origin and CP1 named attachment required')
        zero_binding_offsets(b)
        # The separate birth validator owns finite CP0 fallback semantics;
        # CP1 stays the original explicit attachment, with no selected-child guess.
        fallback=b.get('fallback_position',{})
        import math
        require(isinstance(fallback,dict) and set(fallback)<=set('xyz')
            and all(type(v)in(int,float) and math.isfinite(v) for v in fallback.values()), 'invalid fallback')
        rows[cp]=dict(b,provider=provider,target_entity_id=instance['entity_id'])
    require(set(rows)=={0,1}, 'both recorded owner/attachment inputs required')
    for update in controls['literal_transforms']:
        cp=update['control_point']
        require(type(cp)is int and cp==2, 'only original literal CP2 is admitted')
        if cp not in rows:
            require(update['tick']==instance['created_tick'], 'literal CP2 must start at birth')
            rows[cp]=dict(control_point=cp,provider='literal_transform',updates=[])
        rows[cp]['updates'].append(copy.deepcopy(update))
    require(set(rows)=={0,1,2}, 'complete recorded CP0/1/2 inputs required')
    return [rows[i] for i in sorted(rows)]


def model_controls(instance,bindings,capture,scene,*,silence_birth_capture=None):
    validate_silence_capture(silence_birth_capture,scene,instance)
    validate_client_child_capture(capture,scene,instance)
    require(instance.get('resource')==SILENCE and [b.get('control_point') for b in bindings]==[0,1,2]
        and [b.get('provider') for b in bindings]==['root_origin','point_follow','literal_transform']
        and all(b.get('target_entity_id')==instance['entity_id'] for b in bindings[:2]),
        'separate owner CP0, parent-body CP1 and literal CP2 required')
    return [0]


def validate_hitbox_response(simulation,instance_id,headers):
    rows=simulation.get('instances')
    require(isinstance(rows,list) and len(rows)==1 and rows[0].get('id')==instance_id
        and rows[0].get('model_hitbox_input_policy')==HITBOX_POLICY
        and digest(rows[0].get('model_hitbox_bindings'))==digest(headers), 'hitbox input acknowledgement differs')


def validate_hitbox_provider(provider,scene,row):
    from .simulated_effect_package import model_constraints_identity
    require(isinstance(provider,dict) and provider.get('profile')==PROFILE and provider.get('policy')==HITBOX_POLICY
        and provider.get('definition_sha256')==DEFINITIONS, 'unqualified hitbox provider')
    entities=[e for e in scene['entities'] if e.get('id')==provider.get('entity_id')]
    require(len(entities)==1 and row.get('entity_id')==provider['entity_id'], 'hitbox owner differs')
    model=entities[0].get('model_constraints')or{}
    require(provider.get('model_constraints_sha256')==model_constraints_identity(model)
        and provider.get('resource')==model.get('resource') and provider.get('resource_sha256')==model.get('resource_sha256')
        and type(provider.get('start_tick'))is int and provider['start_tick']==row.get('birth')
        and type(provider.get('end_tick'))is int and provider['end_tick']==scene['end_tick'], 'hitbox model/range differs')
    names=model.get('names')
    require(isinstance(names,list) and 0<len(names)<=32767 and all(isinstance(n,str) and n for n in names)
        and len(names)==len(set(names)), 'hitbox bone order unavailable')
    bone_hash=hashlib.sha256(json.dumps(names,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    h=dict(control_point=0,entity_id=provider['entity_id'],model=model['resource'],
        model_sha256=model['resource_sha256'],bone_order_sha256=bone_hash)
    h['binding_id']=hashlib.sha256('\n'.join(str(h[k]) for k in ('control_point','entity_id','model','model_sha256','bone_order_sha256')).encode()).hexdigest()
    require(digest(provider.get('bindings'))==digest([h]), 'hitbox headers changed CP, owner, model or bone order')
    validate_hitbox_response(dict(instances=[row]),row['id'],[h])


def prepare_observed_inputs(scene,instance,birth,child):
    """Preserve both original envelopes; return all required observations or none."""
    from .particle_history import history_start
    bindings=scene_point_bindings(instance,history_start(scene),scene['end_tick'],
        [e['id'] for e in scene['entities']],scene=scene,client_child_capture=child,silence_birth_capture=birth)
    controls=model_controls(instance,bindings,child,scene,silence_birth_capture=birth)
    value=dict(policy='agr_observed_silence_inputs_v1',instance_id=instance['id'],
        birth=copy.deepcopy(birth),child=copy.deepcopy(child),bindings=bindings,model_controls=controls,
        birth_header=request_header(birth,scene,instance),
        scope='Independent legacy birth/body-selection inputs only; no pose acquisition, hitbox frames or current native-reader qualification')
    return dict(value,content_sha256=digest(value))


def prepare_hitbox_frames(inventory,poses,*,entity_id,start_tick,end_tick):
    """Bounded CP0 frame additions, not a complete control-point/simulation plan."""
    require(type(start_tick)is int and type(end_tick)is int and 0<=start_tick<=end_tick
        and end_tick-start_tick<10000 and isinstance(poses,list)
        and len(poses)==end_tick-start_tick+1, 'complete bounded hitbox pose history required')
    sets=inventory.get('hitbox_sets') if isinstance(inventory,dict)else None
    require(isinstance(sets,list) and 0<len(sets)<=128
        and all(isinstance(s,dict) and isinstance(s.get('boxes'),list) and 0<len(s['boxes'])<=4096 for s in sets),
            'authored hitbox sets unavailable')
    require(sum(len(s['boxes']) for s in sets)*len(poses)<=200000, 'hitbox record budget exceeded')
    frames=[];expected=None
    for tick,pose in zip(range(start_tick,end_tick+1),poses):
        header,frame=prepare_hitbox_input(inventory,pose,entity_id=entity_id,tick=tick,control_point=0)
        if expected is None:expected=header
        require(digest(header)==digest(expected), 'hitbox frame identity changed')
        frames.append(dict(tick=tick,model_hitboxes=[frame]))
    return dict(policy=HITBOX_POLICY,bindings=[expected],frames=frames)


def validate_package(package,scene):
    """Protect the prepared-package boundary; this does not enable a live reader."""
    sim=package.get('simulation',{});rows=sim.get('instances')
    require(isinstance(rows,list) and len(rows)==1 and rows[0].get('resource')==SILENCE,
            'exactly one Silence simulation required')
    row=rows[0];matches=[i for i in scene.get('particle_instances',[]) if i.get('id')==row.get('id')]
    require(not any(k in package for k in ('snapshot_model_provider','crossbow_owner_capture',
                'birth_override_capture','custom_origin_capture'))
        and all(row.get(k)is None for k in ('snapshot_model_input_policy','snapshot_model_bindings',
                'crossbow_owner_provider','custom_origin_provider','birth_override_provider')),
            'other creation/snapshot profiles cannot be combined')
    require(len(matches)==1 and matches[0].get('resource')==SILENCE and row.get('birth')==matches[0].get('created_tick')
        and row.get('entity_id')==matches[0].get('entity_id'), 'recorded identity/resource/birth differs')
    instance=matches[0]
    require(package.get('entity_ids')==[instance['entity_id']], 'exact selected owner required')
    observed=prepare_observed_inputs(scene,instance,package.get('silence_birth_capture'),package.get('client_child_capture'))
    validate_definitions(package.get('assets',{}).get('definitions'))
    validate_hitbox_provider(package.get('hitbox_provider'),scene,row)
    validate_response(sim,instance['id'],observed['birth_header'])
