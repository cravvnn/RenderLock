"""Dense same-run evaluated slide light outputs; no inferred lifetimes."""
import copy
import re
from .replay_capture import canonical_sha256
from .captured_slide_binding import _source_scene_identity as scene_identity
from .observed_slide_profile import NATIVE_DRIVER_DEFAULTS
from .observed_slide_profile import keys, digest
from .particle_point_light import decode_descriptor, SCOPE, require

BACKEND = 'observed_slide_light_frames_v1'
POLICY = 'agr_evaluated_slide_point_lights_v1'
OWNER = '56:895'
RESOURCE = 'particles/generic/slide_start_light.vpcf'
READER_SHA = '5003d506d41aecc83b5020ff386dfee01c25519c968abbb924103094667cebc3'
LAYOUT_SHA = '29bb6ecc7a665867bc56ab75c681e33a7b50988db3e765e9ccc1587bc16c701d'
COPY_SHA = 'dc496ea0a3c1a65c5f49ad7d79161f81aa7b6b0fada99585c64ec21e69dbe37c'
SOURCE_KEYS = {'report_sha256', 'observer_sha256', 'field_reader_sha256', 'freeze_sha256',
               'cleanup_sha256', 'evaluated_reader_sha256', 'layout_sha256', 'copy_proof_sha256'}


def integer(v, lo, hi):
    return type(v) is int and lo <= v <= hi


def validate_reference(reference, data):
    keys(reference, ('capture', 'capture_sha256'), 'light envelope')
    c = reference['capture']
    require(digest(reference['capture_sha256']) and canonical_sha256(c) == reference['capture_sha256'],
            'reference digest differs')
    keys(c, ('schema_version', 'policy', 'entity_id', 'scene_identity', 'native', 'source',
             'between_tick_policy', 'frames', 'scope'), 'light capture')
    require(type(c['schema_version']) is int and c['schema_version'] == 1 and c['policy'] == POLICY
            and c['entity_id'] == OWNER and c['scene_identity'] == scene_identity(data, [OWNER])
            and c['native'] == NATIVE_DRIVER_DEFAULTS, 'scene/native/schema identity differs')
    source = c['source'];keys(source, SOURCE_KEYS, 'light provenance')
    require(all(digest(v) for v in source.values()) and source['evaluated_reader_sha256'] == READER_SHA
            and source['layout_sha256'] == LAYOUT_SHA and source['copy_proof_sha256'] == COPY_SHA,
            'unqualified evaluated-output reader/layout')
    require(c['scope'] == SCOPE and c['between_tick_policy'] == 'hold_complete_arrays', 'timing/scope differs')
    start, end = data['start_tick'], data['end_tick']
    require(integer(start, 0, 2**31-2) and integer(end, start, start+9999), 'range exceeds bound')
    frames = c['frames'];require(type(frames) is list and len(frames) == end-start+1, 'dense frames unavailable')
    total = 0
    for tick, frame in zip(range(start, end+1), frames):
        keys(frame, ('tick', 'root_count', 'systems'), 'light frame')
        require(type(frame['tick']) is int and frame['tick'] == tick and type(frame['systems']) is list
                and integer(frame['root_count'], 0, 8) and len(frame['systems']) == frame['root_count'], 'frame/root bound differs')
        for ordinal, system in enumerate(frame['systems']):
            keys(system, ('root_ordinal', 'child_path', 'state', 'particle_count', 'points'), 'light system')
            require(type(system['root_ordinal']) is int and system['root_ordinal'] == ordinal
                    and type(system['child_path']) is str and re.fullmatch(r'root/[0-7]', system['child_path'])
                    and integer(system['particle_count'], 0, 3) and type(system['points']) is list
                    and len(system['points']) == system['particle_count']
                    and system['state'] == ('captured' if system['particle_count'] else 'known_empty'), 'root/array profile differs')
            ids = []
            for point in system['points']:
                keys(point, ('particle_id', 'descriptor_raw'), 'light point')
                require(integer(point['particle_id'], 0, 2**31-1) and point['particle_id'] not in ids,
                        'duplicate/invalid point ID')
                ids.append(point['particle_id'])
                raw = point['descriptor_raw']
                require(type(raw) is str and len(raw) == 576 and re.fullmatch('[0-9a-f]{576}', raw),
                        'descriptor encoding differs')
                decode_descriptor(bytes.fromhex(raw));total += 1
                require(total <= 30000, 'point budget exceeded')
    return c


