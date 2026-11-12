"""Internal modifier history and extraction boundaries; no game assets needed."""
import copy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from agr import client_modifier_writes as m
from agr import replay

def varint(value):
    value&=(1<<64)-1;out=[]
    while value>=128:out.append((value&127)|128);value>>=7
    return bytes(out+[value])

def payload(**fields):
    numbers={v:k for k,v in m.FIELDS.items()}
    return b''.join(varint(numbers[k]<<3)+varint(v)for k,v in fields.items())

def row(tick,kind='Delta',slot=127,data=b''):
    return {'tick':tick,'kind':kind,'slot':slot,'payload_present':data is not None,
        'wire_bytes':None if data is None else list(data),
        'header':None if data is None else m.wire_header(data)}

def capture(rows=None):
    rows=[row(1,data=payload(entry_type=1,parent=56,serial_number=188,int1=1)),
          row(10,'Snapshot',data=payload(entry_type=1,parent=56,serial_number=188,int1=1)),
          row(10,data=payload(entry_type=2,parent=56,serial_number=188)),
          row(10,data=payload(entry_type=1,parent=58,serial_number=189,int1=0)),
          row(10,slot=128,data=None),row(10,slot=129,data=b'')]if rows is None else rows
    return {'schema_version':1,'policy':m.POLICY,
        'requested_range':{'start_tick':10,'end_tick':20,'inclusive':True},
        'coverage':{'availability':'available','reason':None,'origin':'signon','table':'ActiveModifiers',
            'table_seen':True,'first_observed_tick':0,'last_observed_tick':20,
            'rows':len(rows),'wire_bytes':sum(len(r['wire_bytes']or[])for r in rows),
            'scope':'Raw changes, not births.'},'rows':rows}

def advertise(c):
    return dict(schema_version=1,policy=m.POLICY,file=m.FILE,
        **{k:c['coverage'][k]for k in ('availability','reason','rows','wire_bytes')})

def store(tmp_path,c):
    raw=json.dumps(c).encode();(tmp_path/m.FILE).write_bytes(raw);return raw

def test_history_keeps_prerange_repeated_slot_snapshot_removal_and_presence():
    c=capture();assert m.validate_capture(c,10,20)is c
    assert [r['header']and r['header']['entry_type']for r in c['rows']]==[1,1,2,1,None,None]
    assert c['rows'][0]['tick']<c['requested_range']['start_tick']
    assert c['rows'][4]['wire_bytes']is None and c['rows'][5]['wire_bytes']==[]

def test_wire_header_preserves_absence_last_scalar_and_unknown_fields():
    data=payload(parent=56,int1=-1)+payload(parent=58)
    data+=varint(100<<3|2)+b'\x03abc'+varint(101<<3|1)+b'12345678'
    data+=varint(102<<3|3)+payload(parent=999)+varint(102<<3|4)
    result=m.wire_header(data)
    assert result=={'entry_type':None,'parent':58,'serial_number':None,'modifier_subclass':None,'int1':-1}
    assert m.wire_header(b'')==dict.fromkeys(m.FIELDS.values())

@pytest.mark.parametrize('data',[b'\x00',b'\x80',b'\x08'+b'\xff'*10,b'\x12\x00',
    varint(100<<3|2)+b'\x02x',varint(100<<3|4),varint(100<<3|3)])
def test_header_refuses_bad_wire(data):
    with pytest.raises(ValueError):m.wire_header(data)

