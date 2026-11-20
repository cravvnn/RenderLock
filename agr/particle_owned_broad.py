"""Broad control-point bindings for effects owned by an exported character, simulated as recorded.

Used only after the strict providers have refused an instance. Nothing here is checked against the game and every
package built this way says so. Covered: creation in any attach mode on the owner itself, control points bound at
creation to the owner's origin or to one of its attachment points, literal transforms, release and destruction.
A control point bound to another exported character follows that character. One bound to an entity that is not
exported uses its recorded fallback position, held still. A literal control point first set after creation holds
its first recorded value from creation. An effect created with a named control point configuration (creation mode
-1, used by animation-driven effects) takes its bindings from that configuration in the effect's own definition;
control point 0 then defaults to the owner's origin when the configuration does not set it.
An effect created on no entity but bound at creation to an exported character is that character's effect.
A control point the game never set stays at the world origin; a system whose particles land there, far from the
effect, is left out and the rest is built.
Still refused: a binding to an unexported entity without a fallback position, a binding made or changed after
creation, and non-zero attachment offsets.
"""
import math
from .particle_control_events import compile_control_events

POLICY = 'agr_owned_broad_unverified_v1'
SCOPE = ('One recorded instance simulated from its recorded bindings without a per-effect profile. Not compared with '
         'the game: binding semantics, timing, seeds, shading and any layer the importer leaves out are unverified.')
# Creation modes that follow the owner when no control point 0 binding is recorded:
# ABSORIGIN, ABSORIGIN_FOLLOW, CUSTOMORIGIN_FOLLOW.
ORIGIN_CREATION_MODES = (0, 1, 3)
CONFIGURATION_MODE = -1
DRIVER_ORIGIN = ('PATTACH_ABSORIGIN', 'PATTACH_ABSORIGIN_FOLLOW')
DRIVER_ATTACHMENT = ('PATTACH_POINT', 'PATTACH_POINT_FOLLOW')
DRIVER_WORLD = 'PATTACH_WORLDORIGIN'
FAR_FROM_EFFECT, NEAR_WORLD_ORIGIN = 512.0, 128.0  # game units
NO_ENTITY = 0xffffff


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


def configuration_drivers(creation, definition, owner, birth=0):
    """Bindings set by the named control point configuration of the effect's own definition, by control point."""
    name = creation.get('control_point_configuration')
    require(isinstance(name, str) and name, 'creation names no control point configuration')
    if definition is None:
        return {}
    rows = [row for row in definition.get('m_controlPointConfigurations') or []
            if isinstance(row, dict) and row.get('m_name') == name]
    require(len(rows) == 1 and isinstance(rows[0].get('m_drivers'), list),
            f'the effect has no control point configuration named {name}')
    result = {}
    for driver in rows[0]['m_drivers']:
        require(isinstance(driver, dict), 'invalid configuration driver')
        cp, mode, attachment = driver.get('m_iControlPoint', 0), driver.get('m_iAttachType'), driver.get('m_attachmentName')
        require(type(cp) is int and 0 <= cp <= 63 and cp not in result and driver.get('m_entityName', 'self') == 'self'
                and not any(driver.get('m_vecOffset') or []) and not any(driver.get('m_angOffset') or []),
                'configuration driver with an offset or another entity is not supported')
        if mode in DRIVER_ORIGIN:
            result[cp] = dict(control_point=cp, provider='entity_origin', target_entity=owner, configuration=name)
        elif mode is None:
            # The definition leaves the attach type at its default, which is not known: taken as the entity's origin.
            result[cp] = dict(control_point=cp, provider='entity_origin', target_entity=owner, configuration=name,
                              assumed=True)
        elif mode in DRIVER_ATTACHMENT and isinstance(attachment, str) and attachment:
            result[cp] = dict(control_point=cp, provider='named_attachment', attachment_name=attachment,
                              target_entity=owner, configuration=name)
        elif mode == DRIVER_WORLD:
            # The driver places the point at the origin of the world, not on the entity.
            result[cp] = dict(control_point=cp, provider='literal_transform', configuration=name,
                              updates=[dict(tick=birth, control_point=cp, position=[0.0, 0.0, 0.0],
                                            rotation=[0, 0, 0, 1], interpolation_interval=0)])
        else:
            raise ValueError(f'Owned particles: configuration driver mode {mode} is not supported')
    return result


