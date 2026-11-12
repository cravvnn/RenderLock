"""Bounded, same-run native trail uploads; no inferred particle lifetimes.

Only the measured slide_streaks profile is admitted. Digests detect accidental
changes, not malicious forgery. This component remains partial reconstruction.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
from .client_modifier_writes import integer
from .replay_capture import canonical_sha256
from .captured_slide_binding import _source_scene_identity as scene_identity
from .observed_slide_profile import keys, digest, finite
from .observed_slide_sprite_states import OWNER, ROOT, validate_reference as validate_sprite_reference
from .observed_slide_profile import NATIVE_DRIVER_DEFAULTS

BACKEND = 'observed_slide_trail_frames_v1'
POLICY = 'agr_complete_native_slide_trail_uploads_v1'
RESOURCE = 'particles/generic/slide_streaks.vpcf'
TEXTURE = 'materials/particle/beams/beam_generic04.vtex'
SOURCE_SHA = '4bcded1316e0c9436aba091f51bb1202455ba8666b50e52da7da92c5aebb9574'
DEFINITION_SHA = '9e0e9dd4a2e297a06f0bcdd502d2d43c5deb67cc11e5c13bbc739ab2c6545fb5'
TEXTURE_SHA = 'b9c1e57879e795f41e0ef27ed79875b0a97f7e63c2e46eba4b141d9e53944599'
IMAGE_SHA = 'c22f918e776273e4569f888f8adca9c27786a0be5c5065dd21c257bfed7f4b40'
INPUTS_SHA = '37214cbbaa2a22bbdff04eca0d046790411a2299dd0be0b6e54e59f8c7cebe23'
LAYOUT_SHA = 'd76ce3db9d8a656623cfe1a323e7148aad7f90c6ee81c966eeb3a083368555d3'
UPLOAD_HELPER_SHA = 'ca9e5d5f3b1baf2e95d7e0c6c7b9f7ae7cd2b5c0b8d6d79ebafc6930fb5a5725'
PROJECTION_HELPER_SHA = '73c35f5be7fd21607fba11b564811943bff79a04f90b97a1b919f5f238e7ed67'
QUALIFIED_HELPER_PAIRS = {
    (UPLOAD_HELPER_SHA, PROJECTION_HELPER_SHA),
    # Same native gather/upload bodies, qualified against evaluated-light04's
    # complete independent cohort (184 points, not the old cohort's183).
    ('756111028961c88bbdeb0712662b11c3e4cd8a5eb8e709ab1cafd9cbe222be98',
     'd99c07a7b21e8c663f47f969d2663c31d26ed57886cd3bf00260ad2cc81e055d'),
    # DenseMaterial02: same native bodies, current full 321-tick capture;
    # all 175 points/51 positive collections replayed through native code.
    ('04627237b9f26fcc6d72eae589d5e586b0fed02a12fc487cbc734831f9162136',
     'ad08044b397b2fb76790ed74ba394afd34c53a985c038fbc0b46cdf98efda865'),
}
SCOPE = ('Measured native slide trail geometry/UV/color component for Venator56:895 current properties. '
         'Complete frame-local arrays hold between native ticks; no root/particle lifetime joins or births inferred. '
         'Camera edits require rebaking. Two model emitters, start light, Mina, orphan/endcap/history, common hero/render '
         'clock, Source lighting/fog/depth feathering and GPU/pixel/subframe parity remain unqualified.')
SOURCE_KEYS = {'report_sha256', 'sprite_capture_sha256', 'upload_file_sha256', 'projection_file_sha256',
               'upload_capture_sha256', 'projection_capture_sha256', 'layout_sha256',
               'upload_helper_sha256', 'projection_helper_sha256'}
POINT_KEYS = {'controls_xyzw', 'endpoint_rgba', 'crop_flip_rgba', 'native_animation_input', 'native_random'}


def require(value, message):
    if not value:
        raise ValueError('Observed slide trails: '+message)


def renderer_input_sha(value):
    """Ignore only the three unused bytes after the native bool at3370.

    The actual caller2de6ae reads that flag as one byte; subsequent controls
    begin3374. Both complete native gathers/uploads were qualified with their
    distinct captured padding. All consumed fields and provider data stay bound.
    """
    require(isinstance(value, dict), 'renderer inputs unavailable')
    normalized = copy.deepcopy(value)
    raw = normalized.get('trail_controls_raw')
    require(isinstance(raw, str) and re.fullmatch('[0-9a-f]{56}', raw), 'trail controls unavailable')
    controls = bytearray.fromhex(raw);controls[13:16] = b'\0'*3
    normalized['trail_controls_raw'] = controls.hex()
    return canonical_sha256(normalized)


def validate_assets(assets):
    require(isinstance(assets, dict) and assets.get('models') == {}
            and set(assets.get('definitions', {})) == {RESOURCE}
            and set(assets.get('textures', {})) == {TEXTURE}, 'asset/profile coverage differs')
    from .particle_sheet_uv import validate_shader_inputs
    validate_shader_inputs(assets.get('provenance', {}).get('spritecard_sheet_uv'))
    pins = assets['provenance'].get('input_sha256', {})
    require(pins.get(RESOURCE) == SOURCE_SHA and pins.get(TEXTURE) == TEXTURE_SHA
            and canonical_sha256(assets['definitions'][RESOURCE]) == DEFINITION_SHA, 'authored source/definition differs')
    texture = assets['textures'][TEXTURE]
    require(texture.get('status') == 'supported' and texture.get('atlas') is False
            and texture.get('is_sprite_sheet') is False and texture.get('reasons') == [], 'static texture unavailable')
    sequence = texture.get('sequences', {}).get('0', {})
    frames = sequence.get('frames')
    require(isinstance(frames, list) and len(frames) == 1
            and sequence.get('status') == 'supported'
            and frames[0] == dict(image='beam_generic04.png', duration=1., width=64, height=128, sha256=IMAGE_SHA)
            and set(texture.get('sequences', {})) == {'0'}
            and texture.get('images') == {'beam_generic04.png': dict(width=64, height=128, sha256=IMAGE_SHA)},
            'texture frame identity differs')
    return assets['definitions'][RESOURCE]['m_Renderers'][0]


def validate_reference(envelope, data):
    keys(envelope, ('capture', 'capture_sha256'), 'trail envelope')
    c = envelope['capture']
    require(digest(envelope['capture_sha256']) and canonical_sha256(c) == envelope['capture_sha256'], 'reference digest differs')
    keys(c, ('schema_version', 'policy', 'entity_id', 'scene_identity', 'native', 'sprite_reference',
             'source', 'between_tick_policy', 'frames', 'scope'), 'trail capture')
    require(type(c['schema_version']) is int and c['schema_version'] == 1 and c['policy'] == POLICY
            and c['entity_id'] == OWNER and c['native'] == NATIVE_DRIVER_DEFAULTS
            and c['scene_identity'] == scene_identity(data, [OWNER]), 'schema/native/scene identity differs')
    base = validate_sprite_reference(c['sprite_reference'], data)
    source = c['source'];keys(source, SOURCE_KEYS, 'trail provenance')
    require(all(digest(v) for v in source.values()) and source['report_sha256'] == base['source']['report_sha256']
            and source['sprite_capture_sha256'] == c['sprite_reference']['capture_sha256']
            and source['layout_sha256'] == LAYOUT_SHA
            and (source['upload_helper_sha256'], source['projection_helper_sha256']) in QUALIFIED_HELPER_PAIRS,
            'same-run/helper/layout provenance differs')
    require(c['scope'] == SCOPE and c['between_tick_policy'] == 'hold_complete_arrays', 'coverage/timing policy differs')
    frames = c['frames'];require(isinstance(frames, list) and len(frames) == len(base['samples']), 'dense frames unavailable')
    total = 0
    for frame, sample in zip(frames, base['samples']):
        keys(frame, ('tick', 'systems'), 'trail frame')
        require(type(frame['tick']) is int and frame['tick'] == sample['tick'], 'frame tick differs')
        systems = frame['systems']
        require(isinstance(systems, list) and len(systems) == sample['root_count'], 'current root coverage differs')
        for ordinal, system in enumerate(systems):
            require(isinstance(system, dict), 'system unavailable')
            points = system.get('points')
            require(isinstance(points, list) and len(points) <= 64, 'point budget/array unavailable')
            expected = ('root_ordinal', 'child_path', 'state', 'points')
            if points:
                expected += ('collection_time', 'renderer_inputs')
            keys(system, expected, 'trail system')
            require(type(system['root_ordinal']) is int and system['root_ordinal'] == ordinal
                    and isinstance(system['child_path'], str) and re.fullmatch('root/1', system['child_path']) is not None,
                    'frame-local root/child differs')
            require(system['state'] == ('captured' if points else 'known_empty'), 'unavailable is not empty')
            if points:
                require(finite(system['collection_time'], 36000)
                    and renderer_input_sha(system['renderer_inputs']) == INPUTS_SHA, 'unqualified native renderer inputs')
            total += len(points);require(total <= 200000, 'total point budget exceeded')
            for p in points:
                keys(p, POINT_KEYS, 'trail point')
                controls = p['controls_xyzw']
                require(isinstance(controls, list) and len(controls) == 4
                        and all(isinstance(v, list) and len(v) == 4 and all(finite(x) for x in v)
                                and v[3] >= 0 for v in controls), 'native control/radius invalid')
                require(isinstance(p['endpoint_rgba'], list) and len(p['endpoint_rgba']) == 2
                        and all(isinstance(row, list) and len(row) == 4 and all(integer(v, 0, 255) for v in row)
                                for row in p['endpoint_rgba']), 'native endpoint bytes invalid')
                require(isinstance(p['crop_flip_rgba'], list) and len(p['crop_flip_rgba']) == 4
                        and all(integer(v, 0, 255) for v in p['crop_flip_rgba']), 'native UV bytes unavailable')
                require(finite(p['native_animation_input']) and finite(p['native_random'])
                        and 0 <= p['native_random'] <= 1, 'native animation/random invalid')
    return c


def _read_packet(path):
    # Hard read cap applies even if the file grows after stat. No input is run.
    with Path(path).open('rb') as stream:
        raw = stream.read(8*1024**2+1)
    require(len(raw) <= 8*1024**2, 'proof file exceeds budget')
    packet = json.loads(raw)
    keys(packet, ('capture', 'capture_sha256'), 'native packet')
    require(digest(packet['capture_sha256']) and canonical_sha256(packet['capture']) == packet['capture_sha256'],
            'native packet digest differs')
    return packet, hashlib.sha256(raw).hexdigest()


def build_reference(data, sprite_reference, upload_file, projection_file, layout_file):
    """Admit retained native projection files tied to the audited sprite run.

    The sprite reference owns replay/range/cleanup/root completeness. A trail
    upload from a different capture cannot be stitched into that proof.
    """
    base = validate_sprite_reference(sprite_reference, data)
    upload, upload_sha = _read_packet(upload_file)
    projection, projection_sha = _read_packet(projection_file)
    u, p = upload['capture'], projection['capture']
    with Path(layout_file).open('rb') as stream:
        layout = stream.read(65537)
    require(len(layout) <= 65536 and hashlib.sha256(layout).hexdigest() == LAYOUT_SHA, 'native layout proof differs')
    require(u.get('policy') == 'private_native_trail_upload_v1' and p.get('policy') == 'private_native_slide_trail_gather_v1'
            and u.get('entity_id') == p.get('entity_id') == OWNER
            and u.get('particles_sha256') == p.get('particles_sha256') == NATIVE_DRIVER_DEFAULTS['particles_sha256']
            and (u.get('source_script_sha256'), p.get('source_script_sha256')) in QUALIFIED_HELPER_PAIRS
            and u.get('source_projection_sha256') == projection_sha
            and p.get('source_report_sha256') == base['source']['report_sha256'], 'projection/upload capture provenance differs')
    require(isinstance(u.get('frames'), list) and isinstance(p.get('frames'), list)
            and len(u['frames']) == len(p['frames']) == len(base['samples']), 'projected dense frames differ')
    for uf, pf in zip(u['frames'], p['frames']):
        require(uf.get('tick') == pf.get('tick') and isinstance(uf.get('systems'), list)
                and isinstance(pf.get('systems'), list) and len(uf['systems']) == len(pf['systems']), 'projected frame differs')
        for us, ps in zip(uf['systems'], pf['systems']):
            require(all(us.get(k) == ps.get(k) for k in ('root_ordinal', 'child_path', 'state', 'collection_time', 'renderer_inputs'))
                    and isinstance(us.get('points'), list) and isinstance(ps.get('points'), list)
                    and len(us['points']) == len(ps['points']), 'projected system differs')
            for up, pp in zip(us['points'], ps['points']):
                require([v[:3] for v in up['controls_xyzw']] == pp['controls_xyz']
                        and all(v[3] == pp['radius'] for v in up['controls_xyzw'])
                        and up['endpoint_rgba'][0] == pp['color_bytes']
                        and all(up[k] == pp[k] for k in ('native_animation_input', 'native_random')), 'uploaded point differs')
    source = dict(report_sha256=base['source']['report_sha256'], sprite_capture_sha256=sprite_reference['capture_sha256'],
                  upload_file_sha256=upload_sha, projection_file_sha256=projection_sha,
                  upload_capture_sha256=upload['capture_sha256'], projection_capture_sha256=projection['capture_sha256'],
                  layout_sha256=LAYOUT_SHA, upload_helper_sha256=u['source_script_sha256'],
                  projection_helper_sha256=p['source_script_sha256'])
    c = dict(schema_version=1, policy=POLICY, entity_id=OWNER, scene_identity=scene_identity(data, [OWNER]),
             native=dict(NATIVE_DRIVER_DEFAULTS), sprite_reference=copy.deepcopy(sprite_reference), source=source,
             between_tick_policy='hold_complete_arrays', frames=copy.deepcopy(u['frames']), scope=SCOPE)
    result = dict(capture=c, capture_sha256=canonical_sha256(c))
    validate_reference(result, data)
    return result


def prepare_package(data, reference, assets):
    validate_reference(reference, data);validate_assets(assets)
    result = dict(schema_version=1, backend=BACKEND, entity_ids=[OWNER], scene_identity=scene_identity(data, [OWNER]),
                  reference=copy.deepcopy(reference), assets=copy.deepcopy(assets), assets_sha256=canonical_sha256(assets), coverage_scope=SCOPE)
    validate_package(result, data)
    return result


def validate_package(package, data):
    keys(package, ('schema_version', 'backend', 'entity_ids', 'scene_identity', 'reference', 'assets', 'assets_sha256', 'coverage_scope'), 'trail package')
    require(type(package['schema_version']) is int and package['schema_version'] == 1 and package['backend'] == BACKEND
            and package['entity_ids'] == [OWNER] and package['scene_identity'] == scene_identity(data, [OWNER]), 'package scene/backend differs')
    validate_reference(package['reference'], data);validate_assets(package['assets'])
    require(package['assets_sha256'] == canonical_sha256(package['assets']) and package['coverage_scope'] == SCOPE, 'package asset/scope digest differs')
