"""Current-build Crossbow owner/birth input, separate from snapshot poses.

The acquisition producer must qualify native readers and coherent graph/body
observations. This immutable transport detects stale/cross-scene reuse; its
digests do not authenticate an edited capture. It does not launch the game or
infer a missing capture. Positive render children remain unsupported.
"""
import copy
import math
import struct

from .particle_snapshot_provider import RESOURCE, digest, _sha, _model_binding

POLICY = 'observed_crossbow_owner_birth_6861_v1'
CLIENT_SHA256 = 'd0341ce77398614af905ef8669d328fd4d45958282acef66789a250492e466b5'
PARTICLES_SHA256 = '46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f'
MODEL = 'models/heroes_wip/priest/priest.vmdl_c'
MODEL_SHA256 = '61d28824a8b99733e34164a7117ca84da91c82b7accc44432566798b7c99c432'
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.]
AXIS_RVAS = [67058656, 67058688, 67058736]
AXES = ['0000803f000000000000000000000000',
        '000000000000803f0000000000000000',
        '00000000000000000000803f00000000']
UNSET = struct.unpack('<f', bytes.fromhex('ffff7f7f'))[0]


def _identity(scene, instance):
    from .simulated_effect_package import scene_identity
    return scene_identity(dict(scene, particle_instances=[instance]), [instance['entity_id']])


def _hex(value, size):
    if not isinstance(value, str) or len(value) != 2 * size:
        raise ValueError('Crossbow raw record size differs from the qualified layout')
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError('Invalid Crossbow raw record') from exc
    if len(raw) != size:
        raise ValueError('Invalid Crossbow raw record length')
    return raw


def bindings(scene, instance):
    """Validate original CREATE3 / CP0 mode1 / named CP1/2 / literal CP5.

    No input event is rewritten. Birth equivalence and model/child selection
    remain independent checks in validate_capture; this function alone grants
    no export admission.
    """
    from .particle_control_events import compile_control_events
    from .particle_history import history_start
    from .recorded_effect_simulation import zero_binding_offsets
    birth = instance.get('created_tick')
    handle = instance.get('entity_handle')
    if (instance.get('resource') != RESOURCE or type(handle) is not int
            or not 0 < handle < 0xffffffff
            or instance.get('entity_id') != f'{handle & 0x3fff}:{handle >> 14}'
            or type(instance.get('index')) is not int or instance['index'] < 0
            or type(instance.get('generation')) is not int or instance['generation'] < 1
            or instance.get('id') != f"{instance['index']}:{instance['generation']}"):
        raise ValueError('Crossbow requires exact particle and full owner identities')
    controls = compile_control_events(instance, history_start(scene), scene['end_tick'])
    create = instance['events'][0]['data'].get('create_particle')
    if (not isinstance(create, dict) or type(create.get('attach_type')) is not int
            or create['attach_type'] != 3 or type(create.get('entity_handle')) is not int
            or create['entity_handle'] != handle or create.get('cluster', False) is not False
            or set(create) - {'attach_type', 'entity_handle', 'cluster', 'particle_name_index'}
            or not isinstance(instance.get('resource_hash'), str)
            or create.get('particle_name_index') != instance['resource_hash']):
        raise ValueError('Crossbow requires unchanged owned CREATE3 resource/history')
    _model_binding(scene, instance)
    result = {}
    for event in controls['entity_binding_events']:
        b = event['data'].get('update_particle_ent')
        if (not isinstance(b, dict) or type(b.get('control_point')) is not int
                or b['control_point'] not in (0, 1, 2) or b['control_point'] in result
                or type(b.get('entity_handle')) is not int or b['entity_handle'] != handle
                or event['tick'] != birth or type(b.get('attach_type')) is not int
                or type(b.get('attachment')) is not int
                or type(b.get('include_wearables', False)) is not bool
                or set(b) - {'control_point', 'entity_handle', 'attach_type', 'attachment',
                             'include_wearables', 'offset_position', 'offset_angles', 'fallback_position'}):
            raise ValueError('Crossbow requires unique own birth CP0/1/2 bindings')
        cp = b['control_point']
        if ((cp == 0 and (b['attach_type'], b['attachment']) != (1, 0))
                or (cp in (1, 2) and (b['attach_type'] != 5 or b['attachment'] <= 0))):
            raise ValueError('Unqualified Crossbow attachment mode')
        zero_binding_offsets(b)
        fallback = b.get('fallback_position', {})
        if (not isinstance(fallback, dict) or set(fallback) - set('xyz')
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       or (cp == 0 and v != 0) for v in fallback.values())):
            raise ValueError('Unqualified Crossbow fallback')
        result[cp] = dict(b, provider='root_origin' if cp == 0 else 'point_follow',
                          target_entity_id=instance['entity_id'])
    literals = controls['literal_transforms']
    if (set(result) != {0, 1, 2} or not literals or literals[0]['tick'] != birth
            or any(row['control_point'] != 5 for row in literals)):
        raise ValueError('Crossbow requires complete CP0/1/2 and literal CP5 history')
    result[5] = dict(control_point=5, provider='literal_transform', updates=copy.deepcopy(literals))
    return [result[cp] for cp in sorted(result)]