def broad_bindings(instance, start, end, exported=(), definition=None):
    """Return one binding per control point: entity origin, attachment point or literal transform.

    exported: identities of every character in the scene; a binding to one of them carries 'target_entity'.
    definition: the effect's decoded definition, needed to resolve a named control point configuration; without it
    such an effect is only checked at binding level.
    """
    controls = compile_control_events(instance, start, end)
    birth = instance['created_tick']
    index, generation, handle = (instance.get(field) for field in ('index', 'generation', 'entity_handle'))
    owner = instance.get('entity_id')
    require(type(index) is int and 0 <= index < 2**31 and type(generation) is int and generation >= 1
            and instance.get('id') == f'{index}:{generation}' and type(handle) is int and 0 <= handle <= NO_ENTITY
            and isinstance(owner, str) and owner, 'exact particle generation and an exported owner are required')

    def own(value):
        return type(value) is int and f'{value & 0x3fff}:{value >> 14}' == owner
    creation = instance['events'][0].get('data', {}).get('create_particle')
    require(isinstance(creation, dict) and creation.get('entity_handle') == handle
            and type(creation.get('attach_type')) is int and CONFIGURATION_MODE <= creation['attach_type'] <= 15
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
        bound = binding.get('entity_handle')
        target = f'{bound & 0x3fff}:{bound >> 14}' if type(bound) is int else None
        if not own(bound) and target not in exported:
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
            bindings[cp] = dict(control_point=cp, provider='entity_origin', target_entity=target)
        elif mode in (4, 5) and attachment > 0:
            bindings[cp] = dict(binding, target_entity=target)
        else:
            raise ValueError(f'Owned particles: binding mode {mode} with attachment {attachment} is not supported')
    for update in controls['literal_transforms']:
        cp = update['control_point']
        if cp in bindings and (bindings[cp].get('provider') != 'literal_transform'
                               or bindings[cp].get('other_entity_fallback')):
            raise ValueError('Owned particles: a literal transform replacing an entity binding is not supported')
        if cp not in bindings:
            bindings[cp] = dict(control_point=cp, provider='literal_transform', updates=[])
            if update['tick'] != birth:
                # First set after creation: hold that first value from creation (the game's own default is unknown).
                bindings[cp]['updates'].append(dict(update, tick=birth, backfilled=True))
        bindings[cp]['updates'].append(update)
    if creation['attach_type'] == CONFIGURATION_MODE:
        require(own(handle), 'a configured effect on another entity is not supported')
        for cp, row in configuration_drivers(creation, definition, owner, birth).items():
            bindings.setdefault(cp, row)
        # The configuration may leave control point 0 unset; the game's default for it is not known.
        bindings.setdefault(0, dict(control_point=0, provider='entity_origin', target_entity=owner, assumed=True))
    if 0 not in bindings and creation['attach_type'] in ORIGIN_CREATION_MODES and own(handle):
        bindings[0] = dict(control_point=0, provider='entity_origin', target_entity=owner)
    if 0 not in bindings:
        # The game set other points and never set point 0, which then stays at the world origin. The response
        # check refuses the effect if particles land there.
        require(bindings and own(handle), 'no control point 0 binding is recorded')
        bindings[0] = dict(control_point=0, provider='literal_transform', assumed_world_origin=True,
                           updates=[dict(tick=birth, control_point=0, position=[0.0, 0.0, 0.0], rotation=[0, 0, 0, 1],
                                         interpolation_interval=0)])
    return [bindings[cp] for cp in sorted(bindings)]


def leave_out_world_origin_systems(row, anchors):
    """Empty every system that has a particle at the world origin while the effect is elsewhere.

    anchors: positions of the effect over the clip. A particle counts when it is near the world origin and far from
    every anchor. Returns the emptied system paths, recorded on the row as 'systems_left_at_world_origin'. Refuses
    when nothing else of the effect would remain.
    """
    anchors = [anchor for anchor in anchors if len(anchor) == 3]
    if not anchors:
        return []

    def stray(position):
        return (math.hypot(*position) < NEAR_WORLD_ORIGIN
                and min(math.dist(position, anchor) for anchor in anchors) > FAR_FROM_EFFECT)
    live, lost = set(), set()
    for frame in row.get('frames', []):
        for system in frame['systems']:
            particles = system.get('particles') or []
            if particles:
                live.add(system.get('system_path'))
                if any(stray(particle['position']) for particle in particles):
                    lost.add(system.get('system_path'))
    if not lost:
        return []
    require(None not in lost and lost != live, 'unset control points place every particle at the world origin')
    for frame in row['frames']:
        for system in frame['systems']:
            if system.get('system_path') in lost:
                system['particles'] = []
    row['systems_left_at_world_origin'] = sorted(lost)
    return sorted(lost)


def validate_response(simulation, instance_id, bindings, anchors=()):
    """The simulator must acknowledge the broad provider and every supplied point.

    Returns the control points the effect reads that the game never set. The simulator leaves those at the world
    origin, which is assumed to match the game. anchors: control point 0 positions over the clip; systems whose
    particles land at the world origin far from all of them are emptied (see leave_out_world_origin_systems).
    """
    rows = simulation.get('instances', [])
    require(isinstance(rows, list) and len(rows) == 1 and rows[0].get('id') == instance_id
            and rows[0].get('owned_broad_provider') == dict(policy=POLICY),
            'simulator did not acknowledge the broad provider; the bundled simulator is too old')
    supplied = [binding['control_point'] for binding in bindings]
    confirmed = rows[0].get('control_point_indices')
    require(isinstance(confirmed, list) and set(supplied) <= set(confirmed),
            'simulator did not confirm every supplied control point')
    unprovided = rows[0].get('unprovided_control_point_indices')
    require(unprovided is None or isinstance(unprovided, list), 'invalid unprovided control point list')
    unprovided = sorted(set(unprovided or []) | {binding['control_point'] for binding in bindings
                                                 if binding.get('assumed_world_origin')})
    if not unprovided:
        return []
    leave_out_world_origin_systems(rows[0], anchors)
    return list(unprovided)


def unset_points_allowed(row, supplied):
    """An unverified instance may read points the game never set: a list of other control points."""
    unprovided = row.get('unprovided_control_point_indices')
    return unprovided is None or (isinstance(unprovided, list)
                                  and all(type(cp) is int and 0 <= cp <= 63 for cp in unprovided)
                                  and not set(unprovided) & set(supplied))


def is_unverified(row):
    """True for a simulated instance row built by this path or by the unverified world path."""
    world = row.get('world_origin_provider')
    return (row.get('owned_broad_provider') == dict(policy=POLICY)
            or isinstance(world, dict) and world.get('policy') == 'agr_world_literal_unverified_v1')
