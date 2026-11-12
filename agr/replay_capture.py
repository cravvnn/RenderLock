"""Shared bounded file/provenance checks for internal replay sidecars.

Each caller supplies its fixed filename, policy and strict payload validator.
These synchronous checks do not merge state or establish effect coverage.
"""
import hashlib
import json
import os
from pathlib import Path

def file_sha256(path):
    with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,ensure_ascii=True).encode()).hexdigest()

def _keys(value,names,label,check):
    check(isinstance(value,dict)and set(value)==set(names),label+' fields differ')

def retain_contract(output,advertisement,start,end,demo_sha,helper_sha,*,filename,policy,summary_keys,validator,max_json,check):
    path=Path(output)/filename
    provenance={'demo_sha256':demo_sha,'helper_sha256':helper_sha,'start_tick':start,
        'end_tick':end,'tick_range_inclusive':True,'sidecar_filename':filename,'sidecar_sha256':None,'capture_sha256':None}
    if advertisement is None:
        check(not os.path.lexists(path),'unadvertised sidecar exists')
        return {'availability':'unavailable','reason':'helper_not_advertised','capture':None,'provenance':provenance}
    _keys(advertisement,('schema_version','policy','file','availability','reason',*summary_keys),'advertisement',check)
    check(type(advertisement['schema_version'])is int and advertisement['schema_version']==1
        and advertisement['policy']==policy and advertisement['file']==filename,'advertised contract differs')
    check(path.is_file()and not path.is_symlink(),'advertised sidecar is missing or not a regular file')
    with path.open('rb')as stream:raw=stream.read(max_json+1)
    check(len(raw)<=max_json,'serialized sidecar budget exceeded')
    def unique(pairs):
        value={}
        for key,item in pairs:
            check(key not in value,'duplicate JSON key');value[key]=item
        return value
    try:value=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda x:check(False,'nonfinite JSON value'))
    except (UnicodeError,RecursionError,json.JSONDecodeError)as exc:
        check(False,'invalid bounded JSON: '+type(exc).__name__)
    validator(value,start,end)
    c=value['coverage']
    check(all(type(advertisement[k])is type(c[k])and advertisement[k]==c[k]
        for k in ('availability','reason',*summary_keys)),'advertisement disagrees with sidecar')
    provenance['sidecar_sha256']=hashlib.sha256(raw).hexdigest()
    provenance['capture_sha256']=canonical_sha256(value)
    return {'availability':c['availability'],'reason':c['reason'],'capture':value,'provenance':provenance}

def validate_contract_envelope(value,*,demo_sha256,start_tick,end_tick,filename,validator,check):
    _keys(value,('availability','reason','capture','provenance'),'retained envelope',check)
    p=value['provenance']
    _keys(p,('demo_sha256','helper_sha256','start_tick','end_tick','tick_range_inclusive',
        'sidecar_filename','sidecar_sha256','capture_sha256'),'retained provenance',check)
    def digest(v):return isinstance(v,str)and len(v)==64 and all(x in '0123456789abcdef'for x in v)
    check(digest(p['demo_sha256'])and p['demo_sha256']==demo_sha256 and digest(p['helper_sha256']),
        'retained replay/helper identity differs')
    check(type(p['start_tick'])is int and type(p['end_tick'])is int and 0<=p['start_tick']<=p['end_tick']<2**31-1
        and p['start_tick']==start_tick and p['end_tick']==end_tick and p['tick_range_inclusive']is True
        and p['sidecar_filename']==filename,'retained inclusive range differs')
    capture=value['capture']
    if capture is None:
        check(value['availability']=='unavailable'and value['reason']=='helper_not_advertised'
            and p['sidecar_sha256']is None and p['capture_sha256']is None,'missing capture became qualified')
        return value
    validator(capture,start_tick,end_tick)
    check(value['availability']==capture['coverage']['availability']and value['reason']==capture['coverage']['reason'],
        'retained coverage differs')
    check(digest(p['sidecar_sha256'])and digest(p['capture_sha256'])
        and p['capture_sha256']==canonical_sha256(capture),'retained payload digest differs; regenerate extraction')
    return value


def validate_contract_view(value,*,demo_sha256,start_tick,end_tick,filename,validator,check):
    """Validate a contained shot against the complete original capture unchanged.

    This pure check neither crops history nor rewrites provenance. The caller
    must still select the rows needed for its shot and qualify their semantics.
    """
    _keys(value,('availability','reason','capture','provenance'),'retained envelope',check)
    p=value['provenance']
    check(isinstance(p,dict),'retained provenance fields differ')
    validate_contract_envelope(value,demo_sha256=demo_sha256,
        start_tick=p.get('start_tick'),end_tick=p.get('end_tick'),
        filename=filename,validator=validator,check=check)
    check(type(start_tick)is int and type(end_tick)is int
        and p['start_tick']<=start_tick<=end_tick<=p['end_tick'],
        'requested view is outside original capture range')
    return value
