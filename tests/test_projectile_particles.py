import copy
import pytest
from agr.projectile_particles import CLASS,POLICY,PREFIX,RESOURCE,QUALIFIED_CLIENT_SHA256S,controls,request,validate_capture
from agr.particle_events import particle_resource_lookup


def fixture():
    owner=(283<<14)|58
    fields={PREFIX+'m_hParent':0xffffff,PREFIX+'m_flScale':1,PREFIX+'m_vecOrigin.m_nOutsideWorld':0,
        PREFIX+'m_angRotation':[0,90,0],'m_hOwnerEntity':owner,'m_hParticleDef':int(next(iter(particle_resource_lookup([RESOURCE])))),
        'm_fEffects':0,'m_MoveType':11,'m_flTimeScale':1,'m_bNoInterpolate':False,
        'm_vInitialPosition':[10,20,30],'m_vInitialVelocity':[1,0,0]}
    for axis,value in zip('XYZ',[10,20,30]):fields[PREFIX+'m_vecOrigin.m_cell'+axis]=64;fields[PREFIX+'m_vecOrigin.m_vec'+axis]=value
    track=dict(id='100:7',class_=CLASS,first_seen_tick=9,removed_tick=12,samples=[dict(tick=t,active=True,fields=copy.deepcopy(fields)) for t in range(9,12)])
    track['class']=track.pop('class_')
    data=dict(start_tick=10,end_tick=12,tick_rate=64,entities=[dict(id='58:283')],
        metadata=dict(demo_sha256='a'*64,source_world_half=32768,coordinate_system=dict(unit_scale=.0254,origin=[0,0,0],world_yaw_degrees=0)))
    from agr.replay_capture import canonical_sha256
    value=dict(schema_version=1,policy=POLICY,requested_range=dict(start_tick=9,end_tick=12,inclusive=True),
        coverage=dict(availability='available',reason=None,origin='signon',first_observed_tick=1,last_observed_tick=12,
            tracks=1,samples=3,scope='Network observations'),tracks=[track])
    value['coverage']['class']=CLASS
    capture=dict(availability='available',reason=None,capture=value,provenance=dict(demo_sha256='a'*64,
        helper_sha256='b'*64,start_tick=9,end_tick=12,tick_range_inclusive=True,sidecar_filename='network_projectiles.json',
        sidecar_sha256='c'*64,capture_sha256=canonical_sha256(value)))
    return track,data,capture


def test_projectile_preserves_earlier_creation_and_stops_at_last_live_sample():
    track,data,capture=fixture();validate_capture(capture,data)
    plan,owner=request(track,data,'gameinfo.gi',123)
    assert owner=='58:283' and plan['start_tick']==10 and plan['end_tick']==11
    assert plan['instances'][0]['created_tick']==9
    assert [p['tick'] for p in plan['instances'][0]['control_points']]==[9,10,11]
    assert plan['instances'][0]['control_points'][0]['position']==[10,20,30]
    assert 'events' not in plan['instances'][0] # No fabricated particle-manager lifecycle.
    assert plan['instances'][0]['control_points'][0]['rotation']==pytest.approx([0,0,2**-.5,2**-.5])


@pytest.mark.parametrize('change',[
    lambda t:t.update(id='100:07'),lambda t:t.update(removed_tick=13),
    lambda t:t['samples'].pop(0),lambda t:t['samples'][1].update(tick=True),
    lambda t:t['samples'][1].update(active=False),
    lambda t:t['samples'][1]['fields'].update(m_hOwnerEntity=(282<<14)|58),
    lambda t:t['samples'][1]['fields'].update(m_hParticleDef=1),
    lambda t:t['samples'][1]['fields'].pop(PREFIX+'m_vecOrigin.m_cellX'),
    lambda t:t['samples'][1]['fields'].update({PREFIX+'m_hParent':123}),
    lambda t:t['samples'][1]['fields'].update({PREFIX+'m_flScale':2}),
    lambda t:t['samples'][1]['fields'].update(m_bNoInterpolate=True),
    lambda t:t['samples'][1]['fields'].update({PREFIX+'m_angRotation':[0,float('nan'),0]}),
])
def test_incomplete_or_rebound_projectile_is_rejected(change):
    track,data,_=fixture();change(track)
    with pytest.raises(ValueError):controls(track,data)


def test_capture_rejects_duplicate_ids_and_changed_world_basis():
    track,data,capture=fixture();capture['capture']['tracks'].append(copy.deepcopy(track))
    with pytest.raises(ValueError):validate_capture(capture,data)
    capture['capture']['tracks'].pop();data['metadata']['coordinate_system']['world_yaw_degrees']=90
    with pytest.raises(ValueError,match='coordinate'):validate_capture(capture,data)


