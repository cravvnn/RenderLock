"""Unattached replay effects must survive hero selection without invented owners."""
import copy
import pytest
from agr.particle_events import (compile_particle_timeline,is_world_custom_origin_candidate,
    WORLD_CUSTOM_ORIGIN_SCOPE,ParticleEventError,slice_particle_instances)
from agr.particle_control_events import compile_control_events,require_owned_event_provider
from agr.vendor.resource_hash import resource_id

PATH='particles/test/world_burst.vpcf'
HANDLE=(333<<14)|84
ENTITIES=[dict(id='84:333',entity_index=84,entity_serial=333)]
def event(tick,kind,**args):return dict(demo_tick=tick,data=dict(type='GAME_PARTICLE_MANAGER_EVENT_'+kind,index=9,**args))
def create(tick=10,**changes):
 data=dict(particle_name_index=str(resource_id(PATH)),attach_type=8,entity_handle=0xffffff,cluster=False);data.update(changes)
 return event(tick,'CREATE',create_particle=data)
def transform(tick=10):return event(tick,'UPDATE_TRANSFORM',update_particle_transform=dict(control_point=0,position=dict(x=1,y=2,z=3)))
def compile_rows(rows,**kwargs):return compile_particle_timeline(rows,[PATH],10,20,tick_rate=30,entities=ENTITIES,**kwargs)

def test_world_record_survives_only_explicit_policy_and_stays_unowned():
 rows=[create(),transform(),event(10,'RELEASE')]
 assert compile_rows(rows)['instances']==[]
 result=compile_rows(rows,include_world_custom_origin=True);item=result['instances'][0]
 assert item['entity_id']is None and item['entity_handle']==0xffffff
 assert item['selection_scope']==WORLD_CUSTOM_ORIGIN_SCOPE
 assert item['involved_entity_ids']==[] and item['involved_entity_handles']==[]
 assert item['release_ticks']==[10]and item['destroy_tick']is None
 assert result['summary']['world_custom_origin_candidates']==1

def test_missing_world_controls_are_retained_for_diagnostics():
 item=compile_rows([create()],include_world_custom_origin=True)['instances'][0]
 assert item['selection_scope']==WORLD_CUSTOM_ORIGIN_SCOPE and len(item['events'])==1

@pytest.mark.parametrize('changes',[{'attach_type':5},{'attach_type':None},{'entity_handle':HANDLE+(1<<14)},
 {'entity_handle':None},{'cluster':True},{'cluster':0}])
def test_other_entity_or_unqualified_creation_is_not_world(changes):
 assert compile_rows([create(**changes)],include_world_custom_origin=True)['instances']==[]

@pytest.mark.parametrize('target',[HANDLE+(1<<14),(334<<14)|85])
def test_later_unselected_binding_prevents_world_alias(target):
 rows=[create(),transform(),event(10,'UPDATE_ENT',update_particle_ent=dict(control_point=1,entity_handle=target,attach_type=5,attachment=1))]
 assert compile_rows(rows,include_world_custom_origin=True)['instances']==[]

def test_primary_selected_binding_keeps_existing_hero_contract():
 rows=[create(),event(10,'UPDATE_ENT',update_particle_ent=dict(control_point=0,entity_handle=HANDLE,attach_type=5,attachment=1))]
 old=compile_rows(rows);new=compile_rows(rows,include_world_custom_origin=True)
 assert old['instances']==new['instances']
 assert new['instances'][0]['entity_id']=='84:333'
 assert 'selection_scope'not in new['instances'][0]

def test_world_release_then_index_reuse_keeps_both_generations_and_tails():
 rows=[create(8),transform(8),event(9,'RELEASE'),create(10),transform(10),event(10,'RELEASE')]
 items=compile_rows(rows,include_world_custom_origin=True)['instances']
 assert [i['id']for i in items]==['9:1','9:2']
 assert items[0]['created_tick']==8 and items[0]['destroy_tick']is None
 assert items[0]['index_reused_tick']==10 and items[0]['release_ticks']==[9]

def test_immediate_old_world_destruction_still_culls():
 rows=[create(8),event(9,'DESTROY',destroy_particle=dict(destroy_immediately=True))]
 assert compile_rows(rows,include_world_custom_origin=True)['instances']==[]

def test_slice_keeps_world_scope_and_original_birth():
 item=compile_rows([create(8),transform(8),event(9,'RELEASE')],include_world_custom_origin=True)['instances'][0]
 sliced=slice_particle_instances([item],12,18,ENTITIES)[0]
 assert sliced['entity_id']is None and sliced['selection_scope']==WORLD_CUSTOM_ORIGIN_SCOPE
 assert sliced['created_tick']==8 and sliced['created_frame']==-3
 assert sliced['events'][1]['data']==item['events'][1]['data']

def test_retention_does_not_admit_owned_control_provider():
 item=compile_rows([create(),transform(),event(10,'RELEASE')],include_world_custom_origin=True)['instances'][0]
 controls=compile_control_events(item,10,20)
 with pytest.raises(ValueError,match='full entity identity'):require_owned_event_provider(item,controls,always=True)

def test_missing_handle_provenance_cannot_be_marked_world():
 item=compile_rows([create()],include_world_custom_origin=True)['instances'][0]
 for key in ('involved_entity_ids','involved_entity_handles'):
  changed=copy.deepcopy(item);changed.pop(key);assert not is_world_custom_origin_candidate(changed)

def test_world_policy_requires_boolean():
 with pytest.raises(ParticleEventError,match='boolean policy'):compile_rows([create()],include_world_custom_origin=1)
