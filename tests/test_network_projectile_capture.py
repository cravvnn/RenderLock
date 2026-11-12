import copy
import json
from types import SimpleNamespace
import pytest
from agr import network_projectile_capture as capture


def payload(tracks=True):
    rows=[dict(tick=t,active=True,fields={'m_hOwnerEntity':1})for t in range(3)]
    return dict(schema_version=1,policy=capture.POLICY,
        requested_range=dict(start_tick=1,end_tick=2,inclusive=True),
        coverage=dict(availability='available',reason=None,origin='signon',class_=capture.CLASS,
            first_observed_tick=0,last_observed_tick=2,tracks=int(tracks),samples=3 if tracks else 0,scope='Network observations'),
        tracks=[dict(id='7:2',class_=capture.CLASS,first_seen_tick=0,removed_tick=None,samples=rows)]if tracks else [])


def raw(tracks=True):
    value=payload(tracks);value['coverage']['class']=value['coverage'].pop('class_')
    for t in value['tracks']:t['class']=t.pop('class_')
    return value


def advertise(value):
    c=value['coverage']
    return dict(schema_version=1,policy=capture.POLICY,file=capture.FILE,
        **{k:c[k]for k in ('availability','reason','tracks','samples')})


def retain(tmp_path,value):
    (tmp_path/capture.FILE).write_text(json.dumps(value))
    return capture.retain_capture(tmp_path,advertise(value),1,2,'a'*64,'b'*64)


def test_prerange_and_changed_owner_rows_retained_with_digests(tmp_path):
    value=raw();value['tracks'][0]['samples'][1]['fields']['m_hOwnerEntity']=2
    result=retain(tmp_path,value)
    assert result['capture']==value
    assert [r['tick']for r in result['capture']['tracks'][0]['samples']]==[0,1,2]
    assert result['provenance']['sidecar_sha256']and result['provenance']['capture_sha256']
    assert capture.validate_envelope(result,dict(metadata={'demo_sha256':'a'*64},start_tick=1,end_tick=2))is result


def test_available_empty_differs_from_unadvertised(tmp_path):
    missing=capture.retain_capture(tmp_path,None,1,2,'a'*64,'b'*64)
    assert missing['availability']=='unavailable'and missing['capture']is None
    empty=retain(tmp_path,raw(False))
    assert empty['availability']=='available'and empty['capture']['tracks']==[]


def test_cropped_view_preserves_original_history_and_provenance(tmp_path):
    result=retain(tmp_path,raw());before=copy.deepcopy(result)
    capture.validate_envelope(result,dict(metadata={'demo_sha256':'a'*64},start_tick=2,end_tick=2))
    assert result==before
    with pytest.raises(ValueError,match='exceeds'):
        capture.validate_envelope(result,dict(metadata={'demo_sha256':'a'*64},start_tick=0,end_tick=2))


def test_missing_prefix_stays_visible_for_provider_refusal():
    value=raw();value['tracks'][0]['samples'].pop(0);value['coverage']['samples']=2
    capture.validate_capture(value,1,2)
    assert value['tracks'][0]['first_seen_tick']==0
    assert value['tracks'][0]['samples'][0]['tick']==1


@pytest.mark.parametrize('mutate',[
    lambda v:v.update(schema_version=True),lambda v:v.update(policy='agr_network_projectile_fields_v1'),
    lambda v:v['requested_range'].update(inclusive=1),lambda v:v['coverage'].update(samples=2),
    lambda v:v['coverage'].update(last_observed_tick=1),lambda v:v['coverage'].update(origin='shot_start'),
    lambda v:v['coverage'].update(availability='available',reason='end_tick_not_observed'),
    lambda v:v['tracks'].append(copy.deepcopy(v['tracks'][0])),
    lambda v:v['tracks'][0].update(id='16384:2'),lambda v:v['tracks'][0].update(id='7:262144'),
    lambda v:v['tracks'][0].update(first_seen_tick=True),lambda v:v['tracks'][0].update(removed_tick=2),
    lambda v:v['tracks'][0]['samples'][1].update(tick=0),
    lambda v:v['tracks'][0]['samples'][1].update(active=1),
    lambda v:v['tracks'][0]['samples'][0]['fields'].update(x=float('nan')),
    lambda v:v['tracks'][0]['samples'][0]['fields'].update(x=[[1]]),
    lambda v:v['tracks'][0]['samples'][0]['fields'].update(x=[0]*5),
    lambda v:v['tracks'][0]['samples'][0]['fields'].update(x='x'*257),
])
def test_capture_mutations_refused(mutate):
    value=raw();mutate(value)
    with pytest.raises(ValueError):capture.validate_capture(value,1,2)


