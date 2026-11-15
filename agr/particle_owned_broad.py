"""Broad control-point bindings for effects owned by an exported character, simulated as recorded.

Used only after the strict providers have refused an instance. Nothing here is checked against the game and every
package built this way says so. Covered: creation in any attach mode on the owner itself, control points bound at
creation to the owner's origin or to one of its attachment points, literal transforms, release and destruction.
A control point bound to an entity other than the exported owner uses its recorded fallback position, held still.
Still refused: such a binding without a fallback position, a binding made or changed after creation, and non-zero
attachment offsets.
"""
import math
from .particle_control_events import compile_control_events

POLICY = 'agr_owned_broad_unverified_v1'
SCOPE = ('One recorded instance simulated from its recorded bindings without a per-effect profile. Not compared with '
         'the game: binding semantics, timing, seeds, shading and any layer the importer leaves out are unverified.')
# Creation modes that follow the owner when no control point 0 binding is recorded:
# ABSORIGIN, ABSORIGIN_FOLLOW, CUSTOMORIGIN_FOLLOW.
ORIGIN_CREATION_MODES = (0, 1, 3)


def require(ok, message):
    if not ok:
        raise ValueError('Owned particles: ' + message)


def _zero_offsets(binding):
    for field, components in (('offset_position', 'xyzw'), ('offset_angles', 'xyz')):
        value = binding.get(field)
        if value is None:
            continue
        require(isinstance(value, dict) and not set(value) - set(components)
                and all(type(v) in (int, float) and math.isfinite(v) and v == 0 for v in value.values()),
                'attachment offsets are not supported')


def broad_bindings(instance, start, end):
    """Return one binding per control point: entity origin, attachment point or literal transform."""
    controls = compile_control_events(instance, start, end)
    birth = instance['created_tick']
    index, generation, handle = (instance.get(field) for field in ('index', 'generation', 'entity_handle'))
    owner = instance.get('entity_id')
    require(type(index) is int and 0 <= index < 2**31 and type(generation) is int and generation >= 1
            and instance.get('id') == f'{index}:{generation}' and type(handle) is int and 0 <= handle < 0xffffff
            and isinstance(owner, str) and owner, 'exact particle generation and an exported owner are required')

    def own(value):
        return type(value) is int and f'{value & 0x3fff}:{value >> 14}' == owner
    creation = instance['events'][0].get('data', {}).get('create_particle')
    require(isinstance(creation, dict) and creation.get('entity_handle') == handle
            and type(creation.get('attach_type')) is int and 0 <= creation['attach_type'] <= 15
            and creation.get('cluster', False) is False, 'creation record differs from its instance')
    bindings = {}
    for event in controls['entity_binding_events']:
        binding = event['data'].get('update_particle_ent')
        require(isinstance(binding, dict), 'invalid entity binding')
        cp = binding.get('control_point')
        require(type(cp) is int and 0 <= cp <= 63 and cp not in bindings and event['tick'] == birth,
                'a control point bound or rebound after creation is not supported')
        mode, attachment = binding.get('attach_type'), binding.get('attachment')
        require(type(mode) is int and type(attachment) is int, 'invalid entity binding mode')
        _zero_offsets(binding)
        if not own(binding.get('entity_handle')):
            fallback = binding.get('fallback_position')
            require(isinstance(fallback, dict) and set(fallback) <= set('xyz')
                    and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 131072 for v in fallback.values()),
                    'a control point on an entity that is not exported has no recorded fallback position')
            position = [float(fallback.get(axis, 0)) for axis in 'xyz']
            bindings[cp] = dict(control_point=cp, provider='literal_transform', other_entity_fallback=True,
                                updates=[dict(tick=birth, control_point=cp, position=position, rotation=[0, 0, 0, 1],
                                              interpolation_interval=0)])
            continue
        if mode in (0, 1) and attachment == 0:
            bindings[cp] = dict(control_point=cp, provider='entity_origin')
        elif mode in (4, 5) and attachment > 0:
            bindings[cp] = dict(binding)
        else:
            raise ValueError(f'Owned particles: binding mode {mode} with attachment {attachment} is not supported')
    for update in controls['literal_transforms']:
        cp = update['control_point']
        if cp in bindings and (bindings[cp].get('provider') != 'literal_transform'
                               or bindings[cp].get('other_entity_fallback')):
            raise ValueError('Owned particles: a literal transform replacing an entity binding is not supported')
        if cp not in bindings:
            require(update['tick'] == birth, 'a literal control point needs its creation-time value')
            bindings[cp] = dict(control_point=cp, provider='literal_transform', updates=[])
        bindings[cp]['updates'].append(update)
    if 0 not in bindings:
        require(creation['attach_type'] in ORIGIN_CREATION_MODES and own(handle),
                'no control point 0 binding is recorded')
        bindings[0] = dict(control_point=0, provider='entity_origin')
    return [bindings[cp] for cp in sorted(bindings)]


def validate_response(simulation, instance_id, bindings):
    """The simulator must acknowledge the broad provider and every supplied point, and read nothing unsupplied."""
    rows = simulation.get('instances', [])
    require(isinstance(rows, list) and len(rows) == 1 and rows[0].get('id') == instance_id
            and rows[0].get('owned_broad_provider') == dict(policy=POLICY),
            'simulator did not acknowledge the broad provider; the bundled simulator is too old')
    supplied = [binding['control_point'] for binding in bindings]
    confirmed = rows[0].get('control_point_indices')
    require(isinstance(confirmed, list) and set(supplied) <= set(confirmed),
            'simulator did not confirm every supplied control point')
    unprovided = rows[0].get('unprovided_control_point_indices')
    require(unprovided in (None, []), f'the effect reads control points that were not recorded: {unprovided}')


def is_unverified(row):
    """True for a simulated instance row built by this path or by the unverified world path."""
    world = row.get('world_origin_provider')
    return (row.get('owned_broad_provider') == dict(policy=POLICY)
            or isinstance(world, dict) and world.get('policy') == 'agr_world_literal_unverified_v1')
