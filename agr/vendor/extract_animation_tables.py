"""Extract and audit replay animation string tables without running the game.

Requires python-snappy. Supports the PBDEMS2 command/packet format used by the
provided demo. Reads the sequential stream and verifies any seek snapshots that
contain animation tables. Unsupported encodings fail rather than guessing.
"""
import argparse, collections, hashlib, io, json, struct
from pathlib import Path
import snappy

ANIMATION_TABLES={'AnimAssetData','AnimTaskTypes'}

def varint(stream):
    n=0
    for shift in range(0,70,7):
        b=stream.read(1)
        if not b: raise EOFError('truncated varint')
        n|=(b[0]&127)<<shift
        if b[0]<128:return n
    raise ValueError('oversized varint')

def wire(data):
    """Minimal protobuf wire reader preserving repeated fields."""
    stream=io.BytesIO(data);out=collections.defaultdict(list)
    while stream.tell()<len(data):
        tag=varint(stream);field,kind=tag>>3,tag&7
        if not field:raise ValueError('protobuf field zero')
        if kind==0:value=varint(stream)
        elif kind in (1,2,5):
            size=varint(stream) if kind==2 else 8 if kind==1 else 4
            value=stream.read(size)
            if len(value)!=size:raise ValueError('truncated protobuf field')
        else:raise ValueError(f'unsupported protobuf wire type {kind}')
        out[field].append(value)
    return dict(out)

def one(fields,key,default=None):
    values=fields.get(key,[])
    if len(values)>1:raise ValueError(f'duplicate singular protobuf field {key}')
    return values[0] if values else default

