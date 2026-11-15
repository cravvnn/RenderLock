import pytest

from agr.particle_owned_broad import POLICY, broad_bindings, is_unverified, validate_response
from agr.particle_render_controls import render_contexts

OWNER = 14663736          # entity 56:895
OTHER = 4636730


def event(kind, order, tick=100, **data):
    return dict(type=kind, tick=tick, order=order, data=dict(type='GAME_PARTICLE_MANAGER_EVENT_' + kind, index=7, **data))


def instance(events, *, handle=OWNER, mode=3):
    creation = dict(particle_name_index='1', attach_type=mode, entity_handle=handle, cluster=False)
    destroy = next((e for e in events if e['type'] == 'DESTROY'), None)
    row = dict(id='7:1', index=7, generation=1, resource='particles/example.vpcf', resource_hash='1', created_tick=100,
               entity_id='56:895', entity_handle=handle, create=creation,
               events=[event('CREATE', 0, create_particle=dict(creation))] + events,
               destroy_tick=destroy['tick'] if destroy else None)
    if destroy:
        row['destroy_immediately'] = False
    return row


def bind(order, point, handle, mode, attachment, **extra):
    return event('UPDATE_ENT', order, update_particle_ent=dict(control_point=point, entity_handle=handle,
                                                               attach_type=mode, attachment=attachment, **extra))


def test_follow_creation_without_bindings_uses_the_owner_origin():
    rows = broad_bindings(instance([event('DESTROY', 1, tick=120, destroy_particle=dict(destroy_immediately=False))]),
                          90, 130)
    assert rows == [dict(control_point=0, provider='entity_origin', target_entity='56:895')]


def test_origin_attachment_and_literal_points_are_each_bound():
    rows = broad_bindings(instance([
        bind(1, 0, OWNER, 1, 0), bind(2, 1, OWNER, 5, 3),
        event('UPDATE_TRANSFORM', 3, update_particle_transform=dict(control_point=2, position=dict(x=1, y=2, z=3))),
    ], mode=2), 90, 130)
    assert [row['control_point'] for row in rows] == [0, 1, 2]
    assert rows[0]['provider'] == 'entity_origin' and rows[1]['attachment'] == 3
    assert rows[2]['provider'] == 'literal_transform' and rows[2]['updates'][0]['position'] == [1.0, 2.0, 3.0]


def test_point_on_an_entity_that_is_not_exported_holds_its_recorded_fallback():
    rows = broad_bindings(instance([
        bind(1, 0, OWNER, 5, 3), bind(2, 1, OTHER, 5, 3, fallback_position=dict(x=28.0, y=15.5, z=1340.0)),
    ], handle=OTHER, mode=2), 90, 130)
    assert rows[1]['provider'] == 'literal_transform' and rows[1]['other_entity_fallback'] is True
    assert rows[1]['updates'] == [dict(tick=100, control_point=1, position=[28.0, 15.5, 1340.0], rotation=[0, 0, 0, 1],
                                       interpolation_interval=0)]


@pytest.mark.parametrize('events, mode, message', [
    ([bind(1, 0, OWNER, 5, 3), bind(2, 1, OTHER, 5, 3)], 2, 'no recorded fallback'),
    ([bind(1, 0, OWNER, 5, 3, ), bind(2, 1, OWNER, 5, 3, offset_position=dict(x=1.0))], 2, 'offsets'),
    ([bind(1, 0, OWNER, 7, 0)], 2, 'binding mode 7'),
    ([], 2, 'no control point 0'),
    ([bind(1, 0, OWNER, 5, 3), bind(2, 1, OWNER, 5, 3, ) | dict(tick=101)], 2, 'after creation'),
])
def test_unsupported_shapes_stay_refused(events, mode, message):
    with pytest.raises(ValueError, match=message):
        broad_bindings(instance(events, mode=mode), 90, 130)


def test_response_must_acknowledge_the_provider_and_read_nothing_unsupplied():
    bindings = [dict(control_point=0, provider='entity_origin')]
    row = dict(id='7:1', owned_broad_provider=dict(policy=POLICY), control_point_indices=[0],
               unprovided_control_point_indices=[])
    validate_response(dict(instances=[row]), '7:1', bindings)
    assert is_unverified(row)
    with pytest.raises(ValueError, match='not recorded'):
        validate_response(dict(instances=[dict(row, unprovided_control_point_indices=[5])]), '7:1', bindings)
    with pytest.raises(ValueError, match='too old'):
        validate_response(dict(instances=[{k: v for k, v in row.items() if k != 'owned_broad_provider'}]), '7:1', bindings)


