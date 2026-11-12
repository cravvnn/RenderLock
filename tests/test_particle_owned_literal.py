from copy import deepcopy
import pytest
from agr.particle_owned_literal import bindings,header,RESOURCE,collect
from agr.vendor.resource_hash import resource_id
from agr.recorded_effect_simulation import recorded_binding_profile,fixed_point_bindings


def fixture():
    create=dict(particle_name_index=str(resource_id(RESOURCE)),attach_type=2,entity_handle=14663736,cluster=False)
    events=[]
    for number,(kind,payload)in enumerate((('CREATE',dict(create_particle=create)),
        ('UPDATE_TRANSFORM',dict(update_particle_transform=dict(control_point=0,position=dict(x=1.,y=2.,z=3.)))),
        ('UPDATE_TRANSFORM',dict(update_particle_transform=dict(control_point=1,position=dict(x=240.,y=0.,z=1.)))),
        ('RELEASE',{}))):
        events.append(dict(type=kind,tick=100,order=10+number,data=dict(type='GAME_PARTICLE_MANAGER_EVENT_'+kind,index=7,**payload)))
    return dict(id='7:1',index=7,generation=1,entity_handle=14663736,entity_id='56:895',resource=RESOURCE,
        resource_hash=str(resource_id(RESOURCE)),create=create,created_tick=100,events=events,destroy_tick=None,release_ticks=[100])


def test_exact_original_literals_are_selected_without_relaxing_point_follow():
    instance=fixture();before=deepcopy(instance);model,root,provider=recorded_binding_profile(instance)
    assert not model and not root and provider is bindings
    result=provider(instance,100,132)
    assert [b['control_point']for b in result]==[0,1]
    assert all(b['provider']=='literal_transform'for b in result)
    assert result[0]['updates'][0]['position']==[1.,2.,3.] and instance==before
    with pytest.raises(ValueError,match='attachment bindings'):fixed_point_bindings(instance,100,132)


@pytest.mark.parametrize('mutation',[
    lambda i:i.update(entity_id='56:894'),lambda i:i.update(entity_handle=0xffffff),
    lambda i:i.update(generation=True),lambda i:i.update(id='7:2'),
    lambda i:i.update(selection_scope='unattached_world_custom_origin'),
    lambda i:i.update(resource=RESOURCE.replace('flashbang','other')),
    lambda i:i.update(create={}),lambda i:i['create'].update(attach_type=5),
    lambda i:i['create'].update(cluster=True),lambda i:i['create'].update(unknown=0),
    lambda i:i['events'][1].update(tick=101),lambda i:i['events'][2].update(order=14),
    lambda i:i['events'].pop(),lambda i:i['events'][1]['data'].update(index=True),
    lambda i:i['events'][1]['data']['update_particle_transform'].update(control_point=1),
    lambda i:i['events'][2]['data']['update_particle_transform'].update(control_point=0),
    lambda i:i['events'][1]['data']['update_particle_transform'].update(interpolation_interval=.1),
    lambda i:i['events'][1]['data']['update_particle_transform'].update(orientation=dict(x=0,y=0,z=0,w=1)),
    lambda i:i['events'][1]['data']['update_particle_transform']['position'].update(x=float('nan')),
    lambda i:i['events'][1]['data']['update_particle_transform']['position'].update(x=1e9),
    lambda i:i['events'][1]['data']['update_particle_transform']['position'].pop('x'),
    lambda i:i['events'][3]['data'].update(release_particle_index=dict(unknown=True)),
    lambda i:i.update(destroy_tick=100),lambda i:i.update(release_ticks=[]),
])
def test_unknown_or_incomplete_original_inputs_refuse(mutation):
    instance=fixture();mutation(instance)
    with pytest.raises(ValueError):bindings(instance,100,132)


@pytest.mark.parametrize('start,end',[(101,132),(0,99),(0,5000),(True,132)])
def test_birth_and_range_are_not_invented(start,end):
    with pytest.raises(ValueError):bindings(fixture(),start,end)


def test_header_binds_full_source_replay_range_and_installed_profile(tmp_path):
    scene=dict(start_tick=100,end_tick=132,tick_rate=64,metadata=dict(demo_sha256='a'*64))
    original=header(scene,fixture());changed=fixture();changed['events'][1]['data']['update_particle_transform']['position']['x']=2.
    assert header(scene,changed)!=original
    scene['metadata']['demo_sha256']='b'*64;assert header(scene,fixture())!=original
    class Provider:install_path=tmp_path
    with pytest.raises(ValueError,match='native profile'):collect(scene,fixture(),Provider())
    scene['tick_rate']=60
    with pytest.raises(ValueError,match='64Hz'):header(scene,fixture())
