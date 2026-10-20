import copy
import json
from types import SimpleNamespace
import pytest

from agr.recorded_effect_simulation import (fixed_attachment, fixed_point_bindings,
    validate_control_response, substitute_seed, validate_seed_response, SEED_POLICY,
    regeneration_bindings, validate_model_response, validate_regeneration_definitions,
    REGEN_RESOURCE, validate_request_response, validate_event_response, root_origin_bindings)
from agr.cli import parser


def instance():
    handle=(3<<14)|17
    return {'id':'23:2','entity_id':'17:3','entity_handle':handle,'created_tick':100,
            'events':[{'type':'CREATE','tick':100},
                      {'type':'UPDATE_ENT','tick':100,'data':{'update_particle_ent':{
                          'control_point':0,'attach_type':5,'entity_handle':handle,'attachment':7}}},
                      {'type':'RELEASE','tick':100}]}


def test_substitute_seed_survives_range_changes_but_binds_instance_and_demo():
    data={'start_tick':100,'end_tick':120,'metadata':{'demo_sha256':'a'*64}}
    row=dict(instance(),resource='particles/test.vpcf')
    seed=substitute_seed(data,row)
    assert 0 <= seed < 2**32
    assert substitute_seed(dict(data,end_tick=200),copy.deepcopy(row)) == seed
    assert substitute_seed(data,dict(row,id='24:2')) != seed
    assert substitute_seed({'metadata':{'demo_sha256':'b'*64}},row) != seed


def test_old_probe_cannot_silently_ignore_seed_request():
    with pytest.raises(ValueError,match='rebuild ParticleProbe'):
        validate_seed_response({'instances':[{'id':'23:2'}]},123)
    response={'seed_policy':SEED_POLICY,'seed':123,'instances':[{'system_seeds':[
        {'path':'root','resource':'particles/test.vpcf','seed':17}]}]}
    validate_seed_response(response,123)
    with pytest.raises(ValueError,match='requested substitute seed'):
        validate_seed_response(response,124)
    response['instances'][0]['system_seeds'][0]['seed']=4096
    with pytest.raises(ValueError,match='seed provenance'):
        validate_seed_response(response,123)


def request_response():
    source = dict(instance(), resource='particles/test.vpcf')
    response = dict(schema_version=1, request_sha256='a'*64, start_tick=100, end_tick=102,
        tick_rate=64, instances=[dict(id=source['id'], resource=source['resource'], birth=100,
                                    frames=[dict(tick=t) for t in range(100,103)])])
    return source, response


def test_response_confirms_exact_request_resource_birth_and_dense_ticks():
    source, response = request_response()
    validate_request_response(response, source, 100, 102, 64, 'a'*64)


@pytest.mark.parametrize('change', [
    lambda r:r.pop('request_sha256'),
    lambda r:r.update(request_sha256='b'*64),
    lambda r:r.update(tick_rate=64.0),
    lambda r:r.update(start_tick=99),
    lambda r:r['instances'][0].update(resource='particles/other.vpcf'),
    lambda r:r['instances'][0].update(birth=101),
    lambda r:r['instances'][0].update(birth=100.0),
    lambda r:r['instances'][0]['frames'].pop(1),
    lambda r:r['instances'][0]['frames'][1].update(tick=True),
    lambda r:r['instances'][0]['frames'].reverse(),
])
def test_stale_or_incomplete_simulator_states_are_withheld(change):
    source, response = request_response(); change(response)
    with pytest.raises(ValueError):
        validate_request_response(response, source, 100, 102, 64, 'a'*64)


def test_release_does_not_destroy_fixed_point_follow_binding():
    assert fixed_attachment(instance(),100,120)['attachment']==7


