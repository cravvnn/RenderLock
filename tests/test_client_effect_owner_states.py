import copy
import json
import hashlib
from pathlib import Path
import pytest
from agr import client_effect_owner_states as m
from agr import replay
from test_client_modifier_writes import extraction

def owner(tick=10,index=58,serial=283):
    return dict(tick=tick,entity_id=f'{index}:{serial}',entity_index=index,entity_serial=serial,
        hero_id=63,active=True,life_state=0,enabled_state_words=[0]*12,
        predicted_state_words=[0,128,0,8,0,0,0,0,0,0,0,1],disabled_state_words=[0]*12)

def capture(rows=None):
    rows=[owner()]if rows is None else rows
    return dict(schema_version=1,policy=m.POLICY,requested_range=dict(start_tick=10,end_tick=20,inclusive=True),
        coverage=dict(availability='available',reason=None,origin='signon',first_observed_tick=1,last_observed_tick=20,
            rows=len(rows),words=sum(len(r[k])for r in rows for k in m.ARRAYS if r[k]is not None),
            incomplete_rows=sum(r['life_state']is None or any(r[k]is None for k in m.ARRAYS)for r in rows),
            scope='Raw network owner words; missing is not zero.'),rows=rows)

def advertise(c):
    return dict(schema_version=1,policy=m.POLICY,file=m.FILE,
        **{k:c['coverage'][k]for k in ('availability','reason','rows','words','incomplete_rows')})

def store(output,c):
    raw=json.dumps(c).encode();(output/m.FILE).write_bytes(raw);return raw

def test_every_index_and_missing_component_survive_portable_retention(tmp_path):
    missing=owner(20);missing['enabled_state_words']=None;missing['life_state']=None
    c=capture([owner(),missing]);raw=store(tmp_path,c);before=copy.deepcopy(c)
    result=m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    assert result['capture']==before
    assert result['capture']['rows'][0]['predicted_state_words'][11]==1
    assert result['capture']['rows'][0]['predicted_state_words'][1]==128
    assert result['capture']['rows'][1]['enabled_state_words']is None
    assert result['capture']['coverage']['incomplete_rows']==1
    assert result['provenance']['sidecar_sha256']==hashlib.sha256(raw).hexdigest()
    (tmp_path/m.FILE).unlink()
    m.validate_envelope(result,demo_sha256='a'*64,start_tick=10,end_tick=20)

@pytest.mark.parametrize('change',[
 lambda c:c.update(schema_version=True),lambda c:c.update(policy='other'),
 lambda c:c['requested_range'].update(start_tick=True),lambda c:c['requested_range'].update(end_tick=19),
 lambda c:c['requested_range'].update(inclusive=False),lambda c:c['coverage'].update(last_observed_tick=19),
 lambda c:c['coverage'].update(words=0),lambda c:c['coverage'].update(incomplete_rows=True),
 lambda c:c['coverage'].update(availability='available',reason='row_budget'),
 lambda c:c['coverage'].update(availability='unavailable',reason=[]),
 lambda c:c['rows'][0].update(tick=9),lambda c:c['rows'][0].update(entity_id='00058:283'),
 lambda c:c['rows'][0].update(entity_index=0x4000),lambda c:c['rows'][0].update(entity_serial=0x40000),
 lambda c:c['rows'][0].update(hero_id=True),lambda c:c['rows'][0].update(active=1),
 lambda c:c['rows'][0].update(life_state=True),lambda c:c['rows'][0].update(life_state=256),
 lambda c:c['rows'][0].update(enabled_state_words=[]),
 lambda c:c['rows'][0].update(enabled_state_words=[0]*129),
 lambda c:c['rows'][0]['predicted_state_words'].__setitem__(11,True),
 lambda c:c['rows'][0]['predicted_state_words'].__setitem__(11,-1),
 lambda c:c['rows'][0]['predicted_state_words'].__setitem__(11,2**32),
 lambda c:c['rows'][0].update(extra='unknown-v1-key')])
def test_invalid_owner_state_is_not_a_complete_mask_or_zero(change):
    c=capture();change(c)
    with pytest.raises(ValueError,match='Client effect owner states'):m.validate_capture(c,10,20)

