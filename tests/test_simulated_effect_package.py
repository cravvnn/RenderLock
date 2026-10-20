import copy
import pytest
from agr.simulated_effect_package import scene_identity, validate_package, model_constraints_identity


@pytest.fixture
def fixture(tmp_path):
    files = {}
    for field in ('model_file', 'skeleton_file', 'pose_file'):
        p = tmp_path / field
        p.write_bytes(field.encode())
        files[field] = str(p)
    scene = {'start_tick': 10, 'end_tick': 12, 'tick_rate': 64, 'entities': [
        {'id': '83:906', 'hero_id': 11, 'samples': [{'tick': 10, 'position': [1, 2, 3]}], **files}]}
    package = {'schema_version': 1, 'backend': 'simulated_states_v1', 'entity_ids': ['83:906'],
               'scene_identity': scene_identity(scene, ['83:906']), 'coverage_scope': 'one event only',
               'simulation': {'schema_version': 1, 'backend': 'fixture', 'warnings': [],
                 'start_tick': 10, 'end_tick': 12, 'tick_rate': 64,
                 'instances': [{'id': '1:1', 'entity_id': '83:906', 'frames': [{'tick': t} for t in range(10,13)]}]}}
    return scene, package


def test_valid_prepared_package_and_changed_track_rejected(fixture):
    scene, package = fixture
    validate_package(package, scene)
    scene['entities'][0]['samples'][0]['position'][0] += 1
    with pytest.raises(ValueError, match='do not match'):
        validate_package(package, scene)


def test_changed_pose_bytes_rejected(fixture):
    from pathlib import Path
    scene, package = fixture
    Path(scene['entities'][0]['pose_file']).write_bytes(b'new pose')
    with pytest.raises(ValueError, match='do not match'):
        validate_package(package, scene)


def test_explicit_render_root_basis_invalidates_prepared_particles(fixture):
    scene, package = fixture
    original = package['scene_identity']
    entity = scene['entities'][0]
    entity['render_root_yaw_degrees'] = 180
    with pytest.raises(ValueError, match='do not match'):
        validate_package(package, scene)
    package['scene_identity'] = scene_identity(scene, ['83:906'])
    validate_package(package, scene)
    entity['render_root_yaw_degrees'] = 0
    with pytest.raises(ValueError, match='do not match'):
        validate_package(package, scene)
    del entity['render_root_yaw_degrees']
    assert scene_identity(scene, ['83:906']) == original


@pytest.mark.parametrize('change', ['attachment', 'lifecycle', 'resource'])
def test_recorded_particle_events_are_part_of_cached_scene_identity(fixture, change):
    scene, package = fixture
    scene['particle_instances'] = [{'id': '1:1', 'entity_id': '83:906',
        'resource': 'particles/test.vpcf', 'created_tick': 10, 'events': [
            {'type': 'UPDATE_ENT', 'tick': 10, 'data': {'update_particle_ent': {'attachment': 3}}}]}]
    package['scene_identity'] = scene_identity(scene, ['83:906'])
    validate_package(package, scene)
    row = scene['particle_instances'][0]
    if change == 'attachment':row['events'][0]['data']['update_particle_ent']['attachment'] = 4
    if change == 'lifecycle':row['events'].append({'type':'DESTROY','tick':11})
    if change == 'resource':row['resource'] = 'particles/other.vpcf'
    with pytest.raises(ValueError, match='do not match'):
        validate_package(package, scene)


@pytest.mark.parametrize('change', ['warnings', 'ticks', 'binding'])
def test_invalid_simulation_rejected(fixture, change):
    scene, package = fixture
    sim = package['simulation']
    if change == 'warnings':sim['warnings'] = ['unsupported operator']
    if change == 'ticks':sim['instances'][0]['frames'].reverse()
    if change == 'binding':sim['instances'][0]['entity_id'] = '83:907'
    with pytest.raises(ValueError):validate_package(package, scene)


@pytest.mark.parametrize('change', [
    lambda frames:frames.pop(0), lambda frames:frames.pop(1), lambda frames:frames.pop(),
    lambda frames:frames[1].update(tick=11.0)])
def test_saved_simulation_cannot_interpolate_over_missing_or_coerced_ticks(fixture, change):
    scene, package = fixture
    change(package['simulation']['instances'][0]['frames'])
    with pytest.raises(ValueError, match='tick order/range'):
        validate_package(package, scene)