def literal_point_instance():
    row=instance()
    row['index']=23
    row['generation']=2
    row['events'][0]['data']=dict(index=23,create_particle=dict(entity_handle=row['entity_handle'],attach_type=5,cluster=False))
    row['events'].insert(2,dict(type='UPDATE_TRANSFORM',tick=100,
        data=dict(index=23,update_particle_transform=dict(control_point=2,position=dict(x=.82)))))
    row['events'].append(dict(type='UPDATE_TRANSFORM',tick=102,
        data=dict(index=23,update_particle_transform=dict(control_point=2,position=dict(x=3,y=4,z=5)))))
    return row


def test_literal_provider_preserves_packet_space_and_all_updates_without_replacing_attachments():
    row=literal_point_instance()
    bindings=fixed_point_bindings(row,100,120)
    assert [binding['control_point'] for binding in bindings]==[0,2]
    assert bindings[0]['attachment']==7
    assert bindings[1]['provider']=='literal_transform'
    assert [(update['tick'],update['position']) for update in bindings[1]['updates']]==[
        (100,[.8199999928474426,0,0]),(102,[3,4,5])]


@pytest.mark.parametrize('change,match',[
    (lambda row:row['events'][2]['data']['update_particle_transform'].update(control_point=0),'cannot replace'),
    (lambda row:row['events'][2].update(tick=101),'ordered'),
    (lambda row:row['events'][2]['data']['update_particle_transform'].update(interpolation_interval=.25),'setter qualification'),
    (lambda row:row['events'][1]['data']['update_particle_ent'].update(attach_type=1),'exact-serial'),
])
def test_literal_controls_cannot_expand_entity_provider_or_timeline_scope(change,match):
    row=literal_point_instance();change(row)
    with pytest.raises(ValueError,match=match):fixed_point_bindings(row,100,120)


def test_literal_control_point_requires_creation_time_value():
    row=literal_point_instance()
    row['events'].pop(2)
    with pytest.raises(ValueError,match='creation-time transform'):fixed_point_bindings(row,100,120)


@pytest.mark.parametrize('change',[
    lambda row:row.update(generation=1),
    lambda row:row.update(entity_id='17:4'),
    lambda row:row['events'][0]['data']['create_particle'].update(entity_handle=17),
    lambda row:row['events'][0]['data']['create_particle'].update(attach_type=0),
    lambda row:row['events'][0]['data']['create_particle'].update(cluster=True),
    lambda row:row['events'][0]['data'].pop('create_particle'),
])
def test_new_control_policy_requires_full_owned_creation_identity(change):
    row=literal_point_instance();change(row)
    with pytest.raises(ValueError,match='identity|creation provider'):fixed_point_bindings(row,100,120)


def root_point_instance():
    row=literal_point_instance()
    row['events'][1]['data']['update_particle_ent'].update(attach_type=1,attachment=0,include_wearables=True)
    return row


def test_native_root_origin_provider_bypasses_unnamed_wearable_search_and_retains_literal_cp2():
    bindings=root_origin_bindings(root_point_instance(),100,120)
    assert [binding['provider'] for binding in bindings]==['root_origin','literal_transform']
    assert bindings[0]['entity_handle']==(3<<14)|17
    assert bindings[1]['updates'][0]['position']==[.8199999928474426,0,0]


@pytest.mark.parametrize('change',[
    lambda row:row['events'][1]['data']['update_particle_ent'].update(attachment=3),
    lambda row:row['events'][1]['data']['update_particle_ent'].update(entity_handle=17),
    lambda row:row['events'][1]['data']['update_particle_ent'].update(fallback_position={'x':.1}),
    lambda row:row['events'][1]['data']['update_particle_ent'].update(offset_position={'x':1}),
    lambda row:row['events'][1].update(tick=101),
])
def test_native_root_provider_does_not_infer_named_or_offset_semantics(change):
    row=root_point_instance();change(row)
    with pytest.raises(ValueError):root_origin_bindings(row,100,120)


