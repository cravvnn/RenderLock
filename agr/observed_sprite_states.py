"""Complete native frames for two pinned ambient sprite systems.

Array slots never become particle lifetimes. Native random floats and manual
animation fields are measured inputs, not replacements derived from a fake seed
or inferred birth. Portable digests detect changes, not malicious forgery.
"""
import copy
import math
import struct
import numpy as np
from .replay_capture import canonical_sha256
from .captured_slide_binding import _source_scene_identity as scene_identity
from .observed_slide_profile import NATIVE_DRIVER_DEFAULTS
from .client_modifier_writes import integer

BACKEND = 'observed_sprite_frames_v1'
POLICY = 'agr_complete_native_ambient_sprite_arrays_v1'
OWNER = '56:895'
ROOT = 'particles/abilities/priest/priest_ambient.vpcf'
PROFILES = {
    'particles/abilities/priest/priest_ambient_censer_smoke_base.vpcf': (
        '54a125b84d5e7d212c939886ecc6df6e4627dce9b12ce4cbd5d0b1de5e1a4203',
        '36d7707b914221ec1a726895688b9510a0472e23fb4b5152f023ac4ba2a2d762'),
    'particles/abilities/priest/priest_ambient_smoke_tip.vpcf': (
        'ab2d05c42cfb3997cd75d7e342917af617c741a91fa230a4ffee8e28bd04de0b',
        '8461a84fb1d30b26781c28d92d1ba715b5d1b948ead2b12f2e3e912ada11b955'),
}
RAW_FIELDS = {0: 3, 3: 1, 4: 1, 6: 3, 7: 1, 8: 1, 9: 1, 11: 1,
              12: 1, 13: 1, 16: 1, 20: 1, 38: 1, 39: 1, 40: 1}
SOURCE_KEYS = {'report_sha256', 'observer_sha256', 'field_reader_sha256',
               'freeze_sha256', 'cleanup_sha256', 'native_inputs_sha256',
               'native_defaults_sha256'}
INPUT_POLICY = dict(random='native_sprite_gather_table_4096_v1', alpha2_field=16,
                    animation='native_manual_frames_attribute38_v1',
                    collection_time='native_scalar_clock50_zero_offset_v1')
POINT_KEYS = {'position', 'radius', 'rotation', 'color_bytes', 'sequence',
              'manual_frame', 'shader_extra_data1', 'native_random'}


def require(value, message):
    if not value:
        raise ValueError('Observed sprite arrays: '+message)


def keys(value, expected, label):
    require(isinstance(value, dict) and set(value) == set(expected), label+' fields differ')


def digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def finite(value, bound=1e8):
    return type(value) in (float, int) and math.isfinite(value) and abs(value) <= bound


def native_color_bytes(rgb, alpha, alpha2):
    """Installed gather's float32 UNORM packing, before renderer alpha scale.

    Addition of 2**23 rounds to integer using native nearest-even float32. Its
    low byte becomes the channel. The admitted RGB range excludes wraparound;
    alpha is the native clamped product of attributes7 and16.
    """
    require(isinstance(rgb, list) and len(rgb) == 3
            and all(finite(v) and 0 <= v <= 1 for v in rgb)
            and finite(alpha) and finite(alpha2), 'unqualified native color range')
    f = np.float32
    values = [f(v) for v in rgb]+[f(min(f(1), max(f(0), f(f(alpha)*f(alpha2)))))]
    return [int(np.asarray(f(f(v*f(255))+f(8388608)), dtype='<f4').view('<u4')) & 255
            for v in values]


