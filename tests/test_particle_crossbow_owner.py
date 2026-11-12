import copy
import struct

import pytest

from agr import particle_crossbow_owner as owner
from agr.particle_snapshot_provider import digest


@pytest.fixture
def observed(tmp_path):
    handle = 16387
    entity = dict(id='3:1', samples=[dict(tick=t, position=[t, 0, 0]) for t in range(10, 13)],
        model_constraints=dict(schema_version=1, resource=owner.MODEL, resource_sha256=owner.MODEL_SHA256,
                               names=['root', 'crossbow'], parents=[-1, 0], definitions=[]))
    for name in ('model_file', 'skeleton_file', 'pose_file'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        entity[name] = str(path)
    instance = dict(id='8:1', index=8, generation=1, entity_id=entity['id'], entity_handle=handle,
                    resource=owner.RESOURCE, resource_hash='123', created_tick=10)
    instance['events'] = [dict(type='CREATE', tick=10, data=dict(create_particle=dict(
        entity_handle=handle, attach_type=3, particle_name_index='123')))]
    for cp, kind, index in [(0, 1, 0), (1, 5, 40), (2, 5, 13)]:
        instance['events'].append(dict(type='UPDATE_ENT', tick=10, data=dict(update_particle_ent=dict(
            control_point=cp, entity_handle=handle, attach_type=kind, attachment=index, include_wearables=True))))
    instance['events'].extend(dict(type='UPDATE_TRANSFORM', tick=t, data=dict(update_particle_transform=dict(
        control_point=5, position=dict(x=t-10)))) for t in range(10, 13))
    scene = dict(start_tick=10, end_tick=12, tick_rate=64, entities=[entity], particle_instances=[instance],
                 metadata=dict(demo_sha256='a'*64))
    raw = bytearray(88)
    runtime = 32771
    struct.pack_into('<i', raw, 4, 1)
    struct.pack_into('<3f', raw, 0x10, *[owner.UNSET]*3)
    struct.pack_into('<12f', raw, 0x1c, *owner.IDENTITY)
    struct.pack_into('<Q', raw, 0x50, runtime)
    collection = bytearray(0xe0)
    struct.pack_into('<Q', collection, 0x60, runtime)
    capture = dict(schema_version=1, policy=owner.POLICY, client_sha256=owner.CLIENT_SHA256,
        particles_sha256=owner.PARTICLES_SHA256, resource=owner.RESOURCE, entity_id=entity['id'], tick=10,
        demo_sha256='a'*64, raw_capture_sha256='b'*64, model=owner.MODEL, model_sha256=owner.MODEL_SHA256,
        coherent=True, identity_flags=0, model_method='stock_dynamic_component_skeleton_model_getter_v1',
        runtime_owner_handle=runtime, collection_owner_handle=runtime, skeleton_vtable_rva=42327240,
        body_component_type_index=10, body_component_offset=48, owner_srt=[10, 0, 0, 1, 0, 0, 0, 1],
        runtime_axes_rvas=owner.AXIS_RVAS.copy(), runtime_axes_raw=owner.AXES.copy(), runtime_axes_coherent=True,
        graph_coherence_policy='double_read_links_class_flags_v1', graph_coherent=True,
        child_model_inputs_complete=True, eligible_render_children=0, graph_nodes=2, graph_sha256='c'*64,
        cp0_record=raw.hex(), collection_record=collection.hex())
    return scene, instance, capture


def test_current_observation_binds_without_rewriting_events_or_aliasing_inputs(observed):
    scene, instance, capture = observed
    before = copy.deepcopy(instance)
    packet = owner.bind_capture(capture, scene, instance)
    header = owner.request_header(packet, scene, instance)
    assert header['creation_mode'] == 3 and header['observed_mode'] == 1
    assert [b['control_point'] for b in owner.bindings(scene, instance)] == [0, 1, 2, 5]
    owner.validate_response(dict(instances=[dict(id=instance['id'], crossbow_owner_provider=header)]), instance['id'], header)
    capture['owner_srt'][0] = 999
    assert packet['capture']['owner_srt'][0] == 10 and instance == before
    with pytest.raises(ValueError):
        owner.validate_response(dict(instances=[dict(id=instance['id'])]), instance['id'], header)


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('policy', 'old'), ('client_sha256', '2'*64), ('particles_sha256', '5'*64),
    ('demo_sha256', 'd'*64), ('resource', 'particles/other.vpcf'), ('entity_id', '3:2'), ('tick', 11),
    ('model_sha256', 'b'*64), ('coherent', False), ('identity_flags', 1), ('identity_flags', False),
    ('model_method', 'guessed'), ('runtime_owner_handle', 16387), ('collection_owner_handle', 32772),
    ('skeleton_vtable_rva', 0x285dc98), ('body_component_type_index', 11), ('body_component_offset', 64),
    ('owner_srt', [0, 0, 0, 2, 0, 0, 0, 1]), ('owner_srt', [0, 0, 0, 1, 0, 0, 0, .9]),
    ('owner_srt', [1e100, 0, 0, 1, 0, 0, 0, 1]), ('runtime_axes_raw', []),
    ('runtime_axes_coherent', False), ('graph_coherent', False), ('graph_coherence_policy', 'single_read'),
    ('child_model_inputs_complete', False), ('eligible_render_children', 1), ('eligible_render_children', False),
    ('graph_nodes', 65), ('graph_nodes', 0), ('graph_sha256', ''), ('raw_capture_sha256', ''),
    ('cp0_record', '00'*89), ('collection_record', '00'*224),
])
def test_current_capture_refuses_changed_semantics_even_with_new_digest(observed, field, value):
    scene, instance, capture = observed
    packet = owner.bind_capture(capture, scene, instance)
    packet['capture'][field] = value
    packet['content_sha256'] = digest({k:v for k,v in packet.items() if k != 'content_sha256'})
    with pytest.raises(ValueError):
        owner.validate_bound_capture(packet, scene, instance)