def test_valid_parent_attachment_cannot_replace_client_child_inventory():
    row=root_point_instance()
    named=copy.deepcopy(row['events'][1])
    named['data']['update_particle_ent'].update(control_point=1,attach_type=5,attachment=4)
    row['events'].insert(2,named)
    with pytest.raises(ValueError,match='client-child attachment inventory'):
        root_origin_bindings(row,100,120)


def test_model_and_literal_response_requires_both_cp_provenance_and_exact_bone_count():
    row=dict(id='23:2',control_point_indices=[0,2],materialized_control_point_indices=[0,2],model_bone_count=227)
    validate_model_response(dict(instances=[row]),'23:2',227,[0,2])
    with pytest.raises(ValueError,match='confirm the requested model bones'):
        validate_model_response(dict(instances=[row]),'23:2',227,[0])
    row['materialized_control_point_indices']=[0,1,2]
    with pytest.raises(ValueError,match='confirm the requested model bones'):
        validate_model_response(dict(instances=[row]),'23:2',227,[0,2])


@pytest.mark.parametrize('immediate',[False,True])
def test_fixed_attachment_lifecycle_records_destroy_and_requires_new_helper_confirmation(immediate):
    from agr.particle_control_events import compile_control_events
    row=literal_point_instance()
    row.update(destroy_tick=104,destroy_immediately=immediate)
    row['events'].append(dict(type='DESTROY',tick=104,
        data=dict(index=23,destroy_particle=dict(destroy_immediately=immediate))))
    assert len(fixed_point_bindings(row,100,120))==2
    expected=compile_control_events(row,100,120)
    response=dict(instances=[dict(id=row['id'],replay_event_policy='agr_recorded_control_lifecycle_v1',
        literal_transforms_applied=expected['literal_transforms'],destroy_applied=expected['destroy'],
        release_ticks=expected['release_ticks'])])
    validate_event_response(response,row,100,120)
    for field in ('replay_event_policy','literal_transforms_applied','destroy_applied','release_ticks'):
        stale=copy.deepcopy(response);stale['instances'][0].pop(field)
        with pytest.raises(ValueError,match='rebuild ParticleProbe'):
            validate_event_response(stale,row,100,120)
    stale=copy.deepcopy(response);stale['instances'][0]['destroy_applied']['destroy_immediately']=not immediate
    with pytest.raises(ValueError,match='rebuild ParticleProbe'):validate_event_response(stale,row,100,120)


@pytest.mark.parametrize('change', [
    lambda r:r.update(created_tick=100.0),
    lambda r:r.update(entity_handle=str(r['entity_handle'])),
    lambda r:r.update(entity_handle=float(r['entity_handle'])),
    lambda r:r['events'][1].update(tick=100.0),
    lambda r:r['events'][-1].update(tick=121),
    lambda r:r['events'][0].update(tick=99),
    lambda r:r['events'][1]['data']['update_particle_ent'].update(attach_type=5.0),
    lambda r:r['events'][1]['data']['update_particle_ent'].update(entity_handle=float(r['entity_handle'])),
])
def test_fixed_bindings_reject_coercible_identity_and_events_outside_history(change):
    row = instance(); change(row)
    with pytest.raises(ValueError):
        fixed_point_bindings(row, 100, 120)


def multiple_points():
    row = instance()
    extra = copy.deepcopy(row['events'][1])
    extra['data']['update_particle_ent'].update(control_point=3, attachment=2)
    row['events'].insert(1, extra)
    return row


def test_multiple_point_bindings_are_sorted_and_preserve_attachment_identity():
    bindings = fixed_point_bindings(multiple_points(),100,120)
    assert [(b['control_point'],b['attachment']) for b in bindings] == [(0,7),(3,2)]
    with pytest.raises(ValueError): fixed_attachment(multiple_points(),100,120)


@pytest.mark.parametrize('field', ['offset_position', 'offset_angles'])
def test_zero_proto_offsets_preserve_the_attachment_binding(field):
    for value in ({}, {'x':0.0}, {'x':0, 'y':-0.0, 'z':0.0}):
        row=multiple_points()
        row['events'][1]['data']['update_particle_ent'][field]=value
        assert len(fixed_point_bindings(row,100,120)) == 2


