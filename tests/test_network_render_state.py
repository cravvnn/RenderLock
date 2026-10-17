import json
import pytest
from agr.network_render_state import render_state_track,attach_render_state


def test_exact_uint32_missing_samples_and_gaps_survive_metadata():
    entity=dict(id='85:334',samples=[dict(tick=1,network_render_state=dict(rgba8_packed=0x80402011,mode=0xffffffff)),dict(tick=2),dict(tick=4,network_render_state=dict(mode=0))])
    root={};report=attach_render_state(root,entity)
    track=json.loads(root['agr_network_render_state'])
    assert track==render_state_track(entity)
    assert track['samples'][0]['state']['rgba8_packed']==0x80402011
    assert track['samples'][1]['state'] is None
    assert [r['tick'] for r in track['samples']]==[1,2,4]
    assert report['available_samples']==2 and report['shader_applied'] is False


def test_older_exports_do_not_gain_fabricated_color():
    root={}
    assert attach_render_state(root,dict(id='x',samples=[dict(tick=1)]))==dict(status='not_recorded')
    assert root=={}


@pytest.mark.parametrize('state',[{},dict(mode=True),dict(rgba8_packed=-1),dict(mode=2**32),dict(unknown=0)])
def test_invalid_record_rejected_before_write(state):
    root={}
    with pytest.raises(ValueError):attach_render_state(root,dict(id='x',samples=[dict(tick=1,network_render_state=state)]))
    assert root=={}


def test_duplicate_tick_rejected():
    row=dict(tick=1,network_render_state=dict(mode=0))
    with pytest.raises(ValueError):render_state_track(dict(id='x',samples=[row,row]))