def validate_profiles(assets):
    require(type(assets) is dict and assets.get('models', {}) == {}, 'unexpected sprite model assets')
    definitions = assets.get('definitions', {})
    require(set(definitions) == set(PROFILES), 'authored sprite coverage differs')
    pins = assets.get('provenance', {}).get('input_sha256', {})
    from .particle_sheet_uv import validate_shader_inputs
    validate_shader_inputs(assets.get('provenance', {}).get('spritecard_sheet_uv'))
    from .particle_texture_animation import validate_sheet
    for resource, (source, definition) in PROFILES.items():
        require(pins.get(resource) == source and canonical_sha256(definitions[resource]) == definition,
                'authored native sprite definition changed')
    for name, texture in assets.get('textures', {}).items():
        if texture.get('is_sprite_sheet'):
            sheet = validate_sheet(texture.get('source_sheet'))
            require(sheet.get('input_sha256') == pins.get(name), 'original sprite sheet identity differs')
    return definitions


def validate_reference(envelope, data):
    keys(envelope, ('capture', 'capture_sha256'), 'envelope')
    c = envelope['capture']
    require(digest(envelope['capture_sha256']) and canonical_sha256(c) == envelope['capture_sha256'],
            'portable reference digest differs')
    keys(c, ('schema_version', 'policy', 'demo_sha256', 'scene_identity', 'entity_id',
             'start_tick', 'end_tick', 'inclusive', 'native', 'profiles', 'source',
             'input_policy', 'between_tick_policy', 'samples', 'scope'), 'capture')
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
        for name, (source, definition) in PROFILES.items()}, 'native module/profile pair differs')
    keys(c['source'], SOURCE_KEYS, 'source provenance')
    require(all(digest(v) for v in c['source'].values()), 'source provenance missing')
    require(c['input_policy'] == INPUT_POLICY and c['between_tick_policy'] == 'hold_complete_arrays'
            and isinstance(c['scope'], str) and 0 < len(c['scope']) <= 2048, 'input policy/scope differs')
    samples = c['samples']
    require(isinstance(samples, list) and len(samples) == c['end_tick']-c['start_tick']+1,
            'native frame reference is not complete/dense')
    total = 0
    for tick, sample in zip(range(c['start_tick'], c['end_tick']+1), samples):
        keys(sample, ('tick', 'collections'), 'frame')
        require(type(sample['tick']) is int and sample['tick'] == tick, 'native frame tick differs')
        rows = sample['collections']
        require(isinstance(rows, list) and len(rows) == 2
                and [r.get('resource') for r in rows if isinstance(r, dict)] == sorted(PROFILES),
                'frame collection coverage differs')
        for row in rows:
            keys(row, ('resource', 'state', 'count', 'points', 'collection_time', 'raw_flags'), 'collection')
            require(integer(row['count'], 0, 64) and isinstance(row['points'], list)
                    and len(row['points']) == row['count']
                    and row['state'] == ('captured' if row['count'] else 'known_empty'),
                    'unknown/truncated native array is not empty')
            require(finite(row['collection_time'], 36000) and integer(row['raw_flags'], 0, 255),
                    'native collection clock/flags invalid')
            total += row['count']; require(total <= 200000, 'reference point budget exceeded')
            for p in row['points']:
                keys(p, POINT_KEYS, 'point')
                require(all(isinstance(p[k], list) and len(p[k]) == 3 and all(finite(v) for v in p[k])
                            for k in ('position', 'rotation')), 'point vector missing/nonfinite')
                require(finite(p['radius']) and p['radius'] >= 0 and finite(p['manual_frame'])
                        and finite(p['shader_extra_data1']) and finite(p['native_random'])
                        and 0 <= p['native_random'] <= 1 and integer(p['sequence'], 0, 32767),
                        'point renderer input missing/nonfinite')
                require(isinstance(p['color_bytes'], list) and len(p['color_bytes']) == 4
                        and all(integer(v, 0, 255) for v in p['color_bytes']), 'native RGBA8 missing')
    return c


