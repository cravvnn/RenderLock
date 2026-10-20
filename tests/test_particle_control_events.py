"""Literal updates retain Source values and do not reinterpret RELEASE as death."""
from copy import deepcopy

import pytest

from agr.particle_control_events import compile_control_events


def instance():
    return dict(index=178,created_tick=1162,destroy_tick=None,destroy_immediately=None,
                release_ticks=[1162],events=[
                    dict(type='CREATE',tick=1162,order=253,data={'index':178}),
                    dict(type='UPDATE_ENT',tick=1162,order=254,data={'index':178,'update_particle_ent':{'control_point':0}}),
                    dict(type='UPDATE_TRANSFORM',tick=1162,order=256,data={'index':178,'update_particle_transform':
                         {'control_point':2,'position':{'x':.8199999928474426,'y':0.,'z':0.}}}),
                    dict(type='RELEASE',tick=1162,order=257,data={'index':178})])


def test_recorded_cp2_is_literal_with_native_absent_orientation_default():
    source=instance();before=deepcopy(source)
    plan=compile_control_events(source,1000,1300)
    assert plan['literal_transforms']==[dict(tick=1162,control_point=2,
        position=[.8199999928474426,0.,0.],rotation=[0,0,0,1],interpolation_interval=0)]
    assert plan['destroy'] is None and plan['release_ticks']==[1162]
    assert len(plan['entity_binding_events'])==1
    assert source==before


@pytest.mark.parametrize('immediate',[False,True])
def test_destroy_mode_and_tick_are_retained_exactly(immediate):
    source=instance()
    source.update(destroy_tick=1200,destroy_immediately=immediate)
    source['events'].append(dict(type='DESTROY',tick=1200,order=258,
        data={'index':178,'destroy_particle':{'destroy_immediately':immediate}}))
    assert compile_control_events(source,1000,1300)['destroy']==dict(tick=1200,destroy_immediately=immediate)


@pytest.mark.parametrize('field,value,match',[
    ('control_point',64,'out of range'),('control_point',True,'out of range'),
    ('interpolation_interval',.1,'setter qualification'),
    ('orientation',{'x':0,'y':0,'z':0,'w':0},'unit quaternion'),
    ('position',{'x':float('nan')},'components'),('position',None,'components'),
    ('position',{'x':1e40},'float32'),('unknown',1,'fields')])
def test_unqualified_transform_inputs_are_rejected(field,value,match):
    source=instance()
    source['events'][2]['data']['update_particle_transform'][field]=value
    with pytest.raises(ValueError,match=match):compile_control_events(source,1000,1300)


def test_missing_history_and_changed_event_summary_cannot_be_hidden():
    source=instance()
    with pytest.raises(ValueError,match='creation-time history'):compile_control_events(source,1200,1300)
    source['destroy_tick']=1200
    with pytest.raises(ValueError,match='summary'):compile_control_events(source,1000,1300)
    source=instance();source['events'][2]['order']=254
    with pytest.raises(ValueError,match='order'):compile_control_events(source,1000,1300)
    source=instance();source['events'][2]['type']='UPDATE_FWD'
    with pytest.raises(ValueError,match='Unsupported'):compile_control_events(source,1000,1300)


def test_packet_components_round_to_float32_before_comparison_with_native_helper():
    source=instance()
    source['events'][2]['data']['update_particle_transform']['position']={'x':.82}
    assert compile_control_events(source,1000,1300)['literal_transforms'][0]['position']==[.8199999928474426,0,0]
    source['events'][2]['data']['index']=178.0
    with pytest.raises(ValueError,match='index'):compile_control_events(source,1000,1300)