def test_delayed_child_with_no_particles_is_not_an_error():
    system = dict(system_path='root/6', system_age=-0.05, particles=[], control_points=[dict(index=0, position=[0, 0, 0])])
    simulation = dict(control_point_snapshot_policy=None)
    assert render_contexts(simulation) == {}
    from agr import particle_render_controls as controls
    row = dict(id='7:1', control_point_indices=[0], materialized_control_point_indices=[0],
               frames=[dict(tick=100, systems=[system])])
    simulation = dict(control_point_snapshot_policy=controls.SNAPSHOT_POLICY, instances=[row])
    contexts = render_contexts(simulation)
    assert contexts[('7:1', 'root/6', 100)]['age'] == 0.0


def test_broad_asset_collection_leaves_out_unbuildable_layers_instead_of_refusing():
    from agr.particle_render_assets import prepare_render_assets

    class Decoder:
        identity = 'decoder'

    class Provider:
        def resolve(self, name):
            import types
            return types.SimpleNamespace(sha256='ab' * 32)
    definitions = {'particles/example.vpcf': {'m_Renderers': [
        {'_class': 'C_OP_RenderSprites', 'm_vecTexturesInput': []},
        {'_class': 'C_OP_RenderModels', 'm_ModelList': [{'m_model': 'a.vmdl'}], 'm_unknown': 1},
        {'_class': 'C_OP_RenderDeferredLight'},
    ]}}
    assets = prepare_render_assets({'particles/example.vpcf'}, Decoder(), None, Provider(), {},
                                   definitions=definitions, omit_unsupported_layers=True)
    omitted = assets['omitted_layers']['particles/example.vpcf']
    assert sorted(omitted) == ['0', '1', '2']
    assert 'no texture' in omitted['0'] and 'model layer left out' in omitted['1'] and 'not supported' in omitted['2']


def test_point_on_another_exported_character_follows_that_character():
    rows = broad_bindings(instance([bind(1, 0, OWNER, 5, 3), bind(2, 1, OTHER, 1, 0)], mode=2), 90, 130,
                          exported={'56:895', '58:283'})
    assert rows[1] == dict(control_point=1, provider='entity_origin', target_entity='58:283')


def test_literal_first_set_after_creation_is_held_from_creation():
    late = event('UPDATE_TRANSFORM', 3, tick=104, update_particle_transform=dict(control_point=1, position=dict(x=9, y=8, z=7)))
    rows = broad_bindings(instance([late]), 90, 130)
    updates = rows[1]['updates']
    assert [u['tick'] for u in updates] == [100, 104] and updates[0]['backfilled'] is True
    assert updates[0]['position'] == updates[1]['position'] == [9.0, 8.0, 7.0]


CONFIGURED = {'m_controlPointConfigurations': [{'m_name': 'preview', 'm_drivers': [
    {'m_iControlPoint': 1, 'm_iAttachType': 'PATTACH_POINT_FOLLOW', 'm_attachmentName': 'umbrella_top_fx', 'm_entityName': 'self'},
    {'m_iControlPoint': 2, 'm_iAttachType': 'PATTACH_ABSORIGIN_FOLLOW', 'm_entityName': 'self'}]}]}


def configured_instance(**creation):
    row = instance([], mode=-1)
    row['create']['control_point_configuration'] = 'preview'
    row['events'][0]['data']['create_particle'].update(control_point_configuration='preview', **creation)
    return row


def test_named_configuration_supplies_the_bindings():
    rows = broad_bindings(configured_instance(), 90, 130, definition=CONFIGURED)
    assert rows[0] == dict(control_point=0, provider='entity_origin', target_entity='56:895', assumed=True)
    assert rows[1] == dict(control_point=1, provider='named_attachment', attachment_name='umbrella_top_fx',
                           target_entity='56:895', configuration='preview')
    assert rows[2]['provider'] == 'entity_origin' and rows[2]['configuration'] == 'preview'


def test_named_configuration_without_its_definition_is_checked_at_binding_level_only():
    assert [row['control_point'] for row in broad_bindings(configured_instance(), 90, 130)] == [0]


@pytest.mark.parametrize('definition, message', [
    ({}, 'no control point configuration named preview'),
    ({'m_controlPointConfigurations': [{'m_name': 'preview', 'm_drivers': [
        {'m_iAttachType': 'PATTACH_POINT_FOLLOW', 'm_attachmentName': 'a', 'm_vecOffset': [1.0, 0, 0]}]}]}, 'offset'),
    ({'m_controlPointConfigurations': [{'m_name': 'preview', 'm_drivers': [
        {'m_iAttachType': 'PATTACH_WORLDORIGIN'}]}]}, 'driver mode PATTACH_WORLDORIGIN'),
])
def test_unsupported_configurations_stay_refused(definition, message):
    with pytest.raises(ValueError, match=message):
        broad_bindings(configured_instance(), 90, 130, definition=definition)