def bound_package(tmp_path):
    from agr.projectile_particles import CLIENT_SHA256,SCOPE,digest
    from agr.simulated_effect_package import scene_identity
    track,data,capture=fixture();data['network_projectiles']=capture
    for field in ('model_file','skeleton_file','pose_file'):
        path=tmp_path/field;path.write_bytes(field.encode());data['entities'][0][field]=str(path)
    plan,owner=request(track,data,'gameinfo.gi',123)
    instance=dict(id=plan['instances'][0]['id'],resource=RESOURCE,birth=9,entity_id=owner,
        materialized_control_point_indices=[0],model_bone_count=0,literal_transforms_applied=[],
        destroy_applied=None,release_ticks=[],frames=[dict(tick=t) for t in (10,11)])
    simulation=dict(schema_version=1,backend='test-binding-only',warnings=[],request_sha256=digest(plan),
        seed=123,seed_policy='agr_substitute_sha256_v1',start_tick=10,end_tick=11,tick_rate=64,instances=[instance])
    return dict(schema_version=1,backend='simulated_projectile_flight_v2',projectile_provider=dict(
        client_sha256=CLIENT_SHA256,entity_id=track['id'],track_sha256=digest(track),capture_sha256=capture['provenance']['capture_sha256'],
        capture_provenance_sha256=digest(capture['provenance'])),projectile_request=plan,
        entity_ids=[owner],scene_identity=scene_identity(data,[owner]),simulation=simulation,
        simulation_request_sha256=digest(plan),coverage_scope=SCOPE),data


@pytest.mark.parametrize('client_sha256',sorted(QUALIFIED_CLIENT_SHA256S))
def test_projectile_package_binds_exact_owner_request_and_replay_files(tmp_path,client_sha256):
    from agr.projectile_particles import validate_package
    package,data=bound_package(tmp_path);package['projectile_provider']['client_sha256']=client_sha256
    validate_package(package,data)
    from pathlib import Path
    Path(data['entities'][0]['pose_file']).write_bytes(b'changed pose')
    with pytest.raises(ValueError,match='differs'):validate_package(package,data)


@pytest.mark.parametrize('client_sha256',sorted(QUALIFIED_CLIENT_SHA256S))
@pytest.mark.parametrize('change',[
    lambda p:p['projectile_request']['instances'][0]['control_points'][0]['position'].__setitem__(0,99),
    lambda p:p.update(entity_ids=['56:895']),
    lambda p:p['projectile_provider'].update(client_sha256='0'*64),
    lambda p:p['projectile_provider'].update(track_sha256='0'*64),
    lambda p:p['simulation'].update(seed_policy='unknown'),
    lambda p:p['simulation']['instances'][0]['frames'].pop(),
    lambda p:p['simulation']['instances'][0].update(entity_id='56:895'),
    lambda p:p['simulation']['instances'][0].update(destroy_applied=11),
    lambda p:p['simulation']['instances'][0].update(materialized_control_point_indices=[0,1]),
    lambda p:p.update(coverage_scope='complete'),
])
def test_projectile_package_rejects_stale_or_invented_inputs(tmp_path,change,client_sha256):
    from agr.projectile_particles import validate_package
    package,data=bound_package(tmp_path);package['projectile_provider']['client_sha256']=client_sha256;change(package)
    with pytest.raises(ValueError):validate_package(package,data)


def test_projectile_only_capture_reaches_normal_binding_preflight():
    from agr.recorded_effect_simulation import preflight_recorded_bindings
    track,data,capture=fixture();data.update(network_projectiles=capture,particle_instances=[],effects={})
    report=preflight_recorded_bindings(data)
    assert report['status']=='possible'and report['possible_instances']==1
    assert report['checked_instances']==1


def test_selected_tracks_does_not_drop_unknown_owner_or_mutate_capture():
    from agr.projectile_particles import selected_tracks
    from agr.replay_capture import canonical_sha256
    track,data,capture=fixture();before=copy.deepcopy(capture)
    assert selected_tracks(capture,data)==[track]and capture==before
    data['entities']=[{'id':'1:1'}];assert selected_tracks(capture,data)==[]
    track['samples'][0]['fields'].pop('m_hOwnerEntity')
    capture['provenance']['capture_sha256']=canonical_sha256(capture['capture'])
    assert selected_tracks(capture,data)==[track]
    with pytest.raises(ValueError,match='handle'):controls(track,data)


@pytest.mark.parametrize('field',[PREFIX+'m_flScale',PREFIX+'m_vecOrigin.m_nOutsideWorld','m_fEffects','m_flTimeScale'])
def test_boolean_numeric_motion_fields_are_not_qualified(field):
    track,data,_=fixture();track['samples'][0]['fields'][field]=field.endswith(('Scale','TimeScale'))
    with pytest.raises(ValueError):controls(track,data)


