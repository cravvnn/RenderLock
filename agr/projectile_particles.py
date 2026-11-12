"""Exact owner/lifetime/pose inputs for bounded entity-driven projectile effects.

These are not particle-manager CREATE messages. Native creation attaches the
effect to the projectile's absolute origin. Only recorded in-flight samples are
rendered; client interpolation, visibility and post-deletion tails are separate.
"""
import hashlib
import json
import math
import re
from .particle_control_provenance import confirmed_materialized_points

from .network_projectile_capture import POLICY,validate_envelope
CLASS='CCitadel_Projectile_BatSwarmProjectile'
RESOURCE='particles/abilities/vampirebat/vampirebat_batswarm_tracer.vpcf'
CLIENT_SHA256='802d2b73124af86936875b0ccdf4862d95281490faff5ff0b03deea02cf22b45'
PREVIOUS_CLIENT_SHA256='2007fddaf9f48211e1ed188aff3c31ad47ba667cbaace16f5dd30e9376b884c7'
CURRENT_CLIENT_SHA256='d0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5'
# Independently executed RTTI-owned creation consumers qualify each module.
# This does not qualify interpolation, visibility or post-deletion effects.
QUALIFIED_CLIENT_SHA256S=frozenset((CLIENT_SHA256,PREVIOUS_CLIENT_SHA256,CURRENT_CLIENT_SHA256))
PREFIX='CBodyComponent.m_skeletonInstance.'
SCOPE='Recorded in-flight projectile only; client interpolation, orientation overrides, visibility and post-deletion endcap are not reconstructed.'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def identity(handle):
    if type(handle) is not int or not 0<=handle<0xffffffff or handle==0xffffff:
        raise ValueError('Projectile owner requires an exact full entity handle')
    return f'{handle&0x3fff}:{handle>>14}'


def vector(value):
    if not isinstance(value,(list,tuple)) or len(value)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):
        raise ValueError('Projectile requires finite recorded vectors')
    return [float(x) for x in value]


def controls(track,data):
    """Reject missing history, reused identities, changed owners and stale poses."""
    from .particle_events import particle_resource_lookup
    if (track.get('class')!=CLASS or not isinstance(track.get('id'),str)
            or not re.fullmatch(r'(0|[1-9][0-9]*):(0|[1-9][0-9]*)',track['id'])):
        raise ValueError('Unqualified projectile class or full identity')
    entry,serial=map(int,track['id'].split(':'))
    if not 0<=entry<0x4000 or not 0<=serial<0x40000:raise ValueError('Projectile handle bounds invalid')
    rows=track.get('samples');birth=track.get('first_seen_tick')
    if (type(birth) is not int or birth<0 or not isinstance(rows,list) or not 1<=len(rows)<=4096
            or any(not isinstance(r,dict)or type(r.get('tick')) is not int for r in rows)
            or [r.get('tick') for r in rows]!=list(range(birth,birth+len(rows)))):
        raise ValueError('Projectile requires contiguous history from observed creation')
    end=rows[-1]['tick'];removed=track.get('removed_tick')
    if removed is not None and (type(removed) is not int or removed!=end+1):
        raise ValueError('Projectile deletion differs from its last recorded sample')
    # An enclosed saved-shot view needs the entire earlier prefix, but later
    # owner/pose changes outside this shot are not inputs to its simulation.
    rows=[row for row in rows if row['tick']<=data['end_tick']]
    if not rows:raise ValueError('Projectile flight does not overlap output range')
    expected=next(iter(particle_resource_lookup([RESOURCE])))
    owners={str(e['id']) for e in data['entities']};owner=None;result=[]
    for row in rows:
        f=row.get('fields',{})
        if row.get('active') is not True:raise ValueError('Dormant projectile rendering is unqualified')
        current=identity(f.get('m_hOwnerEntity'))
        if current not in owners or owner is not None and owner!=current:
            raise ValueError('Projectile owner is missing, changed or not exported')
        owner=current
        if type(f.get('m_hParticleDef'))is not int or not 0<=f['m_hParticleDef']<2**64 or str(f['m_hParticleDef'])!=expected:
            raise ValueError('Projectile particle resource differs from the qualified tracer')
        if (type(f.get(PREFIX+'m_hParent'))is not int or f[PREFIX+'m_hParent'] not in (0xffffff,0xffffffff)
                or type(f.get(PREFIX+'m_flScale'))not in (int,float)or f[PREFIX+'m_flScale']!=1
                or type(f.get('m_fEffects'))is not int or f['m_fEffects']!=0
                or type(f.get('m_MoveType'))is not int or f['m_MoveType']!=11
                or type(f.get('m_flTimeScale'))not in (int,float)or f['m_flTimeScale']!=1
                or f.get('m_bNoInterpolate') is not False
                or type(f.get(PREFIX+'m_vecOrigin.m_nOutsideWorld'))is not int or f[PREFIX+'m_vecOrigin.m_nOutsideWorld']!=0):
            raise ValueError('Unqualified projectile parent, scale, visibility or motion mode')
        position=[]
        for axis in 'XYZ':
            cell=f.get(PREFIX+'m_vecOrigin.m_cell'+axis);offset=f.get(PREFIX+'m_vecOrigin.m_vec'+axis)
            if type(cell) is not int or not 0<=cell<128 or type(offset) not in (int,float) or not math.isfinite(offset):
                raise ValueError('Incomplete projectile cell/offset position')
            position.append(cell*512.-32768.+offset)
        pitch,yaw,roll=[math.radians(x)/2 for x in vector(f.get(PREFIX+'m_angRotation'))]
        sy,cy=math.sin(yaw),math.cos(yaw);sp,cp=math.sin(pitch),math.cos(pitch);sr,cr=math.sin(roll),math.cos(roll)
        rotation=[sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy]
        vector(f.get('m_vInitialPosition'));vector(f.get('m_vInitialVelocity'))
        result.append(dict(tick=row['tick'],position=position,rotation=rotation))
    return owner,result


