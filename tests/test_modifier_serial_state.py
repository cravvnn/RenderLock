import copy
import json
import pytest
from agr import client_modifier_writes as capture_io
from agr.modifier_serial_state import fold_capture,fold_retained
from test_client_modifier_writes import capture,row,payload,advertise,store

def active(tick,serial,slot=0,**fields):
    return row(tick,slot=slot,data=payload(serial_number=serial,**fields))

def test_slot_reuse_preserves_both_serials_and_snapshot_does_not_apply():
    c=capture([active(1,7,parent=56,int1=1),active(2,8,parent=58,int1=0),
        row(3,'Snapshot',slot=0,data=payload(entry_type=2,serial_number=7)),
        active(4,7,slot=1,int1=2)])
    before=copy.deepcopy(c);result=fold_capture(c)
    assert c==before
    a,b=result['states'];assert [a['header']['serial_number'],b['header']['serial_number']]==[7,8]
    assert a['header']['parent']==56 and a['header']['int1']==2
    assert a['first_active_tick']==1 and a['first_active_row_index']==0
    assert a['header']['entry_type']is None # Operational proto default did not fill data.
    assert b['header']['parent']==58 and b['header']['int1']==0
    assert result['counts']['snapshots_ignored']==1
    assert result['events'][0]['state']['header']['int1']==1 # Earlier view was not mutated.

def test_removal_targets_serial_not_slot_and_reapply_has_no_stale_parent_or_choice():
    c=capture([active(1,7,slot=0,parent=56,int1=1),active(2,8,slot=0,parent=58,int1=0),
        active(3,7,slot=0,entry_type=2),active(4,7,slot=1,entry_type=1)])
    result=fold_capture(c);a,b=result['states']
    assert a['header']['serial_number']==7 and a['header']['parent']is None and a['header']['int1']is None
    assert a['first_active_tick']==4 and a['first_active_row_index']==3
    assert b['header']['serial_number']==8 and b['header']['parent']==58
    assert result['events'][2]['action']=='raw_removed'
    assert result['events'][2]['state']['header']['parent']==56

def test_omitted_and_empty_payloads_are_neither_removal_nor_default_state():
    c=capture([active(1,7,int1=3),row(2,slot=0,data=None),row(3,slot=0,data=b''),active(4,8,entry_type=2)])
    result=fold_capture(c)
    assert len(result['states'])==1 and result['states'][0]['header']['int1']==3
    assert result['counts']['omitted_payloads']==result['counts']['empty_payloads']==1
    assert result['counts']['removals_without_prior_state']==1
    assert result['events'][-1]['action']=='raw_remove_without_prior_state'

def test_prerange_state_is_retained_with_inclusive_exact_tick_queries():
    c=capture([active(1,7,int1=1),active(10,7,int1=2),active(20,7,entry_type=2)])
    assert fold_capture(c,through_tick=0)['states']==[]
    assert fold_capture(c,through_tick=9)['states'][0]['header']['int1']==1
    assert fold_capture(c,through_tick=10)['states'][0]['header']['int1']==2
    assert fold_capture(c,through_tick=19)['states'][0]['header']['int1']==2
    assert fold_capture(c,through_tick=20)['states']==[]

@pytest.mark.parametrize('tick',[True,-1,21,1.5,None])
def test_invalid_query_does_not_retime_history(tick):
    if tick is None:assert fold_capture(capture(),through_tick=tick)['through_tick']==20
    else:
        with pytest.raises(ValueError):fold_capture(capture(),through_tick=tick)

@pytest.mark.parametrize('fields',[{'int1':1},{'serial_number':7,'entry_type':3}])
def test_unknown_serial_or_kind_refuses_entire_fold_without_slot_fallback(fields):
    c=capture([active(1,8,int1=1),row(2,slot=8,data=payload(**fields))])
    with pytest.raises(ValueError,match='serial|entry type'):fold_capture(c)

def test_unavailable_capture_does_not_get_a_raw_state_view():
    c=capture([]);c['coverage'].update(availability='unavailable',reason='table_not_observed',table_seen=False)
    with pytest.raises(ValueError,match='unavailable'):fold_capture(c)

def envelope(tmp_path):
    c=capture();store(tmp_path,c)
    return capture_io.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)

def test_embedded_capture_rechecks_identity_without_external_file(tmp_path):
    e=envelope(tmp_path);(tmp_path/capture_io.FILE).unlink()
    scene={'metadata':{'demo_sha256':'a'*64},'start_tick':10,'end_tick':20}
    result=fold_retained(e,scene)
    assert result['states'][0]['header']['serial_number']==189
    assert result['source_capture_sha256']==e['provenance']['capture_sha256']
    assert result['states'][0]['header']['parent']==58

@pytest.mark.parametrize('change',['payload','header','demo','range','helper','availability','missing_digest'])
def test_changed_portable_identity_cannot_reuse_retained_capture(tmp_path,change):
    e=envelope(tmp_path);scene={'metadata':{'demo_sha256':'a'*64},'start_tick':10,'end_tick':20}
    if change=='payload':
        # A self-consistent field+wire edit still fails the embedded capture digest.
        r=e['capture']['rows'][0];r['wire_bytes'][-1]=2;r['header']['int1']=2
    elif change=='header':e['capture']['rows'][0]['header']['int1']=2
    elif change=='demo':scene['metadata']['demo_sha256']='c'*64
    elif change=='range':scene['end_tick']=21
    elif change=='helper':e['provenance']['helper_sha256']='not-a-digest'
    elif change=='availability':e['availability']='unavailable'
    else:del e['provenance']['capture_sha256']
    with pytest.raises(ValueError):fold_retained(e,scene)

def test_old_helper_remains_explicitly_unavailable_not_empty(tmp_path):
    e=capture_io.retain_capture(tmp_path,None,10,20,'a'*64,'b'*64)
    capture_io.validate_envelope(e,demo_sha256='a'*64,start_tick=10,end_tick=20)
    assert e['provenance']['capture_sha256']is None
    with pytest.raises(ValueError,match='unavailable'):
        fold_retained(e,{'metadata':{'demo_sha256':'a'*64},'start_tick':10,'end_tick':20})


def test_enclosed_shot_keeps_original_history_and_ends_query_at_shot_end(tmp_path):
    c=capture([active(1,7,parent=56,int1=1),active(12,7,int1=2),active(20,7,entry_type=2)])
    store(tmp_path,c);e=capture_io.retain_capture(tmp_path,advertise(c),10,20,'a'*64,'b'*64)
    before=copy.deepcopy(e);scene={'metadata':{'demo_sha256':'a'*64},'start_tick':13,'end_tick':19}
    result=fold_retained(e,scene)
    assert e==before and result['through_tick']==19
    assert result['states'][0]['first_active_tick']==1
    assert result['states'][0]['header']['int1']==2
    assert result['source_capture_sha256']==e['provenance']['capture_sha256']
    assert fold_retained(e,scene,through_tick=9)['states'][0]['header']['int1']==1
    with pytest.raises(ValueError,match='shot end'):fold_retained(e,scene,through_tick=20)


@pytest.mark.parametrize('start,end',[(9,19),(11,21),(True,19),(11,19.5),(19,11)])
def test_shot_view_does_not_expand_retime_or_invert_capture(tmp_path,start,end):
    e=envelope(tmp_path)
    with pytest.raises(ValueError):
        fold_retained(e,{'metadata':{'demo_sha256':'a'*64},'start_tick':start,'end_tick':end})
