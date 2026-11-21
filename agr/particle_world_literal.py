"""Unowned world effects driven only by recorded literal control points, simulated as recorded.

This is the broad path: any resource, no per-effect profile, no client or definition pin. Nothing here is checked
against the game, and every package says so. The narrow bat-explosion profile in particle_world_origin stays as is.
Accepted shape: one CREATE (unattached, mode 8), literal UPDATE_TRANSFORM events, optional RELEASE and DESTROY.
An entity binding or an interpolated transform is refused and stays visible as unsupported.
A model the game assigns to a control point is not applied: the systems that need it end up at the world origin and
are left out (see leave_out_world_origin_systems). An effect that never sets control point 0 has it at the world
origin, as the game leaves it.
Because every input is a recorded literal, an effect born before the clip is simulated from its true birth, up to
MAX_TICKS before the clip end.
"""
import math
from .particle_events import WORLD_CUSTOM_ORIGIN_SCOPE, is_world_custom_origin_candidate
from .particle_control_events import compile_control_events
from .particle_value_identity import digest, _sha

POLICY = 'agr_world_literal_unverified_v1'
BACKEND = 'simulated_world_literal_unverified_v1'
SCOPE = ('Recorded unowned effect simulated from its literal control points. Not compared with the game: timing, '
         'seeds, shading and any layer the importer leaves out are unverified.')
MAX_TICKS = 4095
MODEL_EVENT = 'SET_CONTROL_POINT_MODEL'


def without_model_events(instance):
    """The instance without its control point model assignments, which nothing downstream applies."""
    return dict(instance, events=[event for event in instance['events'] if event.get('type') != MODEL_EVENT])


def models_not_applied(instance):
    return sorted({str(event.get('data', {}).get('set_control_point_model', {}).get('model_name'))
                   for event in instance['events'] if event.get('type') == MODEL_EVENT})


def simulator_events(instance, points):
    """The events sent to the simulator: model assignments removed and, when the game never set control point 0,
    an explicit world-origin value for it at creation (the simulator requires one). The simulator needs an order on
    every event, so the recorded orders are doubled and the added point takes the slot right after creation.
    """
    events = without_model_events(instance)['events']
    if not points[0][0].get('assumed_world_origin'):
        return events
    require(all(type(event.get('order')) is int and 0 <= event['order'] < 2**30 for event in events),
            'recorded event order exceeds bounds')
    events = [dict(event, order=2 * event['order']) for event in events]
    origin = dict(type='UPDATE_TRANSFORM', tick=instance['created_tick'], order=events[0]['order'] + 1, data=dict(
        type='GAME_PARTICLE_MANAGER_EVENT_UPDATE_TRANSFORM', index=instance['index'],
        update_particle_transform=dict(control_point=0, position=dict(x=0.0, y=0.0, z=0.0))))
    return [events[0], origin] + events[1:]


def placed_positions(points):
    """Where the effect is: every supplied position away from the world origin (a point there is a setting)."""
    return [list(update['position']) for updates in points.values() for update in updates
            if not update.get('assumed_world_origin') and math.hypot(*update['position']) >= 128.0]


def require(ok, message):
    if not ok:
        raise ValueError('World-origin particles: ' + message)


def literal_controls(scene, instance):
    """Return the recorded literal transforms of one admissible instance, grouped by control point."""
    require(isinstance(instance, dict) and instance.get('selection_scope') == WORLD_CUSTOM_ORIGIN_SCOPE
            and is_world_custom_origin_candidate(instance), 'explicit unattached creation provenance is required')
    resource = instance.get('resource')
    require(isinstance(resource, str) and resource.endswith('.vpcf'), 'recorded resource name is required')
    start, end, birth, rate = (scene.get('start_tick'), scene.get('end_tick'), instance.get('created_tick'),
                               scene.get('tick_rate'))
    require(all(type(v) is int for v in (start, end, birth, rate)) and rate == 64
            and 0 <= start <= end < 2**31 - 1 and 0 <= birth <= end, 'requires a birth before the clip end at 64Hz')
    require(end - birth <= MAX_TICKS, 'born more than 64 s before the clip end; not simulated')
    require(_sha(scene.get('metadata', {}).get('demo_sha256')), 'replay SHA256 is required')
    index, generation = instance.get('index'), instance.get('generation')
    require(type(index) is int and 0 <= index < 2**31 and type(generation) is int and 1 <= generation < 2**31
            and instance.get('id') == f'{index}:{generation}' and instance.get('entity_handle') == 0xffffff,
            'exact generation and the unowned handle are required')
    events = instance.get('events')
    require(isinstance(events, list) and 2 <= len(events) <= 4096 and all(isinstance(e, dict) for e in events)
            and {e.get('type') for e in events} <= {'CREATE', 'UPDATE_TRANSFORM', 'RELEASE', 'DESTROY', MODEL_EVENT},
            'only literal transforms, release and destruction are supported; entity bindings are not')
    require(all(type(e.get('order')) is int for e in events), 'recorded event order is required')
    creation = events[0].get('data', {}).get('create_particle')
    require(creation == instance.get('create') and isinstance(creation, dict)
            and {'particle_name_index', 'attach_type', 'entity_handle', 'cluster'} <= set(creation)
            <= {'particle_name_index', 'attach_type', 'entity_handle', 'cluster', 'aggregation_position'}
            and creation.get('cluster') is False
            and instance.get('resource_hash') == creation.get('particle_name_index'),
            'creation fields or resource identity differ')
    controls = compile_control_events(without_model_events(instance), birth, end)
    require(not controls['entity_binding_events'], 'world controls differ')
    points = {}
    for literal in controls['literal_transforms']:
        require(all(math.isfinite(v) and abs(v) <= 131072 for v in literal['position']),
                'control point position exceeds bounds')
        points.setdefault(literal['control_point'], []).append(literal)
    require(points, 'no recorded control point')
    if 0 not in points:
        # Never set by the game, which leaves it at the world origin.
        points[0] = [dict(tick=birth, control_point=0, position=[0.0, 0.0, 0.0], rotation=[0.0, 0.0, 0.0, 1.0],
                          interpolation_interval=0, assumed_world_origin=True)]
    require(all(rows[0]['tick'] == birth for rows in points.values()),
            'a control point first set after creation needs its creation-time value')
    return points


