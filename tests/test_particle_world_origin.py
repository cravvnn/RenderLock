"""World origins must not be coerced into hero ownership or owned stop policy."""
import copy
import pytest
from agr.particle_events import compile_particle_timeline
from agr.particle_world_origin import (RESOURCE,POLICY,request_header,literal_birth,
    control_points,world_identity,validate_response,validate_definitions)
from agr.vendor.resource_hash import resource_id
from agr.particle_control_events import compile_control_events,require_owned_event_provider

def fixture():
 rows=[]
 for order,(kind,payload)in enumerate([
  ('CREATE',dict(create_particle=dict(particle_name_index=str(resource_id(RESOURCE)),attach_type=8,entity_handle=0xffffff,cluster=False))),
  ('UPDATE_TRANSFORM',dict(update_particle_transform=dict(control_point=0,position=dict(x=10.,y=-20.,z=30.)))),
  ('RELEASE',{})],17):
  rows.append(dict(demo_tick=100,order=order,data=dict(type='GAME_PARTICLE_MANAGER_EVENT_'+kind,index=9,**payload)))
 item=compile_particle_timeline(rows,[RESOURCE],100,104,tick_rate=64,entities=[],include_world_custom_origin=True)['instances'][0]
 scene=dict(start_tick=100,end_tick=104,tick_rate=64,entities=[],particle_instances=[item],
  metadata=dict(demo_sha256='a'*64,coordinate_system=dict(unit_scale=.0254,origin=[0,0,0],world_yaw_degrees=0)))
 return scene,item

def test_world_literal_profile_preserves_unowned_birth_and_release_tail():
 scene,item=fixture();literal=literal_birth(scene,item)
 assert literal['position']==[10,-20,30] and item['entity_id']is None
 assert request_header(scene,item)['policy']==POLICY
 assert [r['tick']for r in control_points(scene,item)]==[100,101,102,103,104]
 assert all(r['position']==[10,-20,30]for r in control_points(scene,item))
 with pytest.raises(ValueError,match='full entity identity'):
  require_owned_event_provider(item,compile_control_events(item,100,104),always=True)

@pytest.mark.parametrize('change',[
 lambda s,i:i.update(entity_id='9:1'),
 lambda s,i:i.update(entity_handle=0xffffffff),
 lambda s,i:i.update(resource='particles/another_world.vpcf'),
 lambda s,i:i.update(generation=2),
 lambda s,i:i.update(generation=2**31,id=f'9:{2**31}'),
 lambda s,i:i['create'].update(cluster=True),
 lambda s,i:i['create'].update(attach_type=5),
 lambda s,i:i['create'].update(fallback_position={}),
 lambda s,i:i['events'].pop(),
 lambda s,i:i['events'][2].update(type='DESTROY'),
 lambda s,i:i['events'][1].update(order=100),
 lambda s,i:[e.update(order=-3+n)for n,e in enumerate(i['events'])],
 lambda s,i:i['events'][1].update(tick=101),
 lambda s,i:i['events'][1]['data']['update_particle_transform'].update(control_point=1),
 lambda s,i:i['events'][1]['data']['update_particle_transform'].update(interpolation_interval=.1),
 lambda s,i:i['events'][1]['data']['update_particle_transform']['position'].pop('z'),
 lambda s,i:i['events'][1]['data']['update_particle_transform']['position'].update(x=float('nan')),
 lambda s,i:i['events'][1]['data']['update_particle_transform']['position'].update(z=131073),
 lambda s,i:i['events'][2]['data'].update(unrecognized=True),
 lambda s,i:i['events'][2]['data'].update(release_particle_index=dict(index=9)),
 lambda s,i:i['involved_entity_handles'].append(16777216),
 lambda s,i:s.update(start_tick=101),
 lambda s,i:s.update(end_tick=5000),
 lambda s,i:s.update(tick_rate=30),
 lambda s,i:s['metadata'].update(demo_sha256='missing'),
 lambda s,i:s['metadata']['coordinate_system'].update(world_yaw_degrees=90),
])
def test_unqualified_world_modes_and_lifecycles_are_not_admitted(change):
 scene,item=fixture();change(scene,item)
 with pytest.raises(ValueError):request_header(scene,item)