@pytest.mark.parametrize('observed', [[0,7], [0.0], [False], [0,0]])
def test_cached_states_with_unknown_or_coerced_control_points_are_rejected(fixture, observed):
    scene, package = fixture
    row = package['simulation']['instances'][0]
    row.update(control_point_indices=[0], materialized_control_point_indices=observed)
    with pytest.raises(ValueError, match='unqualified control-point dependencies'):
        validate_package(package, scene)


def test_older_request_bound_cache_cannot_bypass_dependency_validation(fixture):
    scene, package = fixture
    package['simulation_request_sha256'] = package['simulation']['request_sha256'] = 'a'*64
    with pytest.raises(ValueError, match='dependency provenance; regenerate'):
        validate_package(package, scene)


@pytest.mark.parametrize('policy',['agr_set_child_position_inputs_v1','agr_authored_child_controls_v2'])
def test_qualified_authored_control_points_survive_prepared_package_validation(fixture,policy):
    scene,package=fixture
    row=package['simulation']['instances'][0]
    row.update(control_point_indices=[0],materialized_control_point_indices=[0,3],
        control_point_input_policy=policy,declared_control_point_inputs=[0],
        authored_control_point_indices=[3],unprovided_control_point_indices=[])
    validate_package(package,scene)


@pytest.mark.parametrize('field,value',[
    ('control_point_input_policy',None),('control_point_input_policy','unknown'),
    ('declared_control_point_inputs',[0,3]),('authored_control_point_indices',[0,3]),
    ('authored_control_point_indices',[3,3]),('authored_control_point_indices',[True]),
    ('unprovided_control_point_indices',[3]),('unprovided_control_point_indices',[7]),
    ('unprovided_control_point_indices',None),('materialized_control_point_indices',[0,3,7]),
])
def test_prepared_packages_cannot_hide_missing_or_earlier_unprovided_inputs(fixture,field,value):
    scene,package=fixture
    row=package['simulation']['instances'][0]
    row.update(control_point_indices=[0],materialized_control_point_indices=[0,3],
        control_point_input_policy='agr_authored_child_controls_v2',declared_control_point_inputs=[0],
        authored_control_point_indices=[3],unprovided_control_point_indices=[])
    row[field]=value
    with pytest.raises(ValueError,match='unqualified control-point dependencies'):
        validate_package(package,scene)


@pytest.mark.parametrize('change', ['definition','hierarchy','model','missing','ack'])
def test_model_particles_bind_constraints_not_only_original_pose_bytes(fixture,change):
    scene,package=fixture
    model={'schema_version':1,'resource':'models/hero.vmdl','resource_sha256':'a'*64,
           'names':['root','child'],'parents':[-1,0],'definitions':[{'strength':1}]}
    scene['entities'][0]['model_constraints']=model
    package['scene_identity']=scene_identity(scene,['83:906'])
    package['model_provider']={'entity_id':'83:906','resource':model['resource'],
        'resource_sha256':model['resource_sha256'],'bone_count':2,
        'model_constraints_sha256':model_constraints_identity(model)}
    validate_package(package,scene)
    if change=='definition':model['definitions'][0]['strength']=.5
    if change=='hierarchy':model['parents'][1]=-1
    if change=='model':model['resource_sha256']='b'*64
    if change=='missing':del scene['entities'][0]['model_constraints']
    if change=='ack':del package['model_provider']['model_constraints_sha256']
    assert (package['scene_identity']==scene_identity(scene,['83:906'])) == (change=='ack')
    with pytest.raises(ValueError,match='constraints|rig inputs'):validate_package(package,scene)


def test_fixed_point_particles_bind_authored_constraints_without_a_model_provider(fixture):
    scene,package=fixture
    scene['entities'][0]['model_constraints']={'schema_version':1,'names':['root'],
        'parents':[-1],'definitions':[{'strength':1}]}
    package['scene_identity']=scene_identity(scene,['83:906'])
    validate_package(package,scene)
    scene['entities'][0]['model_constraints']['definitions'][0]['strength']=.5
    with pytest.raises(ValueError,match='rig inputs'):validate_package(package,scene)


def test_secondary_motion_changes_invalidate_bone_attachment_particles(fixture,tmp_path):
    scene,package=fixture
    path=tmp_path/'cloth.npz';path.write_bytes(b'first cloth bake')
    scene['entities'][0]['secondary_motion_file']=str(path)
    package['scene_identity']=scene_identity(scene,['83:906'])
    validate_package(package,scene)
    path.write_bytes(b'revised cloth bake')
    with pytest.raises(ValueError,match='rig inputs'):validate_package(package,scene)