def validate_capture(capture,data):
    validate_envelope(capture,data)
    if capture['availability']!='available':
        raise ValueError('Projectile capture unavailable: '+str(capture['reason']))
    coordinate=data.get('metadata',{}).get('coordinate_system',{})
    if (data.get('metadata',{}).get('source_world_half')!=32768 or coordinate.get('unit_scale')!=.0254
            or coordinate.get('origin')!=[0,0,0] or coordinate.get('world_yaw_degrees')!=0):
        raise ValueError('Projectile capture requires the declared Source world coordinate basis')
    return capture['capture']['tracks']


def selected_tracks(capture,data):
    """Select recorded overlap/owners without rewriting the retained payload."""
    owners={str(e['id'])for e in data['entities']};result=[]
    for track in validate_capture(capture,data):
        rows=track['samples']
        if rows[-1]['tick']<data['start_tick'] or track['first_seen_tick']>data['end_tick']:continue
        recorded=set();unknown=False
        for row in rows:
            if row['tick']>data['end_tick']:continue
            try:recorded.add(identity(row['fields'].get('m_hOwnerEntity')))
            except ValueError:unknown=True
        if unknown or recorded&owners:result.append(track)
    return result


def request(track,data,gameinfo,seed):
    if type(seed) is not int or not 0<=seed<=0xffffffff or type(data['tick_rate']) is not int or not 0<data['tick_rate']<=1024:
        raise ValueError('Projectile request requires a bounded tick rate and seed')
    owner,points=controls(track,data)
    points=[point for point in points if point['tick']<=data['end_tick']]
    if not points or points[-1]['tick']<data['start_tick']:raise ValueError('Projectile flight does not overlap output range')
    return dict(gameinfo=gameinfo,start_tick=data['start_tick'],end_tick=points[-1]['tick'],tick_rate=data['tick_rate'],seed=seed,
        instances=[dict(id='projectile:'+track['id'],resource=RESOURCE,created_tick=track['first_seen_tick'],control_points=points)]),owner


def validate_package(package,data):
    from .simulated_effect_package import scene_identity
    if type(package.get('schema_version'))is not int or package['schema_version']!=1 or package.get('backend')!='simulated_projectile_flight_v2':
        raise ValueError('Unsupported projectile package')
    provider=package.get('projectile_provider',{})
    if provider.get('client_sha256')not in QUALIFIED_CLIENT_SHA256S:raise ValueError('Unqualified projectile creation provider')
    capture=data.get('network_projectiles');tracks=selected_tracks(capture,data)
    if (provider.get('capture_sha256')!=capture['provenance']['capture_sha256']
            or provider.get('capture_provenance_sha256')!=digest(capture['provenance'])):
        raise ValueError('Projectile package capture provenance differs')
    found=[t for t in tracks if t['id']==provider.get('entity_id')]
    if len(found)!=1 or digest(found[0])!=provider.get('track_sha256'):
        raise ValueError('Projectile package track identity differs')
    plan=package['projectile_request'];expected,owner=request(found[0],data,plan['gameinfo'],plan['seed'])
    if plan!=expected or package.get('entity_ids')!=[owner] or package.get('scene_identity')!=scene_identity(data,[owner]):
        raise ValueError('Projectile package differs from replay, owner or flight samples')
    sim=package['simulation'];sha=digest(plan)
    if (type(sim.get('schema_version'))is not int or sim['schema_version']!=1
            or not isinstance(sim.get('backend'),str)or not sim['backend'] or sim.get('warnings')!=[]
            or sim.get('request_sha256')!=sha or type(sim.get('seed'))is not int or sim['seed']!=plan['seed']
            or sim.get('seed_policy')!='agr_substitute_sha256_v1'
            or any(type(sim.get(k))is not int or sim[k]!=plan[k] for k in ('start_tick','end_tick','tick_rate'))
            or package.get('simulation_request_sha256')!=sha):
        raise ValueError('Projectile simulator request or response identity differs')
    instances=sim.get('instances',[])
    if not isinstance(instances,list)or len(instances)!=1 or not isinstance(instances[0],dict):
        raise ValueError('Projectile package requires one exact instance')
    instance=instances[0];expected_instance=plan['instances'][0]
    frames=instance.get('frames')
    if (instance.get('id')!=expected_instance['id'] or instance.get('resource')!=RESOURCE
            or type(instance.get('birth'))is not int or instance['birth']!=expected_instance['created_tick'] or instance.get('entity_id')!=owner
            or not confirmed_materialized_points(instance,[0])
            or type(instance.get('model_bone_count'))is not int or instance['model_bone_count']!=0 or instance.get('literal_transforms_applied')!=[]
            or instance.get('destroy_applied') is not None or instance.get('release_ticks')!=[]
            or not isinstance(frames,list)or any(not isinstance(f,dict)or type(f.get('tick'))is not int for f in frames)
            or [f['tick'] for f in frames]!=list(range(max(plan['start_tick'],instance['birth']),plan['end_tick']+1))):
        raise ValueError('Incomplete projectile states or unqualified control/lifecycle inputs')
    if package.get('coverage_scope')!=SCOPE:
        raise ValueError('Projectile package must retain its in-flight coverage limit')