class Bits:
    def __init__(self,data):self.data=data;self.p=0;self.size=len(data)*8
    def read(self,n):
        if n<0 or self.p+n>self.size:raise ValueError('bitstream overrun')
        v=int.from_bytes(self.data[self.p//8:(self.p+n+7)//8],'little')>>(self.p%8)
        self.p+=n;return v&((1<<n)-1)
    def varint(self):
        v=0
        for shift in range(0,70,7):
            n=self.read(8);v|=(n&127)<<shift
            if n<128:return v
        raise ValueError('oversized bitstream varint')
    def ubitvar(self):
        v=self.read(6);flag=v&48
        return (v&15)|(self.read({16:4,32:8,48:28}[flag])<<4) if flag else v
    def bytes(self,n):
        if n<0 or self.p+8*n>self.size:raise ValueError('byte read overrun')
        if self.p%8==0:
            v=self.data[self.p//8:self.p//8+n];self.p+=n*8;return v
        return self.read(n*8).to_bytes(n,'little')
    def string(self):
        out=bytearray()
        while (v:=self.read(8))!=0:
            out.append(v)
            if len(out)>65536:raise ValueError('oversized string')
        return out.decode('utf8')

def entries(data,count,meta,existing):
    b=Bits(data);index=-1;history=[];changed=[]
    for _ in range(count):
        # Explicit indices use the wire value plus one; they are not a delta
        # from the previous entry. Checked against the demo's snapshots.
        index=index+1 if b.read(1) else b.varint()+1
        old=existing.get(index);name=old['name'] if old else None
        if b.read(1):
            if b.read(1):
                slot,length=b.read(5),b.read(5)
                if slot>=len(history) or length>len(history[slot]):
                    raise ValueError('invalid string history reference')
                name=history[slot][:length]+b.string()
            else:name=b.string()
            history.append(name)
            if len(history)>32:history.pop(0)
        value=old['data_hex'] if old else ''
        if b.read(1):
            compressed=False
            if meta['fixed']:
                bits=meta['user_bits']
                if bits%8:raise ValueError('non-byte-aligned fixed animation table')
                size=bits//8
            else:
                if meta['flags']&1:compressed=bool(b.read(1))
                size=b.ubitvar() if meta['varint_sizes'] else b.read(17)
            raw=b.bytes(size)
            value=(snappy.decompress(raw) if compressed else raw).hex()
        if name is None:raise ValueError('entry with no recoverable name')
        row={'index':index,'name':name,'data_hex':value}
        existing[index]=row;changed.append(row)
    remaining=b.size-b.p
    # The byte container can include nonzero unused high bits from its writer.
    if remaining>=8:raise ValueError('unexpected string-table trailing bytes')
    return changed

def parse_asset(row):
    raw=bytes.fromhex(row['data_hex']);pos=0
    def u32():
        nonlocal pos
        if pos+4>len(raw):raise ValueError('asset table integer overrun')
        v=struct.unpack_from('<I',raw,pos)[0];pos+=4;return v
    def strings():
        nonlocal pos
        n=u32();out=[]
        if n>65536:raise ValueError('asset table list too large')
        for _ in range(n):
            end=raw.index(0,pos);out.append(raw[pos:end].decode());pos=end+1
        return out
    version=u32()
    if version!=1:raise ValueError(f'unsupported AnimAssetData version {version}')
    out={'table_index':row['index'],'resource':row['name'],'format_version':version,
         'raw_sha256':hashlib.sha256(raw).hexdigest()}
    if row['name'].endswith('.vnmgraph'):out['resources']=strings()
    elif row['name'].endswith('.vnmskel'):
        out['bones']=strings();out['masks']=strings()
    else:raise ValueError(f'unsupported animation resource {row["name"]}')
    if pos!=len(raw):raise ValueError('unparsed asset table tail')
    return out

def extract(path,end_tick):
    metadata=[];state={};initial=None;snapshots=[];changes=[]
    command_counts=collections.Counter();update_counts=collections.Counter()
    header=None;clears=[];message_count=0
    with path.open('rb') as f:
        if f.read(8)!=b'PBDEMS2\0':raise ValueError('not a Source 2 demo')
        f.read(8)
        while f.tell()<path.stat().st_size:
            position=f.tell();command,tick,size=varint(f),varint(f),varint(f)
            if tick&0x80000000:tick-=0x100000000
            raw=f.read(size)
            if len(raw)!=size:raise ValueError('truncated demo command')
            if tick>end_tick:break
            compressed=bool(command&64);command&=~64;command_counts[command]+=1
            if command not in [1,6,7,8,13]:continue
            if compressed:raw=snappy.decompress(raw)
            fields=wire(raw)
            if command==1:
                header={name:(one(fields,k).decode().rstrip('\0') if text else one(fields,k))
                    for k,name,text in [(1,'stamp',True),(2,'patch_version',False),(5,'map',True),
                                       (6,'game_directory',True),(13,'build_num',False)] if k in fields}
            if command in [6,13]:
                tables=fields if command==6 else wire(one(fields,1,b''))
                for table_data in tables.get(1,[]):
                    table=wire(table_data);name=one(table,1,b'').decode()
                    if name not in ANIMATION_TABLES:continue
                    rows=[]
                    for i,item_data in enumerate(table.get(2,[])):
                        item=wire(item_data)
                        rows.append({'index':i,'name':one(item,1,b'').decode(),
                                     'data_hex':one(item,2,b'').hex()})
                    live=state.get(name,{})
                    # Snapshot items can omit empty indices; all animation
                    # snapshots in this demo are dense. Fail on any mismatch.
                    same=rows==[live[k] for k in sorted(live)]
                    mismatches=[{'index':r['index'],'snapshot_name':r['name'],
                                 'live_name':live.get(r['index'],{}).get('name'),
                                 'data_same':r['data_hex']==live.get(r['index'],{}).get('data_hex')}
                                for r in rows if r!=live.get(r['index'])]
                    snapshots.append({'tick':tick,'command':command,'name':name,
                                      'entries':len(rows),'live_entries':len(live),
                                      'matches_sequential_stream':same,'mismatches':mismatches})
                    if not same:raise ValueError(f'animation snapshot differs: {name} at {tick}: {mismatches[:3]}')
                if command==6 and initial is None:
                    initial={n:[dict(v) for _,v in sorted(rows.items())] for n,rows in state.items()}
            if command not in [7,8]:continue
            b=Bits(one(fields,3,b''))
            while b.size-b.p>=14:
                kind=b.ubitvar();length=b.varint();payload=b.bytes(length);message_count+=1
                if kind==51:
                    metadata=[];state={};clears.append(tick)
                elif kind==44:
                    d=wire(payload);name=one(d,1,b'').decode()
                    meta={'name':name,'fixed':bool(one(d,3,0)),
                          'user_bits':one(d,4,0),'flags':one(d,6,0),
                          'varint_sizes':bool(one(d,10,0))}
                    metadata.append(meta)
                    if name in ANIMATION_TABLES:
                        data=one(d,7,b'')
                        if one(d,9,0):data=snappy.decompress(data)
                        if 8 in d and len(data)!=one(d,8):raise ValueError('decompressed table size mismatch')
                        target=state.setdefault(name,{})
                        entries(data,one(d,2,0),meta,target)
                elif kind==45:
                    d=wire(payload);table_id=one(d,1,0)
                    if table_id>=len(metadata):raise ValueError('unknown string table ID')
                    meta=metadata[table_id];name=meta['name'];update_counts[name]+=1
                    if name in ANIMATION_TABLES:
                        for row in entries(one(d,3,b''),one(d,2,1),meta,state[name]):
                            changes.append({'tick':tick,'table':name,**row})
    if initial is None:raise ValueError('no initial animation table snapshot')
    final={name:[v for _,v in sorted(rows.items())] for name,rows in state.items()}
    return {'scope':'Recorded lookup data; no final-pose evaluation or game-runtime execution.',
            'demo_header':header,'end_tick':end_tick,'command_counts':dict(command_counts),
            'network_messages':message_count,'table_clears':clears,
            'network_update_counts':dict(update_counts),'snapshot_checks':snapshots,
            'initial_tables':initial,'animation_table_updates':changes,'final_tables':final,
            'assets':[parse_asset(row) for row in final['AnimAssetData']],
            'task_types':[row['name'] for row in final['AnimTaskTypes']]}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--demo',required=True,type=Path)
    ap.add_argument('--end-tick',type=int,default=113560)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args();result=extract(args.demo,args.end_tick)
    result['demo_sha256']=hashlib.file_digest(args.demo.open('rb'),'sha256').hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'assets':len(result['assets']),'task_types':result['task_types'],
                      'snapshot_checks':len(result['snapshot_checks']),
                      'updated_entries':[(r['tick'],r['name']) for r in result['animation_table_updates']]},indent=2))

if __name__=='__main__':main()