def test_order_same_tick_unique_identity_and_word_accounting():
    c=capture([owner(10),owner(10,index=56),owner(20)])
    assert m.validate_capture(c,10,20)is c
    c=capture([owner(10),owner(10)])
    with pytest.raises(ValueError,match='duplicate'):m.validate_capture(c,10,20)
    c=capture([owner(20),owner(10)])
    with pytest.raises(ValueError,match='tick order'):m.validate_capture(c,10,20)

def test_unavailable_empty_and_old_helper_differ(tmp_path):
    c=capture([]);m.validate_capture(c,10,20)
    c['coverage'].update(availability='unavailable',reason='end_tick_not_observed',last_observed_tick=19)
    m.validate_capture(c,10,20)
    result=m.retain_capture(tmp_path,None,10,20,'a'*64,'b'*64)
    assert result['capture']is None and result['reason']=='helper_not_advertised'
    m.validate_envelope(result,demo_sha256='a'*64,start_tick=10,end_tick=20)

def test_bounds_and_advertisement_are_checked_before_acceptance(tmp_path,monkeypatch):
    c=capture();store(tmp_path,c);adv=advertise(c)
    monkeypatch.setattr(m,'MAX_JSON',16)
    with pytest.raises(ValueError,match='serialized'):m.retain_capture(tmp_path,adv,10,20,'a'*64,'b'*64)
    monkeypatch.setattr(m,'MAX_JSON',128*1024*1024);monkeypatch.setattr(m,'MAX_WORDS',1)
    with pytest.raises(ValueError,match='word'):m.validate_capture(c,10,20)
    monkeypatch.setattr(m,'MAX_WORDS',8000000);adv['rows']=True
    with pytest.raises(ValueError,match='disagrees'):m.retain_capture(tmp_path,adv,10,20,'a'*64,'b'*64)

def test_digest_binds_self_consistent_word_edits_and_replay_range(tmp_path):
    c=capture();store(tmp_path,c);result=m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    result['capture']['rows'][0]['predicted_state_words'][1]=2
    with pytest.raises(ValueError,match='payload digest'):m.validate_envelope(result,demo_sha256='a'*64,start_tick=10,end_tick=20)

def test_normal_extraction_retains_optional_owner_capture_without_changing_cli(extraction,monkeypatch):
    e=extraction
    def native(*args,**kwargs):
        result=e.run(*args,**kwargs);coverage=e.output/'native_coverage.json'
        c=capture();store(e.output,c);v=json.loads(coverage.read_text());v['client_effect_owner_states']=advertise(c)
        coverage.write_text(json.dumps(v));return result
    monkeypatch.setattr(replay.subprocess,'run',native)
    v=json.loads(replay.extract_replay(e.demo,e.output,10,20,[63]).read_text())
    owner_capture=v['client_effect_owner_states']
    assert owner_capture['availability']=='available'
    m.validate_envelope(owner_capture,demo_sha256=v['metadata']['demo_sha256'],start_tick=10,end_tick=20)
    assert e.calls==[[str(e.helper),str(e.demo),str(e.output),'10','20','63']]
    assert v['coverage']['particles']=='not_extracted'
    assert v['client_modifier_writes']['availability']=='available'

@pytest.mark.parametrize('collision',['file','directory','dangling_link'])
def test_owner_reserved_collision_stops_before_native(extraction,collision):
    e=extraction;e.output.mkdir();p=e.output/m.FILE
    if collision=='file':p.write_bytes(b'prior')
    elif collision=='directory':p.mkdir()
    else:
        try:p.symlink_to(e.output/'missing-target')
        except OSError as exc:pytest.skip(str(exc))
    with pytest.raises(replay.ReplayError,match='already contains'):replay.extract_replay(e.demo,e.output,10,20)
    assert e.calls==[] and list(e.output.iterdir())==[p]


def test_enclosed_owner_view_keeps_complete_original_arrays_and_digest(tmp_path):
    c=capture([owner(10),owner(15),owner(20)]);store(tmp_path,c)
    e=m.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64);before=copy.deepcopy(e)
    assert m.validate_view(e,demo_sha256='a'*64,start_tick=12,end_tick=18)is e
    assert e==before and [r['tick'] for r in e['capture']['rows']]==[10,15,20]
    assert e['provenance']['capture_sha256']==before['provenance']['capture_sha256']
    with pytest.raises(ValueError,match='original capture range'):
        m.validate_view(e,demo_sha256='a'*64,start_tick=9,end_tick=18)