def build_reference(report, data, source, cleanup):
    from .observed_capture_read_profile import read_limit
    maximum_external_bytes = read_limit(report, source, cleanup)
    require(report.get('phase') == 'closed' and integer(report.get('launch_count'), 1, 1)
            and integer(report.get('exit_code'), 0, 0) and report.get('workflow_passed') is True
            and report.get('exact_target_coverage') is True and report.get('recordings') == []
            and not report.get('error') and not report.get('cleanup_errors')
            and not report.get('monitor_error') and not report.get('watchdog_fired')
            and not report.get('crash_inventory_changed')
            and all(report.get(k) is True for k in ('config_unchanged_after_cleanup',
                    'source_unchanged_after_run', 'retained_baseline_unchanged')), 'owned capture incomplete')
    pending = report.get('pending_runtime_restoration')
    require(type(pending) is dict and set(pending) == {'camera','playback','game_ui','replay_hud',
            'timing','resolution','layers','hero','speed','follow'} and not any(pending.values()),
            'runtime restoration incomplete')
    require(cleanup.get('status') == 'PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED'
            and cleanup.get('source_absent') is True and cleanup.get('runtime_restored') is True
            and cleanup.get('owned_native_removed_from_game') is True, 'owned archive unavailable')
    require(report.get('replay_sha256') == data.get('metadata', {}).get('demo_sha256'), 'replay differs')
    start, end = data['start_tick'], data['end_tick']
    require(integer(start, 0, 2**31-2) and integer(end, start, start+9999), 'raw range exceeds bound')
    observations = report.get('seeks')
    require(type(observations) is list and len(observations) == end-start+1, 'complete observations unavailable')
    mapping = report.get('initial_seek', {}).get('details', {})
    require(mapping.get('verified') is True and integer(mapping.get('game_tick_offset'), -2**31, 2**31-1),
            'verified seek mapping unavailable')
    frames, previous_bytes = [], 0
    for tick, obs in zip(range(start, end+1), observations):
        goal = obs.get('pause_goal', {});native = obs.get('native_capture', {})
        require(obs.get('target') == obs.get('after') == native.get('tick') == tick
                and goal.get('verified') is True and integer(goal.get('fresh_frames'), 2, 1000)
                and goal.get('no_final_seek') is True and goal.get('cleanup') == 'not_needed'
                and goal.get('target_tick') == goal.get('console_tick') == goal.get('final', {}).get('tick') == tick
                and goal.get('final', {}).get('paused') is True
                and goal.get('server_pause_tick') == tick+mapping['game_tick_offset'], 'exact paused tick unavailable')
        require(native.get('client_sha256') == NATIVE_DRIVER_DEFAULTS['client_sha256']
                and native.get('particles_sha256') == NATIVE_DRIVER_DEFAULTS['particles_sha256']
                and integer(native.get('external_bytes'), previous_bytes+1,maximum_external_bytes)
                and integer(native.get('read_bytes'), 1, 256*1024), 'native identity/read bounds differ')
        previous_bytes = native['external_bytes']
        actors = native.get('actors');require(type(actors) is list and len(actors) == 1, 'actor scope differs')
        actor = actors[0]
        require(actor.get('entity_index') == 56 and actor.get('handle') == 56+(895<<15), 'owner identity differs')
        effects = actor.get('effects');require(type(effects) is list and len(effects) <= 8, 'root scope differs')
        systems, roots = [], set()
        for ordinal, effect in enumerate(effects):
            require(effect.get('resource', {}).get('name', '').removesuffix('_c') == 'particles/generic/slide.vpcf'
                    and type(effect.get('collection')) is int and effect['collection'] > 0
                    and effect['collection'] not in roots, 'current root unavailable/aliased')
            roots.add(effect['collection'])
            tree = effect.get('declared_tree', {})
            require(tree.get('coherent') is True and integer(tree.get('checked_read_bytes'), 1, 96*1024)
                    and type(tree.get('nodes')) is list and len(tree['nodes']) <= 16, 'coherent tree unavailable')
            lights = [n for n in tree['nodes'] if n.get('resource') == RESOURCE]
            require(len(lights) == 1, 'complete light child unavailable')
            light = lights[0];count = light.get('particles')
            require(integer(count, 0, 3), 'light count bound')
            points = []
            if count:
                renderers = light.get('renderer_inputs')
                require(type(renderers) is list and len(renderers) == 1
                        and renderers[0].get('kind') == 'C_OP_RenderStandardLight', 'light renderer unavailable')
                packet = renderers[0].get('evaluated_light_outputs', {})
                outputs = packet.get('outputs')
                require(packet.get('state') == 'observed_light_outputs_not_render_frame_qualified'
                        and integer(packet.get('read_bytes'), 1, 3072)
                        and packet.get('maximum_read_bytes') == 3072
                        and type(outputs) is list and len(outputs) == count, 'evaluated output coverage differs')
                for output in outputs:
                    require(output.get('state') == 'observed_evaluated_point_light_descriptor',
                            'positive particle lacks evaluated scene light')
                    points.append({k: output[k] for k in ('particle_id', 'descriptor_raw')})
            systems.append(dict(root_ordinal=ordinal, child_path=light['path'],
                                state='captured' if count else 'known_empty', particle_count=count, points=points))
        frames.append(dict(tick=tick, root_count=len(effects), systems=systems))
    require(report.get('observer_read_bytes') == previous_bytes, 'aggregate counter differs')
    c = dict(schema_version=1, policy=POLICY, entity_id=OWNER, scene_identity=scene_identity(data, [OWNER]),
             native=dict(NATIVE_DRIVER_DEFAULTS), source=copy.deepcopy(source),
             between_tick_policy='hold_complete_arrays', frames=frames, scope=SCOPE)
    result = dict(capture=c, capture_sha256=canonical_sha256(c));validate_reference(result, data)
    return result


def prepare_package(data, reference):
    validate_reference(reference, data)
    package = dict(schema_version=1, backend=BACKEND, entity_ids=[OWNER], scene_identity=scene_identity(data, [OWNER]),
                   reference=copy.deepcopy(reference), assets={}, coverage_scope=SCOPE)
    validate_package(package, data)
    return package


def validate_package(package, data):
    keys(package, ('schema_version', 'backend', 'entity_ids', 'scene_identity', 'reference', 'assets', 'coverage_scope'), 'light package')
    require(type(package['schema_version']) is int and package['schema_version'] == 1 and package['backend'] == BACKEND
            and package['entity_ids'] == [OWNER] and package['scene_identity'] == scene_identity(data, [OWNER])
            and package['assets'] == {} and package['coverage_scope'] == SCOPE, 'package identity/assets differ')
    validate_reference(package['reference'], data)