@pytest.mark.parametrize('field',['capture_sha256','capture_provenance_sha256'])
def test_projectile_package_binds_retained_capture_provenance(tmp_path,field):
    from agr.projectile_particles import validate_package
    package,data=bound_package(tmp_path);package['projectile_provider'][field]='0'*64
    with pytest.raises(ValueError,match='provenance'):validate_package(package,data)


def test_normal_coverage_merge_keeps_prior_rejections():
    from agr.recorded_projectile_effects import merge_projectile_report
    keys=('candidate_instances','selected_recorded_instances','attempted_instances','rendered_instances','objects',
          'particle_tracks','point_lights','pre_shot_rendered_instances')
    prior={'occurrences':1,'reason':'manager failure'}
    report=dict.fromkeys(keys,1);report.update(instances=[{'id':'manager'}],unsupported=[prior],
        candidate_by_birth_scope={'in_output':1},unsupported_by_birth_scope={'in_output':1},limitations=['prior limit'])
    merge_projectile_report(report,dict(selected_recorded_instances=2,rendered_instances=1,objects=3,
        instances=[{'id':'projectile'}],unsupported=[{'occurrences':1,'reason':'projectile failure'}],
        candidate_by_birth_scope={'in_recovered_history':2},unsupported_by_birth_scope={'in_recovered_history':1},coverage_scope='in flight only'))
    assert report['objects']==4 and report['candidate_instances']==3 and report['rendered_instances']==2
    assert report['unsupported'][0]is prior and len(report['unsupported'])==2
    assert report['unsupported_by_birth_scope']=={'in_output':1,'in_recovered_history':1}
    assert report['limitations']==['prior limit','in flight only']


@pytest.mark.parametrize('change',[
    lambda p:p.update(schema_version=True),
    lambda p:p['simulation'].update(schema_version=True),
    lambda p:p['simulation'].update(start_tick=10.),
    lambda p:p['simulation'].update(seed=123.),
    lambda p:p['simulation'].update(warnings={}),
    lambda p:p['simulation']['instances'][0].update(birth=9.),
    lambda p:p['simulation']['instances'][0].update(model_bone_count=False),
    lambda p:p['simulation']['instances'][0]['frames'][0].update(tick=10.),
])
def test_projectile_response_does_not_coerce_typed_fields(tmp_path,change):
    from agr.projectile_particles import validate_package
    package,data=bound_package(tmp_path);change(package)
    with pytest.raises(ValueError):validate_package(package,data)


def test_particle_resource_requires_recorded_uint64_not_numeric_text():
    track,data,_=fixture();track['samples'][0]['fields']['m_hParticleDef']=str(track['samples'][0]['fields']['m_hParticleDef'])
    with pytest.raises(ValueError):controls(track,data)


def test_additive_capture_does_not_invalidate_unrelated_cached_scene_identity(tmp_path):
    from agr.simulated_effect_package import scene_identity
    package,data=bound_package(tmp_path);original=scene_identity(data,['58:283'])
    data.pop('network_projectiles')
    assert scene_identity(data,['58:283'])==original


def test_cropped_view_does_not_require_future_owner_state():
    track,data,capture=fixture();data['end_tick']=10
    track['samples'][-1]['fields']['m_hOwnerEntity']=(9<<14)|8
    plan,owner=request(track,data,'gameinfo.gi',123)
    assert owner=='58:283'and plan['end_tick']==10
    assert [p['tick']for p in plan['instances'][0]['control_points']]==[9,10]
    data['end_tick']=11
    with pytest.raises(ValueError,match='owner'):request(track,data,'gameinfo.gi',123)


@pytest.mark.parametrize('module_exists',[False,True])
def test_missing_or_unknown_platform_profile_is_per_instance_unsupported(tmp_path,module_exists):
    from agr.recorded_projectile_effects import build_projectile_effects
    track,data,capture=fixture();data['network_projectiles']=capture
    install=tmp_path/'game-install'
    if module_exists:
        module=install/'game/citadel/bin/win64/client.dll';module.parent.mkdir(parents=True);module.write_bytes(b'unknown')
    runtime=dict(install_path=str(install),output_dir=str(tmp_path/'outputs'))
    report=build_projectile_effects(data,None,None,runtime,None,None,None)
    assert report['selected_recorded_instances']==1 and report['attempted_instances']==report['rendered_instances']==0
    assert len(report['unsupported'])==1 and 'qualification'in report['unsupported'][0]['reason']
    assert not list((tmp_path/'outputs').rglob('*.plan.json'))
