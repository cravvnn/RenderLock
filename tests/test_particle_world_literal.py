import copy

import pytest

from agr.particle_world_literal import POLICY, control_points, literal_controls, request_header

RESOURCE = 'particles/example/world_effect.vpcf'


def transform(tick, order, point, position):
    return dict(type='UPDATE_TRANSFORM', tick=tick, order=order, data=dict(
        type='GAME_PARTICLE_MANAGER_EVENT_UPDATE_TRANSFORM', index=7,
        update_particle_transform=dict(control_point=point, position=dict(zip('xyz', position)))))


def scene_and_instance():
    creation = dict(particle_name_index='123', attach_type=8, entity_handle=0xffffff, cluster=False)
    events = [dict(type='CREATE', tick=100, order=10, data=dict(type='GAME_PARTICLE_MANAGER_EVENT_CREATE', index=7,
                                                               create_particle=dict(creation))),
              transform(100, 11, 0, (1, 2, 3)), transform(100, 12, 2, (4, 5, 6)), transform(102, 13, 2, (7, 8, 9)),
              dict(type='RELEASE', tick=102, order=14, data=dict(type='GAME_PARTICLE_MANAGER_EVENT_RELEASE', index=7))]
    instance = dict(id='7:1', index=7, generation=1, resource=RESOURCE, resource_hash='123', created_tick=100,
                    entity_id=None, entity_handle=0xffffff, selection_scope='unattached_custom_origin',
                    involved_entity_ids=[], involved_entity_handles=[], create=dict(creation), events=events,
                    destroy_tick=None, release_ticks=[102])
    scene = dict(start_tick=90, end_tick=103, tick_rate=64, metadata=dict(demo_sha256='ab' * 32))
    return scene, instance


def test_any_resource_with_literal_points_is_admitted_and_labelled_unverified():
    scene, instance = scene_and_instance()
    header = request_header(scene, instance)
    assert header['policy'] == POLICY and header['resource'] == RESOURCE and header['client_sha256'] is None
    assert sorted(literal_controls(scene, instance)) == [0, 2]


def test_every_tick_carries_the_latest_recorded_value_of_each_point():
    scene, instance = scene_and_instance()
    rows = control_points(scene, instance)
    assert [row['tick'] for row in rows] == [100, 101, 102, 103]
    assert all(row['position'] == [1.0, 2.0, 3.0] for row in rows)
    assert [row['extra_control_points'][0]['position'] for row in rows] == [[4.0, 5.0, 6.0]] * 2 + [[7.0, 8.0, 9.0]] * 2
    assert all(row['extra_control_points'][0]['index'] == 2 for row in rows)


@pytest.mark.parametrize('change, message', [
    (lambda s, i: i.update(entity_handle=5), 'unattached creation'),
])
def test_shapes_outside_the_literal_profile_stay_refused(change, message):
    scene, instance = scene_and_instance()
    instance = copy.deepcopy(instance)
    change(scene, instance)
    with pytest.raises(ValueError, match=message):
        literal_controls(scene, instance)


def test_effect_born_before_the_clip_is_simulated_from_its_true_birth():
    scene, instance = scene_and_instance()
    scene.update(start_tick=102, end_tick=110)
    rows = control_points(scene, instance)
    assert rows[0]['tick'] == 100 and rows[-1]['tick'] == 110


def test_effect_that_never_sets_point_zero_has_it_at_the_world_origin():
    from agr.particle_world_literal import placed_positions
    scene, instance = scene_and_instance()
    instance['events'].pop(1)
    points = literal_controls(scene, instance)
    assert points[0][0]['position'] == [0.0, 0.0, 0.0] and points[0][0]['assumed_world_origin'] is True
    assert control_points(scene, instance)[0]['position'] == [0.0, 0.0, 0.0]
    # Only positions away from the world origin say where the effect is.
    points[2][0]['position'] = [4000.0, 5.0, 6.0]
    assert placed_positions(points) == [[4000.0, 5.0, 6.0]]


def test_a_model_assigned_to_a_control_point_is_not_applied_and_is_named():
    from agr.particle_world_literal import models_not_applied, without_model_events
    scene, instance = scene_and_instance()
    instance['events'].insert(2, dict(type='SET_CONTROL_POINT_MODEL', tick=100, order=11, data=dict(
        type='GAME_PARTICLE_MANAGER_EVENT_SET_CONTROL_POINT_MODEL', index=7,
        set_control_point_model=dict(control_point=0, model_name='models/example.vmdl'))))
    instance['events'][2]['order'] = 11
    for order, event in enumerate(instance['events'], 10):
        event['order'] = order
    assert sorted(literal_controls(scene, instance)) == [0, 2]
    assert models_not_applied(instance) == ['models/example.vmdl']
    assert all(event['type'] != 'SET_CONTROL_POINT_MODEL' for event in without_model_events(instance)['events'])


def test_simulator_gets_an_explicit_world_origin_only_when_the_game_set_none():
    from agr.particle_world_literal import simulator_events
    scene, instance = scene_and_instance()
    assert simulator_events(instance, literal_controls(scene, instance)) == instance['events']
    instance['events'].pop(1)
    events = simulator_events(instance, literal_controls(scene, instance))
    assert events[1]['type'] == 'UPDATE_TRANSFORM' and events[1]['data']['update_particle_transform'] == dict(
        control_point=0, position=dict(x=0.0, y=0.0, z=0.0))
    assert [event['order'] for event in events] == [20, 21, 24, 26, 28]
    assert [dict(event, order=None) for event in events[2:]] == [dict(event, order=None) for event in instance['events'][1:]]