@pytest.mark.parametrize('field', ['offset_position', 'offset_angles'])
@pytest.mark.parametrize('value', [
    {'x':1}, {'z':1e-12}, {'x':float('nan')}, {'y':float('inf')},
    {'x':True}, {'x':'0'}, {'pitch':0}, None, [0,0,0],
])
def test_nonzero_or_malformed_offsets_cannot_silently_move_an_effect(field,value):
    row=multiple_points()
    row['events'][1]['data']['update_particle_ent'][field]=value
    with pytest.raises(ValueError,match='Unsupported attachment '+field):
        fixed_point_bindings(row,100,120)


@pytest.mark.parametrize('field,value', [
    ('control_point',0), ('control_point',64), ('control_point',-1),
    ('control_point',True), ('entity_handle',17), ('attach_type',1),
    ('attachment',0), ('attachment',True),
])
def test_extra_cp_cannot_relax_ownership_or_attachment_guards(field,value):
    row=multiple_points()
    row['events'][1]['data']['update_particle_ent'][field]=value
    with pytest.raises(ValueError): fixed_point_bindings(row,100,120)


def test_old_or_incomplete_probe_response_withholds_multiple_cp_output():
    bindings=fixed_point_bindings(multiple_points(),100,120)
    for indices in (None,[0],[0,2],[0,3,4],[3,0]):
        response={'instances':[{'id':'23:2','control_point_indices':indices}]}
        with pytest.raises(ValueError,match='confirm all requested'):
            validate_control_response(response,'23:2',bindings)
    validate_control_response({'instances':[{'id':'23:2','control_point_indices':[0,3],
        'materialized_control_point_indices':[0,3]}]},'23:2',bindings)
    validate_control_response({'instances':[{'id':'23:2','control_point_indices':[0],
        'materialized_control_point_indices':[0]}]},'23:2',bindings[:1])


@pytest.mark.parametrize('materialized', [None, [0,7], [0,0], [7], [0.0], [False]])
def test_unsupplied_default_control_points_cannot_produce_a_supported_instance(materialized):
    response={'instances':[{'id':'23:2','control_point_indices':[0],
        'materialized_control_point_indices':materialized}]}
    with pytest.raises(ValueError,match='unqualified control-point dependencies'):
        validate_control_response(response,'23:2',fixed_point_bindings(instance(),100,120))


def authored_child_response():
    return {'id':'23:2','control_point_indices':[0],
        'materialized_control_point_indices':[0,3],
        'control_point_input_policy':'agr_set_child_position_inputs_v1',
        'declared_control_point_inputs':[0], 'authored_control_point_indices':[3],
        'unprovided_control_point_indices':[], 'model_bone_count':227}


@pytest.mark.parametrize('policy',['agr_set_child_position_inputs_v1','agr_authored_child_controls_v2'])
def test_qualified_child_positions_acknowledge_authored_dependencies(policy):
    row=authored_child_response();row['control_point_input_policy']=policy
    response={'instances':[row]}
    validate_control_response(response,'23:2',fixed_point_bindings(instance(),100,120))
    validate_model_response(response,'23:2',227)


@pytest.mark.parametrize('field,value',[
    ('control_point_input_policy',None),
    ('control_point_input_policy','unknown'),
    ('declared_control_point_inputs',None),
    ('declared_control_point_inputs',[0,3]),
    ('unprovided_control_point_indices',None),
    ('unprovided_control_point_indices',[3]),
    ('unprovided_control_point_indices',[7]),
    ('authored_control_point_indices',None),
    ('authored_control_point_indices',[0,3]),
    ('authored_control_point_indices',[3,3]),
    ('authored_control_point_indices',[True,3]),
    ('authored_control_point_indices',[64]),
    ('materialized_control_point_indices',[0,3,7]),
    ('materialized_control_point_indices',[0]),
])
def test_authored_points_do_not_hide_unsupplied_or_read_before_write_dependencies(field,value):
    row=authored_child_response();row[field]=value
    response={'instances':[row]}
    with pytest.raises(ValueError,match='unqualified control-point dependencies'):
        validate_control_response(response,'23:2',fixed_point_bindings(instance(),100,120))
    with pytest.raises(ValueError,match='confirm the requested model bones'):
        validate_model_response(response,'23:2',227)