def _point_rows(node):
    count = node['particles']; require(integer(count, 0, 64), 'raw point count invalid')
    keys(node['fields'], (str(f) for f in RAW_FIELDS) if count else (), 'raw attributes')
    fields = {}
    for f, components in RAW_FIELDS.items() if count else ():
        entry = node['fields'][str(f)]
        keys(entry, ('components', 'stride', 'raw_hex'), 'raw attribute')
        stride = entry['stride']
        require(type(stride) is int and stride in (0, components*4)
                and type(entry['components']) is int and entry['components'] == components and isinstance(entry['raw_hex'], str),
                'raw SIMD format differs')
        last_lane = min(count-1, 3) if stride == 0 else (count-1) % 4
        length = ((count-1)//4*stride+(components-1)*4+last_lane+1)*4
        require(len(entry['raw_hex']) == length*2, 'raw SIMD length differs')
        fields[f] = stride, bytes.fromhex(entry['raw_hex'])
    def value(f, i, axis=0):
        stride, raw = fields[f]
        return struct.unpack_from('<f', raw, ((i//4)*stride+axis*4+i % 4)*4)[0]
    inputs = node['sprite_inputs']
    require(inputs.get('random_policy') == INPUT_POLICY['random'] and inputs.get('random_draw_offset') == 235
            and isinstance(inputs.get('native_random_values'), list)
            and len(inputs['native_random_values']) == count, 'native shader random inputs unavailable')
    points = []
    for i in range(count):
        sequence = value(9, i)
        require(finite(sequence), 'native sequence nonfinite')
        sequence = int(np.rint(np.float32(sequence)))
        require(0 <= sequence <= 32767, 'native sequence outside qualified int16 range')
        points.append(dict(position=[value(0, i, c) for c in range(3)], radius=value(3, i),
            rotation=[value(f, i) for f in (12, 20, 4)], sequence=sequence, manual_frame=value(38, i),
            color_bytes=native_color_bytes([value(6, i, c) for c in range(3)], value(7, i), value(16, i)),
            shader_extra_data1=value(39, i), native_random=inputs['native_random_values'][i]))
    return points


def build_reference(report, data, source, cleanup):
    require(integer(data.get('start_tick'), 0, 2**31-2)
            and integer(data.get('end_tick'), data['start_tick'], 2**31-2)
            and data['end_tick']-data['start_tick'] < 10000, 'source frame range exceeds bound')
    require(report.get('phase') == 'closed' and type(report.get('launch_count')) is int
            and report['launch_count'] == 1 and type(report.get('exit_code')) is int and report['exit_code'] == 0
            and report.get('workflow_passed') is True and report.get('exact_target_coverage') is True
            and report.get('recordings') == [] and not report.get('error') and not report.get('cleanup_errors')
            and not report.get('monitor_error') and not report.get('watchdog_fired')
            and not report.get('crash_inventory_changed') and report.get('config_unchanged_after_cleanup') is True
            and report.get('source_unchanged_after_run') is True and report.get('retained_baseline_unchanged') is True
            and not any(report.get('pending_runtime_restoration', {}).values()), 'owned run incomplete/not restored')
    require(cleanup.get('status') == 'PASS_EXACT_OWNED_DEPLOYMENT_ARCHIVED'
            and cleanup.get('source_absent') is True and cleanup.get('runtime_restored') is True
            and cleanup.get('owned_native_removed_from_game') is True, 'owned deployment not archived')
    require(report.get('replay_sha256') == data['metadata']['demo_sha256'], 'source replay differs')
    observations = report.get('seeks')
    require(isinstance(observations, list) and len(observations) == data['end_tick']-data['start_tick']+1,
            'owned run lacks complete dense frames')
    samples = []
    for tick, obs in zip(range(data['start_tick'], data['end_tick']+1), observations):
        require(type(obs.get('target')) is int and type(obs.get('after')) is int
                and obs['target'] == obs['after'] == tick, 'observation tick differs')
        native = obs['native_capture']; actors = native.get('actors')
        require(type(native.get('tick')) is int and native['tick'] == tick
                and native.get('client_sha256') == NATIVE_DRIVER_DEFAULTS['client_sha256']
                and native.get('particles_sha256') == NATIVE_DRIVER_DEFAULTS['particles_sha256'],
                'native observation identity differs')
        require(isinstance(actors, list) and len(actors) == 1, 'native owner coverage differs')
        actor = actors[0]
        require(type(actor.get('entity_index')) is int and actor['entity_index'] == 56
                and type(actor.get('handle')) is int and actor['handle'] == 56+(895 << 15)
                and type(actor.get('life_state')) is int and actor['life_state'] == 0, 'native owner differs')
        effects = actor.get('effects')
        require(isinstance(effects, list) and len(effects) == 1
                and effects[0]['resource']['name'].removesuffix('_c') == ROOT, 'native root missing/ambiguous')
        tree = effects[0]['declared_tree']
        require(tree.get('coherent') is True and isinstance(tree.get('nodes'), list)
                and 1 <= len(tree['nodes']) <= 16, 'native graph unavailable')
        rows = []
        for resource in sorted(PROFILES):
            nodes = [n for n in tree['nodes'] if n.get('resource') == resource]
            require(len(nodes) == 1, 'native sprite child missing/ambiguous')
            n = nodes[0]
            require(isinstance(n.get('clock_50_raw'), str) and len(n['clock_50_raw']) == 80,
                    'native collection time unavailable')
            rows.append(dict(resource=resource, state=n['field_state'], count=n['particles'], points=_point_rows(n),
                collection_time=struct.unpack_from('<f', bytes.fromhex(n['clock_50_raw']))[0], raw_flags=n['flags_8c9']))
        samples.append(dict(tick=tick, collections=rows))
    c = dict(schema_version=1, policy=POLICY, demo_sha256=report['replay_sha256'], entity_id=OWNER,
        scene_identity=scene_identity(data, [OWNER]), start_tick=data['start_tick'], end_tick=data['end_tick'], inclusive=True,
        native=dict(NATIVE_DRIVER_DEFAULTS), profiles={n: dict(source_sha256=a, definition_sha256=b) for n, (a, b) in PROFILES.items()},
        source=copy.deepcopy(source), input_policy=dict(INPUT_POLICY), between_tick_policy='hold_complete_arrays', samples=samples,
        scope='Complete native arrays for two ambient sprite systems/three renderers. Array slots are frame-local geometry, '
              'not particle lifetimes. Manual field38, shader extra39, collection clock and native shader random floats are observed. '
              'No fake birth/age/seed/ID is used. Array frames hold between captured ticks. Other layers, root/endcap/orphan tails, '
              'Source shaders/lighting, pixel/subframe visibility and hero render-clock alignment remain separate.')
    result = dict(capture=c, capture_sha256=canonical_sha256(c)); validate_reference(result, data)
    return result


def prepare_package(data, reference, assets):
    validate_reference(reference, data); validate_profiles(assets)
    return dict(schema_version=1, backend=BACKEND, entity_ids=[OWNER], scene_identity=scene_identity(data, [OWNER]),
                reference=copy.deepcopy(reference), assets=copy.deepcopy(assets), assets_sha256=canonical_sha256(assets),
                coverage_scope=reference['capture']['scope'])


def validate_package(package, data):
    keys(package, ('schema_version', 'backend', 'entity_ids', 'scene_identity',
                   'reference', 'assets', 'assets_sha256', 'coverage_scope'), 'package')
    require(type(package.get('schema_version')) is int and package['schema_version'] == 1
            and package.get('backend') == BACKEND and package.get('entity_ids') == [OWNER]
            and package.get('scene_identity') == scene_identity(data, [OWNER]), 'package scene identity differs')
    validate_reference(package.get('reference'), data)
    require(canonical_sha256(package.get('assets')) == package.get('assets_sha256'), 'asset descriptor digest differs')
    validate_profiles(package['assets'])
    require(package.get('coverage_scope') == package['reference']['capture']['scope'], 'package scope differs')