def validate_capture(capture, scene, instance):
    """Check a producer's normalized current-build observation, not authenticity."""
    bindings(scene, instance)
    birth = instance['created_tick']
    handle = instance['entity_handle']
    runtime_handle = (handle >> 14) << 15 | (handle & 0x3fff)
    if (not isinstance(capture, dict) or type(capture.get('schema_version')) is not int
            or capture['schema_version'] != 1 or capture.get('policy') != POLICY
            or capture.get('client_sha256') != CLIENT_SHA256
            or capture.get('particles_sha256') != PARTICLES_SHA256
            or capture.get('resource') != RESOURCE or capture.get('entity_id') != instance['entity_id']
            or type(capture.get('tick')) is not int or capture['tick'] != birth
            or not _sha(capture.get('demo_sha256'))
            or capture['demo_sha256'] != scene.get('metadata', {}).get('demo_sha256')
            or not _sha(capture.get('raw_capture_sha256'))):
        raise ValueError('Crossbow observation requires the qualified current replay/birth/module pair')
    entities = [e for e in scene['entities'] if e.get('id') == instance['entity_id']]
    if len(entities) != 1:
        raise ValueError('Crossbow owner is missing or ambiguous')
    entity = entities[0]
    model = entity.get('model_constraints') or {}
    if (entity.get('model_tracks') or model.get('resource') != MODEL
            or model.get('resource_sha256') != MODEL_SHA256
            or capture.get('model') != MODEL or capture.get('model_sha256') != MODEL_SHA256
            or capture.get('coherent') is not True or type(capture.get('identity_flags')) is not int
            or capture['identity_flags'] != 0
            or capture.get('model_method') != 'stock_dynamic_component_skeleton_model_getter_v1'
            or any(type(capture.get(k)) is not int or capture[k] != v for k, v in
                   [('runtime_owner_handle', runtime_handle), ('collection_owner_handle', runtime_handle),
                    ('skeleton_vtable_rva', 42327240), ('body_component_type_index', 10), ('body_component_offset', 48)])):
        raise ValueError('Crossbow requires the coherent current dynamic owner/body layout and exact model')
    srt = capture.get('owner_srt')
    if (not isinstance(srt, list) or len(srt) != 8
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in srt)
            or srt[3] != 1 or abs(sum(v*v for v in srt[4:]) - 1) > 2e-5):
        raise ValueError('Crossbow birth equivalence requires finite unit-scale Source owner SRT')
    try:
        if any(not math.isfinite(v) for v in struct.unpack('<8f', struct.pack('<8f', *srt))):
            raise ValueError('Crossbow owner SRT exceeds finite float32')
    except (OverflowError, struct.error) as exc:
        raise ValueError('Crossbow owner SRT exceeds float32') from exc
    if (capture.get('runtime_axes_rvas') != AXIS_RVAS or capture.get('runtime_axes_raw') != AXES
            or capture.get('runtime_axes_coherent') is not True
            or capture.get('graph_coherence_policy') != 'double_read_links_class_flags_v1'
            or capture.get('graph_coherent') is not True
            or capture.get('child_model_inputs_complete') is not True
            or type(capture.get('eligible_render_children')) is not int
            or capture['eligible_render_children'] != 0
            or type(capture.get('graph_nodes')) is not int or not 1 <= capture['graph_nodes'] <= 64
            or not _sha(capture.get('graph_sha256'))):
        raise ValueError('Crossbow requires observed unit axes and a complete coherent no-render-child graph')
    raw = _hex(capture.get('cp0_record'), 88)
    collection = _hex(capture.get('collection_record'), 0xe0)
    if (struct.unpack_from('<h', raw)[0] != 0 or struct.unpack_from('<i', raw, 4)[0] != 1
            or struct.unpack_from('<Q', raw, 0x50)[0] != runtime_handle
            or struct.unpack_from('<Q', collection, 0x60)[0] != runtime_handle
            or list(struct.unpack_from('<12f', raw, 0x1c)) != IDENTITY
            or struct.unpack_from('<3f', raw, 0x10) != (UNSET,) * 3):
        raise ValueError('Crossbow CP0 requires observed mode1 identity local/unset fallback and matching owner')
    return capture


def bind_capture(capture, scene, instance):
    validate_capture(capture, scene, instance)
    value = dict(schema_version=1, policy=POLICY, instance_id=instance['id'],
                 scene_identity=_identity(scene, instance), capture=copy.deepcopy(capture))
    return dict(value, content_sha256=digest(value))


def validate_bound_capture(package, scene, instance):
    if (not isinstance(package, dict) or type(package.get('schema_version')) is not int
            or package['schema_version'] != 1 or package.get('policy') != POLICY
            or package.get('instance_id') != instance.get('id')
            or package.get('scene_identity') != _identity(scene, instance)
            or package.get('content_sha256') != digest({k:v for k,v in package.items() if k != 'content_sha256'})):
        raise ValueError('Crossbow observation replay/rig/event history or contents changed')
    return validate_capture(package.get('capture'), scene, instance)


def request_header(package, scene, instance):
    capture = validate_bound_capture(package, scene, instance)
    return dict(policy=POLICY, client_sha256=CLIENT_SHA256, particles_sha256=PARTICLES_SHA256,
                entity_id=instance['entity_id'], resource=RESOURCE, created_tick=instance['created_tick'],
                model=MODEL, model_sha256=MODEL_SHA256, owner_scale=1, authored_matrix=copy.deepcopy(IDENTITY),
                creation_mode=3, observed_mode=1, capture_sha256=package['content_sha256'])


def validate_response(simulation, instance_id, header):
    rows = simulation.get('instances')
    if (not isinstance(rows, list) or len(rows) != 1 or rows[0].get('id') != instance_id
            or digest(rows[0].get('crossbow_owner_provider')) != digest(header)):
        raise ValueError('Simulator did not acknowledge the exact current Crossbow owner/birth input')