@pytest.mark.parametrize('mutate',[
    lambda c:c.update(schema_version=True),
    lambda c:c['requested_range'].update(start_tick=True),
    lambda c:c['requested_range'].update(end_tick=21),
    lambda c:c['requested_range'].update(inclusive=False),
    lambda c:c['coverage'].update(table_seen=False),
    lambda c:c['coverage'].update(last_observed_tick=19),
    lambda c:c['coverage'].update(reason=[]),
    lambda c:c['coverage'].update(rows=5),
    lambda c:c['coverage'].update(wire_bytes=0),
    lambda c:c['coverage'].update(origin='shot_start'),
    lambda c:c['rows'][0].update(tick=11),
    lambda c:c['rows'][0].update(slot=True),
    lambda c:c['rows'][0].update(kind='Merged'),
    lambda c:c['rows'][0].update(payload_present=False),
    lambda c:c['rows'][0]['header'].update(parent=57),
    lambda c:c['rows'][0]['header'].update(int1=True),
    lambda c:c['rows'][0]['wire_bytes'].__setitem__(0,True),
    lambda c:c['rows'][0].update(extra='unknown-v1-key'),
])
def test_invalid_capture_does_not_become_empty_or_available(mutate):
    c=capture();mutate(c)
    with pytest.raises(ValueError,match='Client modifier writes'):m.validate_capture(c,10,20)

def test_unavailable_retains_refused_payload_without_claiming_header_or_semantics():
    c=capture([row(1)]);c['rows'][0].update(wire_bytes=[0xff],header=None)
    c['coverage'].update(availability='unavailable',reason='invalid_payload',wire_bytes=1)
    assert m.validate_capture(c,10,20)is c
    # Full protobuf can reject invalid UTF-8 outside the five retained headers.
    c['rows'][0]['wire_bytes']=list(varint(45<<3|2)+b'\x01\xff');c['coverage']['wire_bytes']=4
    m.validate_capture(c,10,20)
    c['coverage'].update(availability='available',reason=None)
    with pytest.raises(ValueError):m.validate_capture(c,10,20)

def test_empty_available_missing_table_and_old_helper_are_distinct(tmp_path):
    c=capture([]);assert m.validate_capture(c,10,20)['coverage']['availability']=='available'
    c['coverage'].update(availability='unavailable',reason='table_not_observed',table_seen=False)
    assert m.validate_capture(c,10,20)['coverage']['reason']=='table_not_observed'
    result=m.retain_capture(tmp_path,None,10,20,'a'*64,'b'*64)
    assert result['capture']is None and result['reason']=='helper_not_advertised'
    assert result['provenance']['sidecar_sha256']is None

def test_bounded_rows_bytes_and_serialized_read(tmp_path,monkeypatch):
    c=capture();monkeypatch.setattr(m,'MAX_ROWS',2)
    with pytest.raises(ValueError,match='row budget'):m.validate_capture(c,10,20)
    monkeypatch.setattr(m,'MAX_ROWS',65536);monkeypatch.setattr(m,'MAX_PAYLOAD',1)
    with pytest.raises(ValueError,match='per-write'):m.validate_capture(c,10,20)
    monkeypatch.setattr(m,'MAX_PAYLOAD',16384);monkeypatch.setattr(m,'MAX_TOTAL',1)
    with pytest.raises(ValueError,match='payload budget'):m.validate_capture(c,10,20)
    store(tmp_path,c);monkeypatch.setattr(m,'MAX_JSON',16)
    with pytest.raises(ValueError,match='serialized'):m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)

def test_retention_binds_exact_bytes_range_replay_helper_and_advertisement(tmp_path):
    c=capture();raw=store(tmp_path,c)
    result=m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    assert result['capture']==c
    assert result['provenance']=={'demo_sha256':'a'*64,'helper_sha256':'b'*64,'start_tick':10,
        'end_tick':20,'tick_range_inclusive':True,'sidecar_filename':m.FILE,
        'sidecar_sha256':hashlib.sha256(raw).hexdigest(),'capture_sha256':m.canonical_sha256(c)}
    adv=advertise(c);adv['rows']-=1
    with pytest.raises(ValueError,match='disagrees'):m.retain_capture(tmp_path,adv,10,20,'a'*64,'b'*64)
    with pytest.raises(ValueError,match='unadvertised'):m.retain_capture(tmp_path,None,10,20,'a'*64,'b'*64)

