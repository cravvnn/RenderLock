"""Retain complete network state-word arrays, not interpreted owner visibility."""
from .client_modifier_writes import integer
from .replay_capture import retain_contract,validate_contract_envelope,validate_contract_view

FILE='client_effect_owner_states.json'
POLICY='agr_client_effect_owner_states_v1'
MAX_ROWS=200000
MAX_WORDS=8000000
MAX_ARRAY=128
MAX_JSON=128*1024*1024
ARRAYS=('enabled_state_words','predicted_state_words','disabled_state_words')
REASONS={'tick_out_of_range','tick_order','owner_identity','duplicate_owner_tick',
    'array_bounds','row_budget','word_budget','json_budget','end_tick_not_observed'}

def require(condition,message):
    if not condition:raise ValueError('Client effect owner states: '+message)

def keys(value,names,label):
    require(isinstance(value,dict)and set(value)==set(names),label+' fields differ')

def validate_capture(value,start,end):
    keys(value,('schema_version','policy','requested_range','coverage','rows'),'capture')
    require(type(value['schema_version'])is int and value['schema_version']==1 and value['policy']==POLICY,'schema/policy differs')
    r=value['requested_range'];keys(r,('start_tick','end_tick','inclusive'),'range')
    require(integer(r['start_tick'],0,2**31-2)and integer(r['end_tick'],r['start_tick'],2**31-2)
        and r['start_tick']==start and r['end_tick']==end and r['inclusive']is True,'inclusive requested range differs')
    c=value['coverage'];keys(c,('availability','reason','origin','first_observed_tick','last_observed_tick',
        'rows','words','incomplete_rows','scope'),'coverage')
    require(c['availability']in ('available','unavailable')and c['origin']=='signon','availability/origin differs')
    require(c['reason']is None if c['availability']=='available'else isinstance(c['reason'],str)and c['reason']in REASONS,'coverage reason differs')
    first,last=c['first_observed_tick'],c['last_observed_tick']
    require((first is None and last is None)or(integer(first,-1,end)and integer(last,first,end)),'observation range differs')
    if c['availability']=='available':require(last==end,'available capture did not reach inclusive end')
    require(isinstance(c['scope'],str)and len(c['scope'])<=512,'coverage scope exceeds bound')
    rows=value['rows'];require(isinstance(rows,list)and len(rows)<=MAX_ROWS,'row budget exceeded')
    require(integer(c['rows'],0,MAX_ROWS)and c['rows']==len(rows),'row count differs')
    require(integer(c['words'],0,MAX_WORDS)and integer(c['incomplete_rows'],0,len(rows)),'word/incomplete count differs')
    previous=start;seen=set();total=0;incomplete=0
    for row in rows:
        keys(row,('tick','entity_id','entity_index','entity_serial','hero_id','active','life_state',*ARRAYS),'owner row')
        require(integer(row['tick'],previous,end)and first is not None and first<=row['tick']<=last,'owner tick order/range differs')
        if row['tick']!=previous:seen.clear()
        previous=row['tick']
        require(integer(row['entity_index'],0,0x3fff)and integer(row['entity_serial'],0,0x3ffff)
            and row['entity_id']==f"{row['entity_index']}:{row['entity_serial']}"
            and integer(row['hero_id'],1,2**63-1),'owner identity differs')
        require(row['entity_id']not in seen,'duplicate owner/tick');seen.add(row['entity_id'])
        require(type(row['active'])is bool and(row['life_state']is None or integer(row['life_state'],0,255)),'owner active/life fields differ')
        missing=row['life_state']is None
        for field in ARRAYS:
            words=row[field]
            if words is None:missing=True;continue
            require(isinstance(words,list)and 1<=len(words)<=MAX_ARRAY,'owner word array bound differs')
            total+=len(words);require(total<=MAX_WORDS,'owner word budget exceeded')
            require(all(integer(v,0,2**32-1)for v in words),'owner array contains non-uint32 value')
        incomplete+=int(missing)
    require(total==c['words']and incomplete==c['incomplete_rows'],'owner word/incomplete count disagrees')
    return value

def retain_capture(output,advertisement,start,end,demo_sha,helper_sha):
    return retain_contract(output,advertisement,start,end,demo_sha,helper_sha,
        filename=FILE,policy=POLICY,summary_keys=('rows','words','incomplete_rows'),
        validator=validate_capture,max_json=MAX_JSON,check=require)

def validate_envelope(value,*,demo_sha256,start_tick,end_tick):
    return validate_contract_envelope(value,demo_sha256=demo_sha256,start_tick=start_tick,end_tick=end_tick,
        filename=FILE,validator=validate_capture,check=require)


def validate_view(value,*,demo_sha256,start_tick,end_tick):
    """Validate an enclosed shot without rewriting captured arrays/provenance."""
    return validate_contract_view(value,demo_sha256=demo_sha256,start_tick=start_tick,end_tick=end_tick,
        filename=FILE,validator=validate_capture,check=require)
