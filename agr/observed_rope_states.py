"""Complete native collection arrays for two authored ambient rope layers.

Geometry uses array order within each snapshot. It never joins array slots into
particle lifetimes or invents a birth, seed, age, native ID or timer phase.
The portable digest prevents stale/mismatched use; it is not authentication of
an independently edited capture. Native acquisition is qualified separately.
"""
import copy
import math
import struct
from .replay_capture import canonical_sha256
from .captured_slide_binding import _source_scene_identity as scene_identity
from .observed_slide_profile import NATIVE_DRIVER_DEFAULTS
from .client_modifier_writes import integer

BACKEND = 'observed_rope_frames_v1'
POLICY = 'agr_complete_native_ambient_rope_arrays_v1'
OWNER = '56:895'
ROOT = 'particles/abilities/priest/priest_ambient.vpcf'
PROFILES = {
    'particles/abilities/priest/priest_ambient_censer_smoke.vpcf': (
        '1a5aa57e39cb96726a4db6a09dd095a71decd7c95effbbe032ee9f009fe7e211',
        '3ff640d8b8d00002fd0ce30cbf1ac251fa355d9ff8ef7a7de2174a2b55981d50'),
    'particles/abilities/priest/priest_ambient_smoke.vpcf': (
        'c962dbd3f63ea0765535197ceb171131b4904c7812581aa317da6cae7ed3b078',
        'e2887f8a2035e222b0d0e855df63004f08df6ca29a84105deb95a631abb78e01'),
}
FIELDS = {0: ('position', 3), 3: ('radius', 1), 6: ('color', 3),
          7: ('alpha', 1), 16: ('alpha_alternate', 1),
          26: ('scratch_float1', 1)}
SOURCE_KEYS = {'report_sha256', 'observer_sha256', 'field_reader_sha256',
               'freeze_sha256', 'cleanup_sha256'}


def require(value, message):
    if not value:
        raise ValueError('Observed rope arrays: '+message)


def keys(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names), label+' fields differ')


def digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_profiles(assets):
    require(type(assets) is dict and assets.get('models', {}) == {}, 'unexpected rope model assets')
    definitions = assets.get('definitions', {})
    require(set(definitions) == set(PROFILES), 'authored rope resource coverage differs')
    pins = assets.get('provenance', {}).get('input_sha256', {})
    for resource, (source, definition) in PROFILES.items():
        require(pins.get(resource) == source and canonical_sha256(definitions[resource]) == definition,
                'authored native rope definition changed')
    return definitions


def validate_reference(envelope, data):
    keys(envelope, ('capture', 'capture_sha256'), 'envelope')
    c = envelope['capture']
    require(digest(envelope['capture_sha256']) and canonical_sha256(c) == envelope['capture_sha256'],
            'portable reference digest differs')
    keys(c, ('schema_version', 'policy', 'demo_sha256', 'scene_identity', 'entity_id',
             'start_tick', 'end_tick', 'inclusive', 'native', 'profiles', 'source',
             'between_tick_policy', 'samples', 'scope'), 'capture')
    require(type(c['schema_version']) is int and c['schema_version'] == 1 and c['policy'] == POLICY,
            'schema/policy differs')
    require(c['entity_id'] == OWNER and c['demo_sha256'] == data.get('metadata', {}).get('demo_sha256')
            and digest(c['demo_sha256']) and c['scene_identity'] == scene_identity(data, [OWNER]),
            'replay/owner/scene identity differs')
    require(integer(c['start_tick'], 0, 2**31-2) and integer(c['end_tick'], c['start_tick'], 2**31-2)
            and (c['start_tick'], c['end_tick']) == (data.get('start_tick'), data.get('end_tick'))
            and c['end_tick']-c['start_tick'] < 10000 and c['inclusive'] is True,
            'complete inclusive source range differs')
    require(c['native'] == NATIVE_DRIVER_DEFAULTS and c['profiles'] == {
        name: dict(source_sha256=source, definition_sha256=definition)
        for name, (source, definition) in PROFILES.items()}, 'qualified module/profile pair differs')
    keys(c['source'], SOURCE_KEYS, 'source provenance')
    require(all(digest(v) for v in c['source'].values()), 'source provenance missing')
    require(c['between_tick_policy'] == 'hold_complete_arrays'
            and isinstance(c['scope'], str) and 0 < len(c['scope']) <= 1024,
            'frame policy/scope unavailable')
    samples = c['samples']
    require(isinstance(samples, list) and len(samples) == c['end_tick']-c['start_tick']+1,
            'native frame reference is not complete/dense')
    total = 0
    for tick, sample in zip(range(c['start_tick'], c['end_tick']+1), samples):
        keys(sample, ('tick', 'collections'), 'frame')
        require(type(sample['tick']) is int and sample['tick'] == tick, 'native frame tick differs')
        collections = sample['collections']
        require(isinstance(collections, list) and len(collections) == len(PROFILES)
                and [r.get('resource') for r in collections if isinstance(r, dict)] == sorted(PROFILES),
                'frame collection coverage differs')
        for row in collections:
            keys(row, ('resource', 'state', 'count', 'points'), 'collection')
            require(integer(row['count'], 0, 64) and isinstance(row['points'], list)
                    and len(row['points']) == row['count']
                    and row['state'] == ('captured' if row['count'] else 'known_empty'),
                    'unknown/truncated native array is not empty')
            total += row['count']
            require(total <= 200000, 'complete reference point budget exceeded')
            for point in row['points']:
                keys(point, (name for name, _ in FIELDS.values()), 'point')
                for name, size in FIELDS.values():
                    values = point[name] if size > 1 else [point[name]]
                    require(isinstance(values, list) and len(values) == size
                            and all(type(v) in (float, int) and math.isfinite(v) for v in values),
                            'nonfinite/missing native point field')
                require(point['radius'] >= 0, 'negative native rope radius is unqualified')
    return c


