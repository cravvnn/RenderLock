"""Bounded internal projectile sidecar retention; no rendering/lifetime inference.

The native ProjectileCapture model covers capture/publication. These synchronous
validators keep raw observations and exact extraction provenance. An available
capture can contain an unusable track: its missing prefix/changed owner must be
refused by the separate effect provider, not silently repaired here.
"""
import math
import re
from .replay_capture import retain_contract, validate_contract_envelope

FILE='network_projectiles.json'
POLICY='agr_network_projectile_fields_v2'
CLASS='CCitadel_Projectile_BatSwarmProjectile'
MAX_JSON=256*1024*1024
MAX_TRACKS=4096
MAX_TRACK_ROWS=4096
MAX_ROWS=500000


def require(condition,message):
    if not condition:raise ValueError('Network projectile capture: '+message)


def integer(value,low,high):
    return type(value)is int and low<=value<=high


def keys(value,names,label):
    require(isinstance(value,dict)and set(value)==set(names),label+' fields differ')


def _field(value):
    if value is None or type(value)is bool:return True
    if type(value)is int:return -(2**63)<=value<2**64
    if type(value)is float:return math.isfinite(value)
    if isinstance(value,str):return len(value)<=256
    return isinstance(value,list)and len(value)<=4 and all(
        x is None or type(x)is bool or integer(x,-2**63,2**64-1)
        or type(x)is float and math.isfinite(x)for x in value)


def validate_capture(value,start,end):
    keys(value,('schema_version','policy','requested_range','coverage','tracks'),'capture')
    require(type(value['schema_version'])is int and value['schema_version']==1 and value['policy']==POLICY,'schema/policy differs')
    r=value['requested_range'];keys(r,('start_tick','end_tick','inclusive'),'range')
    require(integer(r['start_tick'],0,2**31-2)and integer(r['end_tick'],r['start_tick'],2**31-2)
        and r['start_tick']==start and r['end_tick']==end and r['inclusive']is True,'inclusive requested range differs')
    c=value['coverage'];keys(c,('availability','reason','origin','class','first_observed_tick',
        'last_observed_tick','tracks','samples','scope'),'coverage')
    require(c['origin']=='signon'and c['class']==CLASS,'origin/class differs')
    require(isinstance(c['scope'],str)and len(c['scope'])<=512,'scope exceeds bound')
    first,last=c['first_observed_tick'],c['last_observed_tick']
    require((first is None and last is None)or(integer(first,-1,end)and integer(last,first,end)),'scan range differs')
    require(c['availability']in ('available','unavailable'),'unknown availability')
    failed=c['reason']in ('capture_failed','serialized_budget_exceeded')
    require((c['reason']is None and last==end)if c['availability']=='available'
        else c['reason']=='end_tick_not_observed' or failed,'coverage reason/end differs')
    if failed:
        require(c['availability']=='unavailable'and c['tracks']==0 and c['samples']==0
            and value['tracks']==[],'failed capture retained a partial prefix')
    tracks=value['tracks'];require(isinstance(tracks,list)and len(tracks)<=MAX_TRACKS,'track budget exceeded')
    require(integer(c['tracks'],0,MAX_TRACKS)and c['tracks']==len(tracks),'track count differs')
    require(integer(c['samples'],0,MAX_ROWS),'sample count exceeds bound')
    seen=set();total=0
    for track in tracks:
        keys(track,('id','class','first_seen_tick','removed_tick','samples'),'track')
        name=track['id']
        require(isinstance(name,str)and len(name)<=20 and re.fullmatch(r'(0|[1-9][0-9]*):(0|[1-9][0-9]*)',name),'full identity malformed')
        entry,serial=map(int,name.split(':'))
        require(entry<0x4000 and serial<0x40000 and name not in seen,'identity duplicate/out of bounds');seen.add(name)
        require(track['class']==CLASS,'track class differs')
        birth=track['first_seen_tick'];removed=track['removed_tick'];rows=track['samples']
        require(integer(birth,0,end)and first is not None and first<=birth<=last,'first observation differs')
        require(isinstance(rows,list)and 1<=len(rows)<=MAX_TRACK_ROWS,'per-track row budget exceeded')
        total+=len(rows);require(total<=MAX_ROWS,'total row budget exceeded')
        previous=None
        for row in rows:
            keys(row,('tick','active','fields'),'sample');tick=row['tick']
            require(integer(tick,birth,last)and(previous is None or tick==previous+1),'sample history gap/order differs');previous=tick
            require(type(row['active'])is bool,'active flag differs')
            fields=row['fields'];require(isinstance(fields,dict)and len(fields)<=64,'field count exceeds bound')
            require(all(isinstance(k,str)and len(k)<=128 and _field(v)for k,v in fields.items()),'unbounded or nonfinite field value')
        require(removed is None or integer(removed,previous+1,end),'absence observation differs')
        if c['availability']=='available'and removed is None:
            require(previous==end,'live track stopped before the scan end')
    require(c['samples']==total,'total sample count differs')
    return value


def retain_capture(output,advertisement,start,end,demo_sha,helper_sha):
    return retain_contract(output,advertisement,start,end,demo_sha,helper_sha,
        filename=FILE,policy=POLICY,summary_keys=('tracks','samples'),validator=validate_capture,
        max_json=MAX_JSON,check=require)


def validate_envelope(value,data):
    # A saved extraction may be cropped for a later shot. Keep the original
    # payload/digests/history intact, and admit only an enclosed inclusive view.
    require(isinstance(value,dict)and isinstance(value.get('provenance'),dict),'missing retained provenance')
    p=value['provenance'];start=p.get('start_tick');end=p.get('end_tick')
    require(integer(start,0,2**31-2)and integer(end,start,2**31-2)
        and integer(data.get('start_tick'),start,end)
        and integer(data.get('end_tick'),data['start_tick'],end),'scene view exceeds captured inclusive range')
    return validate_contract_envelope(value,demo_sha256=data.get('metadata',{}).get('demo_sha256'),
        start_tick=start,end_tick=end,filename=FILE,
        validator=validate_capture,check=require)
