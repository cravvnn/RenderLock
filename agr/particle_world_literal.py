"""Unowned world effects driven only by recorded literal control points, simulated as recorded.

This is the broad path: any resource, no per-effect profile, no client or definition pin. Nothing here is checked
against the game, and every package says so. The narrow bat-explosion profile in particle_world_origin stays as is.
Accepted shape: one CREATE (unattached, mode 8), literal UPDATE_TRANSFORM events and optional RELEASE. A destroyed
effect, an entity binding or an interpolated transform is refused and stays visible as unsupported.
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
            and 0 <= start <= birth <= end < 2**31 - 1 and end - birth <= MAX_TICKS,
            'requires an in-range birth and bounded inclusive 64Hz output')
    require(_sha(scene.get('metadata', {}).get('demo_sha256')), 'replay SHA256 is required')
    index, generation = instance.get('index'), instance.get('generation')
    require(type(index) is int and 0 <= index < 2**31 and type(generation) is int and 1 <= generation < 2**31
            and instance.get('id') == f'{index}:{generation}' and instance.get('entity_handle') == 0xffffff,
            'exact generation and the unowned handle are required')
    events = instance.get('events')
    require(isinstance(events, list) and 2 <= len(events) <= 4096 and all(isinstance(e, dict) for e in events)
            and {e.get('type') for e in events} <= {'CREATE', 'UPDATE_TRANSFORM', 'RELEASE'},
            'only literal transforms and release are supported; destruction and entity bindings are not')
    require(all(type(e.get('order')) is int for e in events), 'recorded event order is required')
    creation = events[0].get('data', {}).get('create_particle')
    require(creation == instance.get('create') and isinstance(creation, dict)
            and {'particle_name_index', 'attach_type', 'entity_handle', 'cluster'} <= set(creation)
            <= {'particle_name_index', 'attach_type', 'entity_handle', 'cluster', 'aggregation_position'}
            and creation.get('cluster') is False
            and instance.get('resource_hash') == creation.get('particle_name_index'),
            'creation fields or resource identity differ')
    controls = compile_control_events(instance, birth, end)
    require(controls['destroy'] is None and not controls['entity_binding_events'], 'world controls or lifetime differ')
    points = {}
    for literal in controls['literal_transforms']:
        require(all(math.isfinite(v) and abs(v) <= 131072 for v in literal['position']),
                'control point position exceeds bounds')
        points.setdefault(literal['control_point'], []).append(literal)
    require(0 in points, 'no recorded origin (control point 0)')
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