@pytest.mark.parametrize('change',[
    lambda i:i.update(created_tick=99),
    lambda i:i.update(created_tick=121),
    lambda i:i.update(entity_id='17:4'),
    lambda i:i['events'].append({'type':'DESTROY','tick':110}),
    lambda i:i['events'].append(copy.deepcopy(i['events'][1])),
    lambda i:i['events'][1].update(tick=101),
    lambda i:i['events'][1]['data']['update_particle_ent'].update(control_point=1),
    lambda i:i['events'][1]['data']['update_particle_ent'].update(entity_handle=1),
    lambda i:i['events'][1]['data']['update_particle_ent'].update(attach_type=1),
])
def test_ambiguous_or_unsupported_history_is_not_silently_simulated(change):
    value=instance();change(value)
    with pytest.raises(ValueError):fixed_attachment(value,100,120)


def test_runtime_is_explicit_and_default_export_keeps_existing_backend():
    args=parser().parse_args(['convert','--segment','in.json','--output','out.blend'])
    assert args.particle_probe is None and args.dotnet is None
    args=parser().parse_args(['convert','--segment','in.json','--output','out.blend',
                             '--particle-probe','probe.dll','--dotnet','dotnet.exe'])
    assert str(args.particle_probe)=='probe.dll' and args.dotnet=='dotnet.exe'


def test_attachment_preparation_preserves_history_without_running_old_renderer(tmp_path,monkeypatch):
    from agr.installed_effects import prepare_installed_effects
    from agr.effect_assets import EffectAssetAdapter
    from agr.vendor.resource_hash import resource_id
    model='models/test.vmdl_c'
    provider=SimpleNamespace(resources=[model])
    def unexpected_dump(*args):raise AssertionError('Attachment mode must not classify or prune particle definitions')
    decoder=SimpleNamespace(dump=unexpected_dump)
    monkeypatch.setattr(EffectAssetAdapter,'attachments',lambda *args:[{
        'm_nInfluences':1,'m_influenceWeights':[1],'m_influenceNames':['root']}]*7)
    current=multiple_points();current['resource']='particles/test.vpcf'
    earlier=copy.deepcopy(current);earlier['id']='22:1';earlier['created_tick']=50
    scene={'entities':[{'id':'17:3','model_handle':str(resource_id(model[:-2]))}],
           'particle_instances':[earlier,current],'start_tick':100,'end_tick':120,'tick_rate':64}
    source=tmp_path/'segment.json';source.write_text(json.dumps(scene))
    report=prepare_installed_effects(source,tmp_path/'assets',provider,decoder,log=lambda _:None,attachments_only=True)
    assert report['status']=='prepared'
    assert '17:3/7' in report['attachments']
    assert '17:3/2' in report['attachments']
    assert report['prepared_layer_count']==0
    assert json.loads(source.read_text())['particle_instances']==[earlier,current]


def regeneration():
    value=instance()
    value['resource']=REGEN_RESOURCE
    value['events'][0]['data']={'create_particle':{
        'entity_handle':value['entity_handle'],'attach_type':2,'cluster':False}}
    root=value['events'][1]['data']['update_particle_ent']
    root.update(attachment=0,attach_type=1,include_wearables=True)
    extra=copy.deepcopy(value['events'][1])
    extra['data']['update_particle_ent'].update(control_point=1,attachment=3,attach_type=5,
        fallback_position={'x':425.,'y':-1157.,'z':423.})
    value['events'].insert(2,extra)
    return value