def request_header(scene, instance):
    literal_controls(scene, instance)
    return dict(policy=POLICY, client_sha256=None, instance_id=instance['id'], resource=instance['resource'],
                created_tick=instance['created_tick'], start_tick=scene['start_tick'], end_tick=scene['end_tick'],
                tick_rate=scene['tick_rate'], demo_sha256=scene['metadata']['demo_sha256'],
                events_sha256=digest(instance['events']))


def control_points(scene, instance):
    """One explicit snapshot per tick: control point 0, plus every other recorded literal point."""
    points = literal_controls(scene, instance)
    rows = []
    for tick in range(instance['created_tick'], scene['end_tick'] + 1):
        current = {cp: [u for u in updates if u['tick'] <= tick][-1] for cp, updates in points.items()}
        row = dict(tick=tick, position=list(current[0]['position']), rotation=list(current[0]['rotation']))
        extras = [dict(index=cp, position=list(u['position']), rotation=list(u['rotation']))
                  for cp, u in sorted(current.items()) if cp != 0]
        if extras:
            row['extra_control_points'] = extras
        rows.append(row)
    return rows


def world_identity(scene, instance):
    return digest(dict(header=request_header(scene, instance), source_instance=instance,
                       coordinates=scene['metadata'].get('coordinate_system')))


def validate_response(simulation, scene, instance):
    rows = simulation.get('instances')
    require(isinstance(rows, list) and len(rows) == 1 and rows[0].get('id') == instance['id']
            and rows[0].get('world_origin_provider') == request_header(scene, instance),
            'simulator did not acknowledge this world provider; the bundled simulator is too old')
    supplied = sorted(literal_controls(scene, instance))
    confirmed = rows[0].get('control_point_indices')
    require(isinstance(confirmed, list) and set(supplied) <= set(confirmed),
            'simulator did not confirm every supplied control point')


def validate_package(package, scene):
    require(type(package.get('schema_version')) is int and package['schema_version'] == 1
            and package.get('backend') == BACKEND and package.get('entity_ids') == []
            and package.get('coverage_scope') == SCOPE, 'package identity differs')
    require(set(package) <= {'schema_version', 'backend', 'entity_ids', 'scene_identity', 'world_origin_provider',
                             'simulation', 'assets', 'simulation_request_sha256', 'coverage_scope'},
            'unknown package fields')
    simulation = package.get('simulation', {})
    rows = simulation.get('instances', []) if isinstance(simulation, dict) else []
    require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
            'one world generation is required')
    originals = [i for i in scene.get('particle_instances', []) if i.get('id') == rows[0].get('id')]
    require(len(originals) == 1, 'recorded source generation is missing or ambiguous')
    original = originals[0]
    require(package.get('scene_identity') == world_identity(scene, original)
            and package.get('world_origin_provider') == request_header(scene, original)
            and rows[0].get('entity_id') is None, 'source world generation, replay, range or coordinates changed')
    require(_sha(package.get('simulation_request_sha256')), 'simulator request digest is required')
    require(simulation.get('backend') and not simulation.get('warnings'), 'unresolved simulation diagnostics')
    validate_response(simulation, scene, original)
    definitions = package.get('assets', {}).get('definitions')
    names = {system['name'].removesuffix('_c') for frame in rows[0].get('frames', []) for system in frame['systems']}
    require(isinstance(definitions, dict) and names and names <= set(definitions),
            'simulated systems lack their definitions')
