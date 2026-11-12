"""Validate internal raw modifier writes, without inferring active effects.

Native ClientModifierWrites.tla models capture/publication. This module performs
bounded synchronous validation on immutable bytes, and keeps their provenance.
It neither merges table fields nor decides particle births, phases or deaths.
"""
from .replay_capture import file_sha256,canonical_sha256,retain_contract,validate_contract_envelope

FILE='client_modifier_writes.json'
POLICY='agr_client_modifier_writes_v1'
MAX_JSON=128*1024*1024
MAX_ROWS=65536
MAX_PAYLOAD=16384
MAX_TOTAL=16*1024*1024
FIELDS={1:'entry_type',2:'parent',3:'serial_number',4:'modifier_subclass',25:'int1'}
REASONS={'invalid_payload','payload_budget','row_budget','payload_too_large','slot_out_of_bounds',
 'unknown_change_kind','tick_order','tick_out_of_range','end_tick_not_observed','table_not_observed'}

def require(condition,message):
    if not condition:raise ValueError('Client modifier writes: '+message)

def integer(value,low,high):
    return type(value)is int and low<=value<=high

def _varint(data,index):
    value=0
    for shift in range(0,70,7):
        require(index<len(data),'truncated protobuf varint')
        byte=data[index];index+=1
        require(shift<63 or byte<=1,'protobuf varint exceeds uint64')
        value|=(byte&127)<<shift
        if byte<128:return value,index
    raise ValueError('Client modifier writes: unterminated protobuf varint')

def wire_header(data):
    """Read only five proto2 scalar fields; skip all other wire fields faithfully."""
    header=dict.fromkeys(FIELDS.values());index=0;groups=[]
    while index<len(data):
        tag,index=_varint(data,index);field,wire=tag>>3,tag&7
        require(1<=field<2**29,'invalid protobuf field number')
        if wire==0:
            value,index=_varint(data,index)
            if not groups and field in FIELDS:
                value&=0xffffffff
                if field in (1,25)and value>=2**31:value-=2**32
                header[FIELDS[field]]=value
        elif wire in (1,5):index+=8 if wire==1 else 4
        elif wire==2:
            size,index=_varint(data,index);index+=size
        elif wire==3:
            require(len(groups)<100,'protobuf group nesting exceeds supported bound');groups.append(field)
        elif wire==4:
            require(groups and groups[-1]==field,'unmatched protobuf group');groups.pop()
        else:require(False,'invalid protobuf wire type')
        require(index<=len(data),'truncated protobuf field')
        # prost refuses a known scalar encoded with the wrong wire type.
        if not groups and field in FIELDS:require(wire==0,'wrong wire type for modifier header')
    require(not groups,'unterminated protobuf group')
    return header

def _keys(value,keys,label):
    require(isinstance(value,dict)and set(value)==set(keys),label+' fields differ')

def validate_capture(value,start,end):
    _keys(value,('schema_version','policy','requested_range','coverage','rows'),'capture')
    require(type(value['schema_version'])is int and value['schema_version']==1 and value['policy']==POLICY,'schema/policy differs')
    requested=value['requested_range'];_keys(requested,('start_tick','end_tick','inclusive'),'range')
    require(integer(requested['start_tick'],0,2**31-2)and integer(requested['end_tick'],0,2**31-2)
        and requested['start_tick']<=requested['end_tick']
        and requested['start_tick']==start and requested['end_tick']==end and requested['inclusive']is True,'inclusive requested range differs')
    c=value['coverage'];_keys(c,('availability','reason','origin','table','table_seen','first_observed_tick',
        'last_observed_tick','rows','wire_bytes','scope'),'coverage')
    require(c['availability']in ('available','unavailable')and type(c['table_seen'])is bool,'coverage availability/table differs')
    require(c['origin']=='signon'and c['table']=='ActiveModifiers','prerange origin/table differs')
    require(isinstance(c['scope'],str)and len(c['scope'])<=512,'coverage scope exceeds bound')
    first,last=c['first_observed_tick'],c['last_observed_tick']
    require((first is None and last is None)or(integer(first,-1,end)and integer(last,first,end)),'observation range differs')
    require((c['reason']is None if c['availability']=='available'else isinstance(c['reason'],str)and c['reason']in REASONS),'coverage reason differs')
    if c['availability']=='available':require(c['table_seen']and last==end,'available capture lacks table/end observation')
    rows=value['rows'];require(isinstance(rows,list)and len(rows)<=MAX_ROWS,'row budget exceeded')
    require(integer(c['rows'],0,MAX_ROWS)and c['rows']==len(rows),'row count differs')
    require(integer(c['wire_bytes'],0,MAX_TOTAL),'payload budget exceeded')
    previous=-1;total=0;invalid=0
    for row in rows:
        _keys(row,('tick','kind','slot','payload_present','wire_bytes','header'),'row')
        require(integer(row['tick'],previous,end)and first is not None and first<=row['tick']<=last,'row tick order/range differs')
        previous=row['tick']
        require(row['kind']in ('Delta','Snapshot')and integer(row['slot'],0,65535),'row kind/slot differs')
        require(type(row['payload_present'])is bool,'payload presence differs')
        data=row['wire_bytes'];header=row['header']
        if not row['payload_present']:
            require(data is None and header is None,'omitted payload was materialized');continue
        require(isinstance(data,list)and len(data)<=MAX_PAYLOAD,'per-write payload budget exceeded')
        total+=len(data);require(total<=MAX_TOTAL,'cumulative payload budget exceeded')
        require(all(integer(x,0,255)for x in data),'payload contains nonbytes')
        # The native full-message decoder can refuse non-header fields (nested
        # messages/UTF-8). Keep its final refused bytes explicitly unavailable;
        # the header-only reader must not pretend to validate the entire proto.
        if header is None and c['reason']=='invalid_payload'and c['availability']=='unavailable':
            invalid+=1
            require(row is rows[-1]and invalid==1,'invalid payload was not a sticky final write')
            continue
        try:expected=wire_header(bytes(data))
        except ValueError:
            invalid+=1
            require(c['availability']=='unavailable'and c['reason']=='invalid_payload'and header is None
                and row is rows[-1]and invalid==1,'malformed payload lacks explicit unavailable coverage')
            continue
        _keys(header,FIELDS.values(),'header')
        require(all(header[k]is None or integer(header[k],-2**31 if k in ('entry_type','int1')else 0,
                2**31-1 if k in ('entry_type','int1')else 2**32-1)for k in FIELDS.values()),'header scalar types differ')
        require(header==expected,'header disagrees with retained protobuf')
    require(total==c['wire_bytes'],'payload byte count differs')
    require(not rows or c['table_seen'],'rows without an observed table')
    require(c['reason']!='invalid_payload'or invalid==1,'invalid payload reason lacks retained evidence')
    return value

def retain_capture(output,advertisement,start,end,demo_sha,helper_sha):
    """Return embedded internal capture plus exact extraction/file provenance."""
    return retain_contract(output,advertisement,start,end,demo_sha,helper_sha,
        filename=FILE,policy=POLICY,summary_keys=('rows','wire_bytes'),
        validator=validate_capture,max_json=MAX_JSON,check=require)

def validate_envelope(value,*,demo_sha256,start_tick,end_tick):
    """Recheck portable embedded capture identity without trusting an old path."""
    return validate_contract_envelope(value,demo_sha256=demo_sha256,start_tick=start_tick,end_tick=end_tick,
        filename=FILE,validator=validate_capture,check=require)