def test_advertised_missing_duplicate_keys_and_unknown_contract_refuse(tmp_path):
    c=capture()
    with pytest.raises(ValueError,match='missing'):m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    store(tmp_path,c);p=tmp_path/m.FILE;p.write_text(p.read_text().replace('"schema_version": 1','"schema_version": 1,"schema_version": 1'))
    with pytest.raises(ValueError,match='duplicate'):m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    adv=advertise(c);adv['policy']='future'
    with pytest.raises(ValueError,match='contract differs'):m.retain_capture(tmp_path,adv,10,20,'a'*64,'b'*64)

@pytest.fixture
def extraction(tmp_path,monkeypatch):
    demo=tmp_path/'owned.dem';demo.write_bytes(b'PBDEMS2\0'+b'\0'*8)
    helper=tmp_path/'helper.exe';helper.write_bytes(b'qualified-helper')
    class Frame:
        def filter(self,*args):return self
        def to_dicts(self):return []
    class Demo:
        build=10932;map_name='start';match_id=1;tick_rate=64;total_ticks=20;total_seconds=20/64
        def __init__(self,path):pass
        def load(self,*args):self.kills=Frame();self.abilities=Frame()
    monkeypatch.setattr(replay,'_dependencies',lambda:(SimpleNamespace(Demo=Demo),
        SimpleNamespace(col=lambda name:SimpleNamespace(is_between=lambda a,b:None))))
    monkeypatch.setattr(replay,'package_version',lambda name:'0.15.0')
    monkeypatch.setattr(replay,'find_native_extractor',lambda:helper)
    calls=[]
    def run(command,**kwargs):
        calls.append(command);out=Path(command[2]);prefix='CBodyComponent.m_skeletonInstance.'
        fields={prefix+'m_vecOrigin.m_cell'+axis:64 for axis in 'XYZ'}
        fields.update({prefix+'m_vecOrigin.m_vec'+axis:0 for axis in 'XYZ'})
        fields[prefix+'m_angRotation']=[0,0,0]
        entity={'demo_tick':10,'hero_id':63,'entity_id':'58:283','entity_index':58,'entity_serial':283,
            'fields':fields,'active':True,'serialized_pose_recipe_array':[1,0,0,0]}
        c=capture();store(out,c)
        for name,data in [('raw_animation_state.json',[entity]),('network_ticks.json',[{'demo_tick':10,'server_tick':123}]),
            ('native_coverage.json',{'source_world_half':32768,'boon_version':'0.15.0','client_modifier_writes':advertise(c)})]:
            (out/name).write_text(json.dumps(data))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(replay.subprocess,'run',run)
    return SimpleNamespace(demo=demo,helper=helper,output=tmp_path/'output',calls=calls,run=run)

def test_extract_signature_cli_metadata_and_animation_outputs_preserved(extraction):
    e=extraction
    assert list(inspect.signature(replay.extract_replay).parameters)==['demo','output','start_tick','end_tick','hero_ids']
    path=replay.extract_replay(e.demo,e.output,10,20,[63]);manifest=json.loads(path.read_text())
    assert e.calls==[[str(e.helper),str(e.demo),str(e.output), '10','20','63']]
    assert manifest['metadata']['demo_sha256']==m.file_sha256(e.demo)
    assert manifest['start_tick']==10 and manifest['end_tick']==20
    assert manifest['coverage']['particles']=='not_extracted'
    assert manifest['client_modifier_writes']['capture']['rows'][0]['tick']==1
    assert manifest['client_modifier_writes']['provenance']['helper_sha256']==m.file_sha256(e.helper)
    assert json.loads((e.output/'network_ticks.json').read_text())==[{'demo_tick':10,'server_tick':123}]
    assert (e.output/'animation_provenance.json').is_file()