def test_world_identity_covers_source_generation_and_empty_hero_list():
 scene,item=fixture();before=world_identity(scene,item)
 altered=copy.deepcopy(item);altered['generation']=2;altered['id']='9:2'
 assert world_identity(scene,altered)!=before
 scene['metadata']['demo_sha256']='b'*64
 assert world_identity(scene,item)!=before

def test_old_helper_or_stale_world_acknowledgement_cannot_pass():
 scene,item=fixture();header=request_header(scene,item)
 good=dict(instances=[dict(id=item['id'],world_origin_provider=header)])
 validate_response(good,scene,item)
 for mutation in ({},dict(header,end_tick=103),dict(header,events_sha256='b'*64)):
  bad=dict(instances=[dict(id=item['id'],world_origin_provider=mutation)])
  with pytest.raises(ValueError,match='acknowledge'):validate_response(bad,scene,item)

def test_changed_definition_tree_is_not_treated_as_supported():
 with pytest.raises(ValueError,match='authored explosion tree'):validate_definitions({RESOURCE:{}})

def test_world_provider_refuses_missing_platform_qualification(tmp_path):
 from types import SimpleNamespace
 from agr.recorded_world_effects import qualify_installation
 with pytest.raises(ValueError,match='not yet qualified'):qualify_installation(SimpleNamespace(install_path=tmp_path))

def test_world_provider_refuses_changed_native_client(tmp_path):
 from types import SimpleNamespace
 from agr.recorded_world_effects import qualify_installation
 binary=tmp_path/'game/citadel/bin/win64/client.dll';binary.parent.mkdir(parents=True);binary.write_bytes(b'changed client')
 with pytest.raises(ValueError,match='client changed'):qualify_installation(SimpleNamespace(install_path=tmp_path))

def test_projected_defaults_require_matching_native_renderer(tmp_path,monkeypatch):
 import hashlib
 from types import SimpleNamespace
 import agr.recorded_world_effects as module
 binary=tmp_path/'game/citadel/bin/win64/client.dll';binary.parent.mkdir(parents=True);binary.write_bytes(b'qualified test client')
 client_hash=hashlib.sha256(binary.read_bytes()).hexdigest()
 monkeypatch.setattr(module,'NATIVE_PROFILES',{client_hash:module.PARTICLES_SHA256})
 provider=SimpleNamespace(install_path=tmp_path)
 with pytest.raises(ValueError,match='platform'):module.qualify_installation(provider)
 particles=tmp_path/'game/bin/win64/particles.dll';particles.parent.mkdir(parents=True);particles.write_bytes(b'changed renderer')
 with pytest.raises(ValueError,match='renderer changed'):module.qualify_installation(provider)
 monkeypatch.setattr(module,'NATIVE_PROFILES',{client_hash:hashlib.sha256(particles.read_bytes()).hexdigest()})
 assert module.qualify_installation(provider)==client_hash


def test_known_native_profiles_keep_distinct_request_and_source_identities():
 from agr.particle_world_origin import CLIENT_SHA256,LEGACY_CLIENT_SHA256
 scene,item=fixture()
 current=request_header(scene,item)
 legacy=request_header(scene,item,client_sha256=LEGACY_CLIENT_SHA256)
 assert current['client_sha256']==CLIENT_SHA256 and legacy['client_sha256']==LEGACY_CLIENT_SHA256
 assert current!=legacy
 assert world_identity(scene,item)!=world_identity(scene,item,client_sha256=LEGACY_CLIENT_SHA256)
 for unknown in ('a'*64,[],None):
  with pytest.raises(ValueError,match='native client profile'):
   request_header(scene,item,client_sha256=unknown)


def test_qualified_client_cannot_use_another_builds_renderer(tmp_path,monkeypatch):
 import hashlib
 from types import SimpleNamespace
 import agr.recorded_world_effects as module
 client=tmp_path/'game/citadel/bin/win64/client.dll';client.parent.mkdir(parents=True);client.write_bytes(b'known current fixture')
 renderer=tmp_path/'game/bin/win64/particles.dll';renderer.parent.mkdir(parents=True);renderer.write_bytes(b'legacy fixture renderer')
 client_sha=hashlib.sha256(client.read_bytes()).hexdigest()
 monkeypatch.setattr(module,'NATIVE_PROFILES',{client_sha:'0'*64,'1'*64:hashlib.sha256(renderer.read_bytes()).hexdigest()})
 with pytest.raises(ValueError,match='renderer changed'):
  module.qualify_installation(SimpleNamespace(install_path=tmp_path))