@pytest.mark.parametrize('mutate',[
    lambda e:e['provenance'].update(demo_sha256='c'*64),
    lambda e:e['provenance'].update(helper_sha256='unqualified'),
    lambda e:e['provenance'].update(start_tick=0),
    lambda e:e['provenance'].update(capture_sha256='c'*64),
    lambda e:e['capture']['tracks'][0]['samples'][0]['fields'].update(m_hOwnerEntity=99),
    lambda e:e.update(availability='unavailable'),
])
def test_envelope_provenance_mutations_refused(tmp_path,mutate):
    result=retain(tmp_path,raw());mutate(result)
    with pytest.raises(ValueError):capture.validate_envelope(result,dict(metadata={'demo_sha256':'a'*64},start_tick=1,end_tick=2))


@pytest.mark.parametrize('text',['{"policy":1,"policy":2}','NaN','{"x":'])
def test_duplicate_nonfinite_or_invalid_json_refused(tmp_path,text):
    (tmp_path/capture.FILE).write_text(text)
    with pytest.raises(ValueError):capture.retain_capture(tmp_path,advertise(raw()),1,2,'a'*64,'b'*64)


def test_unadvertised_or_oversized_sidecar_refused(tmp_path,monkeypatch):
    (tmp_path/capture.FILE).write_text(json.dumps(raw()))
    with pytest.raises(ValueError,match='unadvertised'):capture.retain_capture(tmp_path,None,1,2,'a'*64,'b'*64)
    monkeypatch.setattr(capture,'MAX_JSON',10)
    with pytest.raises(ValueError,match='budget'):capture.retain_capture(tmp_path,advertise(raw()),1,2,'a'*64,'b'*64)


def test_extraction_rejects_reserved_sidecar_before_running_helper(tmp_path,monkeypatch):
    from agr import replay
    demo=tmp_path/'demo.dem';demo.write_bytes(b'PBDEMS2\0'+b'\0'*8)
    helper=tmp_path/'extract.exe';helper.write_bytes(b'helper')
    monkeypatch.setattr(replay,'find_native_extractor',lambda:helper)
    monkeypatch.setattr(replay,'_dependencies',lambda:(SimpleNamespace(Demo=lambda _:SimpleNamespace(
        build=1,map_name='start',match_id=1,tick_rate=64,total_ticks=3,total_seconds=3/64)),None))
    monkeypatch.setattr(replay.subprocess,'run',lambda *a,**kw:pytest.fail('reserved sidecar must block native invocation'))
    output=tmp_path/'out';output.mkdir();(output/capture.FILE).write_text('prior')
    with pytest.raises(replay.ReplayError,match='network_projectiles'):replay.extract_replay(demo,output,1,2)
    assert (output/capture.FILE).read_text()=='prior'


@pytest.mark.parametrize('reason',['capture_failed','serialized_budget_exceeded'])
def test_failed_capture_is_retained_unavailable_with_exact_provenance(tmp_path,reason):
    value=raw(False);value['coverage'].update(availability='unavailable',reason=reason)
    result=retain(tmp_path,value)
    assert result['availability']=='unavailable'and result['reason']==reason
    assert result['capture']['tracks']==[] and result['provenance']['sidecar_sha256']
    capture.validate_envelope(result,dict(metadata={'demo_sha256':'a'*64},start_tick=1,end_tick=2))
    from agr.projectile_particles import selected_tracks
    with pytest.raises(ValueError,match='capture unavailable'):
        selected_tracks(result,dict(metadata={'demo_sha256':'a'*64},start_tick=1,end_tick=2,entities=[]))
    from agr.recorded_projectile_effects import build_projectile_effects
    data=dict(metadata={'demo_sha256':'a'*64},start_tick=1,end_tick=2,network_projectiles=result)
    report=build_projectile_effects(data,None,None,None,None,None,None)
    assert report['status']=='unavailable'and report['capture_reason']==reason
    assert report['instances']==report['unsupported']==[] # Unknown count, not invented occurrences.


@pytest.mark.parametrize('reason',['capture_failed','serialized_budget_exceeded'])
@pytest.mark.parametrize('mutation',['retained_prefix','available','unknown_reason'])
def test_capture_failure_cannot_become_a_qualified_prefix(reason,mutation):
    value=raw(False);value['coverage'].update(availability='unavailable',reason=reason)
    if mutation=='retained_prefix':
        full=raw();value['tracks']=full['tracks'];value['coverage'].update(tracks=1,samples=3)
    elif mutation=='available':value['coverage']['availability']='available'
    else:value['coverage']['reason']='unqualified_failure'
    with pytest.raises(ValueError):capture.validate_capture(value,1,2)