@pytest.mark.parametrize('offset,fmt,value', [(0, '<h', 1), (4, '<i', 3), (0x50, '<Q', 32772),
                                           (0x10, '<f', 0), (0x1c, '<f', 2)])
def test_native_cp_record_is_checked_not_just_its_claims(observed, offset, fmt, value):
    scene, instance, capture = observed
    raw = bytearray.fromhex(capture['cp0_record'])
    struct.pack_into(fmt, raw, offset, value)
    capture['cp0_record'] = raw.hex()
    with pytest.raises(ValueError):
        owner.bind_capture(capture, scene, instance)


@pytest.mark.parametrize('change', [
    lambda s, i: s['metadata'].update(demo_sha256='e'*64),
    lambda s, i: s['entities'][0]['samples'][0]['position'].__setitem__(0, 999),
    lambda s, i: s['entities'][0].update(model_tracks=[{}]),
    lambda s, i: i['events'][0]['data']['create_particle'].update(attach_type=2),
    lambda s, i: i['events'][0]['data']['create_particle'].update(particle_name_index='other'),
    lambda s, i: i['events'][2]['data']['update_particle_ent'].update(entity_handle=16388),
    lambda s, i: i['events'][2].update(tick=11),
    lambda s, i: i['events'][1]['data']['update_particle_ent'].update(offset_position=dict(x=1)),
    lambda s, i: i['events'][4]['data']['update_particle_transform'].update(control_point=0),
    lambda s, i: i['events'][4]['data']['update_particle_transform'].update(interpolation_interval=1),
])
def test_scene_or_history_changes_invalidate_the_bound_observation(observed, change):
    scene, instance, capture = observed
    packet = owner.bind_capture(capture, scene, instance)
    change(scene, instance)
    with pytest.raises(ValueError):
        owner.validate_bound_capture(packet, scene, instance)


def test_missing_capture_does_not_become_empty_supported_capture(observed):
    scene, instance, _ = observed
    with pytest.raises(ValueError):
        owner.validate_bound_capture(None, scene, instance)