def geometry_frames(envelope, data, resource):
    """Project one collection, preserving order; keys exist only in one frame.

    The existing pure geometry routine uses ``id`` solely to reject duplicates
    inside a snapshot. These ordinal keys are never captured/native IDs, joined
    between frames, serialized into the reference, or reported as particle tracks.
    Age=0 is an unused numeric argument for these pinned, time-independent rope
    profiles. Actual texture position is captured field26, not a generated clock.
    """
    c = validate_reference(envelope, data)
    require(resource in PROFILES, 'unknown geometry resource')
    result = []
    for sample in c['samples']:
        row = next(r for r in sample['collections'] if r['resource'] == resource)
        result.append(dict(tick=sample['tick'], age=0., particles=[
            dict(copy.deepcopy(point), id=index) for index, point in enumerate(row['points'])]))
    return result


def _raw_points(node):
    count = node['particles']
    require(integer(count, 0, 64) and node.get('field_state') == ('captured' if count else 'known_empty'),
            'native array acquisition unavailable')
    fields = node['fields']
    keys(fields, (str(f) for f in FIELDS) if count else (), 'raw attribute coverage')
    points = [{} for _ in range(count)]
    for field, (name, components) in FIELDS.items():
        if not count:
            break
        entry = fields[str(field)]
        keys(entry, ('stride', 'components', 'raw_hex'), 'raw attribute')
        stride = entry['stride']
        require(type(stride) is int and stride in (0, components*4)
                and type(entry['components']) is int and entry['components'] == components
                and isinstance(entry['raw_hex'], str) and len(entry['raw_hex']) <= 8192,
                'raw attribute format differs')
        last_lane = min(count-1, 3) if stride == 0 else (count-1) % 4
        length = ((count-1)//4*stride+(components-1)*4+last_lane+1)*4
        raw = bytes.fromhex(entry['raw_hex'])
        require(len(raw) == length, 'raw SIMD attribute length differs')
        for index, point in enumerate(points):
            values = [struct.unpack_from('<f', raw, ((index//4)*stride+index % 4+axis*4)*4)[0]
                      for axis in range(components)]
            point[name] = values if components > 1 else values[0]
    return points


def build_reference(report, data, source, cleanup):
    """Produce a portable reference only from one closed/restored owned run."""
    require(report.get('phase') == 'closed' and type(report.get('launch_count')) is int
            and report['launch_count'] == 1 and type(report.get('exit_code')) is int
            and report['exit_code'] == 0 and report.get('workflow_passed') is True
            and report.get('exact_target_coverage') is True and report.get('recordings') == []
            and not report.get('error') and not report.get('cleanup_errors')
            and not report.get('monitor_error') and not report.get('watchdog_fired')
            and not report.get('crash_inventory_changed')
            and report.get('config_unchanged_after_cleanup') is True
            and report.get('source_unchanged_after_run') is True
            and report.get('retained_baseline_unchanged') is True
            and not any(report.get('pending_runtime_restoration', {}).values()),
            'owned run incomplete or not restored')
    require(cleanup.get('status') == 'PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED'
            and cleanup.get('source_absent') is True and cleanup.get('runtime_restored') is True
            and cleanup.get('owned_native_removed_from_game') is True, 'owned deployment not archived')
    require(report.get('replay_sha256') == data['metadata']['demo_sha256'], 'source replay differs')
    observations = report.get('seeks')
    require(isinstance(observations, list) and len(observations) == data['end_tick']-data['start_tick']+1,
            'owned run lacks complete dense frames')
    samples = []
    for tick, observation in zip(range(data['start_tick'], data['end_tick']+1), observations):
        require(type(observation.get('target')) is int and type(observation.get('after')) is int
                and observation['target'] == tick and observation['after'] == tick, 'observation tick differs')
        native = observation['native_capture']
        require(type(native.get('tick')) is int and native['tick'] == tick
                and native.get('client_sha256') == NATIVE_DRIVER_DEFAULTS['client_sha256']
                and native.get('particles_sha256') == NATIVE_DRIVER_DEFAULTS['particles_sha256'],
                'native observation identity differs')
        actors = native.get('actors')
        require(isinstance(actors, list) and len(actors) == 1, 'native owner coverage differs')
        actor = actors[0]
        require(type(actor.get('entity_index')) is int and actor['entity_index'] == 56
                and type(actor.get('handle')) is int and actor['handle'] == 56+(895 << 15)
                and type(actor.get('life_state')) is int and actor['life_state'] == 0,
                'qualified native owner differs')
        effects = actor.get('effects')
        require(isinstance(effects, list) and len(effects) == 1
                and effects[0]['resource']['name'].removesuffix('_c') == ROOT,
                'native ambient root missing/ambiguous')
        tree = effects[0]['declared_tree']
        require(tree.get('coherent') is True and isinstance(tree.get('nodes'), list)
                and 1 <= len(tree['nodes']) <= 16, 'native graph unavailable')
        rows = []
        for resource in sorted(PROFILES):
            nodes = [n for n in tree['nodes'] if n.get('resource') == resource]
            require(len(nodes) == 1, 'native rope child missing/ambiguous')
            node = nodes[0]
            rows.append(dict(resource=resource, state=node['field_state'], count=node['particles'],
                             points=_raw_points(node)))
        samples.append(dict(tick=tick, collections=rows))
    capture = dict(schema_version=1, policy=POLICY, demo_sha256=report['replay_sha256'],
        scene_identity=scene_identity(data, [OWNER]), entity_id=OWNER,
        start_tick=data['start_tick'], end_tick=data['end_tick'], inclusive=True,
        native=dict(NATIVE_DRIVER_DEFAULTS), profiles={name: dict(source_sha256=a, definition_sha256=b)
            for name, (a, b) in PROFILES.items()}, source=copy.deepcopy(source),
        between_tick_policy='hold_complete_arrays', samples=samples,
        scope='Complete native current-array samples for two authored ambient ropes. Array order is per-frame geometry, '
              'not persistent particle identity. No births/seeds/ages or missing-root emptiness inferred. '
              'Texture position is observed field26; frame arrays hold between ticks. Source shaders, lighting, '
              'pixel/subframe visibility, hero render-clock alignment and orphaned tails remain separate.')
    envelope = dict(capture=capture, capture_sha256=canonical_sha256(capture))
    validate_reference(envelope, data)
    return envelope


def prepare_package(data, reference, assets):
    validate_reference(reference, data)
    validate_profiles(assets)
    return dict(schema_version=1, backend=BACKEND, entity_ids=[OWNER],
                scene_identity=scene_identity(data, [OWNER]), reference=copy.deepcopy(reference),
                assets=copy.deepcopy(assets), assets_sha256=canonical_sha256(assets),
                coverage_scope=reference['capture']['scope'])


def validate_package(package, data):
    keys(package, ('schema_version', 'backend', 'entity_ids', 'scene_identity',
                   'reference', 'assets', 'assets_sha256', 'coverage_scope'), 'package')
    require(package.get('schema_version') == 1 and type(package['schema_version']) is int
            and package.get('backend') == BACKEND and package.get('entity_ids') == [OWNER]
            and package.get('scene_identity') == scene_identity(data, [OWNER]), 'package scene identity differs')
    validate_reference(package.get('reference'), data)
    require(canonical_sha256(package.get('assets')) == package.get('assets_sha256'),
            'prepared asset descriptor digest differs')
    validate_profiles(package.get('assets', {}))
    require(package.get('coverage_scope') == package['reference']['capture']['scope'], 'package scope differs')