@pytest.mark.parametrize('collision',['file','directory','dangling_link'])
def test_reserved_sidecar_collision_fails_before_native_or_any_output(extraction,collision):
    e=extraction;e.output.mkdir();p=e.output/m.FILE
    if collision=='file':p.write_bytes(b'prior')
    elif collision=='directory':p.mkdir()
    else:
        try:p.symlink_to(e.output/'missing-target')
        except OSError as exc:pytest.skip(str(exc))
    with pytest.raises(replay.ReplayError,match='already contains'):replay.extract_replay(e.demo,e.output,10,20)
    assert e.calls==[] and set(x.name for x in e.output.iterdir())=={m.FILE}

@pytest.mark.parametrize('change',['demo','helper','missing_advertised_sidecar'])
def test_native_identity_or_advertised_capture_change_fails_named_contract(extraction,monkeypatch,change):
    e=extraction
    def run(*args,**kwargs):
        result=e.run(*args,**kwargs)
        if change=='demo':e.demo.write_bytes(e.demo.read_bytes()+b'changed')
        elif change=='helper':e.helper.write_bytes(b'changed')
        else:(e.output/m.FILE).unlink()
        return result
    monkeypatch.setattr(replay.subprocess,'run',run)
    with pytest.raises(replay.ReplayError,match='changed|sidecar is missing'):replay.extract_replay(e.demo,e.output,10,20)
    assert not (e.output/'segment.json').exists()


def test_reversed_raw_range_is_rejected():
    c=capture([]);c['requested_range'].update(start_tick=20,end_tick=10)
    with pytest.raises(ValueError,match='inclusive requested range'):
        m.validate_capture(c,20,10)


@pytest.mark.parametrize('failure',[False,True])
def test_modifier_capture_is_independent_of_projectile_availability(extraction,monkeypatch,failure):
    from agr import network_projectile_capture as p
    e=extraction
    def run(command,**kwargs):
        result=e.run(command,**kwargs);out=Path(command[2])
        c=dict(schema_version=1,policy=p.POLICY,requested_range=dict(start_tick=10,end_tick=20,inclusive=True),
            coverage={'availability':'unavailable'if failure else 'available','reason':'capture_failed'if failure else None,
                'origin':'signon','class':p.CLASS,'first_observed_tick':0,'last_observed_tick':20,
                'tracks':0,'samples':0,'scope':'Synthetic observed capture'},tracks=[])
        (out/p.FILE).write_text(json.dumps(c))
        coverage=json.loads((out/'native_coverage.json').read_text())
        coverage['projectile_particles']=dict(schema_version=1,policy=p.POLICY,file=p.FILE,
            **{k:c['coverage'][k]for k in ('availability','reason','tracks','samples')})
        (out/'native_coverage.json').write_text(json.dumps(coverage))
        return result
    monkeypatch.setattr(replay.subprocess,'run',run)
    data=json.loads(replay.extract_replay(e.demo,e.output,10,20,[63]).read_text())
    assert data['client_modifier_writes']['availability']=='available'
    assert data['network_projectiles']['availability']==('unavailable'if failure else 'available')
    assert len(data['entities'])==1 and data['coverage']['particles']=='not_extracted'
    m.validate_envelope(data['client_modifier_writes'],demo_sha256=data['metadata']['demo_sha256'],start_tick=10,end_tick=20)


@pytest.mark.parametrize('mutation',['replay','range','helper','payload','summary'])
def test_retained_capture_cannot_change_identity_or_become_available(tmp_path,mutation):
    c=capture();store(tmp_path,c);value=m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    if mutation=='replay':value['provenance']['demo_sha256']='c'*64
    elif mutation=='range':value['provenance']['start_tick']=9
    elif mutation=='helper':value['provenance']['helper_sha256']='bad'
    elif mutation=='payload':value['capture']['rows'][0]['slot']+=1
    else:value.update(availability='unavailable',reason='row_budget')
    with pytest.raises(ValueError):m.validate_envelope(value,demo_sha256='a'*64,start_tick=10,end_tick=20)