def test_regeneration_preserves_unused_named_cp_but_requires_root_provider():
    bindings=regeneration_bindings(regeneration(),100,120)
    assert [(b['control_point'],b['attach_type'],b['attachment']) for b in bindings]==[(0,1,0),(1,5,3)]
    row=regeneration();del row['events'][2]
    assert len(regeneration_bindings(row,100,120))==1
    # The profile must not silently broaden ordinary attachment eligibility.
    with pytest.raises(ValueError):fixed_point_bindings(regeneration(),100,120)


@pytest.mark.parametrize('change',[
    lambda i:i.update(resource='particles/other.vpcf'),
    lambda i:i.update(entity_id='17:4'),
    lambda i:i.update(entity_handle='49169'),
    lambda i:i.update(created_tick=99),
    lambda i:i.update(created_tick=121),
    lambda i:i['events'][0]['data']['create_particle'].update(cluster=True),
    lambda i:i['events'][0]['data']['create_particle'].update(entity_handle=17),
    lambda i:i['events'].append(copy.deepcopy(i['events'][0])),
    lambda i:i['events'].append({'type':'DESTROY','tick':110}),
    lambda i:i['events'][1].update(tick=101),
    lambda i:i['events'][2].update(tick=101),
    lambda i:i['events'].pop(1),
])
def test_regeneration_rejects_unqualified_lifecycle_or_identity(change):
    row=regeneration();change(row)
    with pytest.raises(ValueError):regeneration_bindings(row,100,120)


@pytest.mark.parametrize('index,field,value',[
    (1,'attachment',3),(1,'attach_type',2),(1,'attach_type',True),
    (1,'fallback_position',{'x':0.01}),(1,'offset_position',{'z':1}),
    (1,'entity_handle',17),(1,'include_wearables',1),
    (2,'control_point',2),(2,'control_point',True),(2,'control_point',0),
    (2,'entity_handle',17),(2,'fallback_position',{'x':float('nan')}),
    (2,'unexpected_mode',True),(2,'attachment',0),
])
def test_regeneration_does_not_guess_changed_provider_semantics(index,field,value):
    row=regeneration();row['events'][index]['data']['update_particle_ent'][field]=value
    with pytest.raises(ValueError):regeneration_bindings(row,100,120)


def test_changed_child_definition_cannot_gain_an_ignored_cp_dependency(monkeypatch):
    import hashlib
    import agr.recorded_effect_simulation as module
    definitions={'root':{'m_Children':[{'m_ChildRef':'child'}]},
                 'child':{'operator':{'control_point':0}}}
    hashes={name:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
             allow_nan=False).encode()).hexdigest() for name,value in definitions.items()}
    monkeypatch.setattr(module,'REGEN_DEFINITIONS',hashes)
    validate_regeneration_definitions(definitions)
    changed=copy.deepcopy(definitions);changed['child']['operator']['control_point']=1
    with pytest.raises(ValueError,match='definition changed'):validate_regeneration_definitions(changed)
    with pytest.raises(ValueError,match='incomplete or changed'):validate_regeneration_definitions({'root':definitions['root']})


@pytest.mark.parametrize('response',[
    {}, {'model_bone_count':0}, {'model_bone_count':True}, {'model_bone_count':226},
    {'model_bone_count':227,'control_point_indices':[0,1]},
    {'model_bone_count':227,'id':'23:3'},
])
def test_old_or_mismatched_model_provider_is_withheld(response):
    row={'id':'23:2','control_point_indices':[0]};row.update(response)
    with pytest.raises(ValueError,match='confirm the requested model bones'):
        validate_model_response({'instances':[row]},'23:2',227)


def test_complete_model_provider_acknowledgement():
    validate_model_response({'instances':[{
        'id':'23:2','control_point_indices':[0],'materialized_control_point_indices':[0],
        'model_bone_count':227}]},'23:2',227)
