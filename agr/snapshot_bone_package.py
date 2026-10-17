"""Identity-bound transport for experimentally captured snapshot bone matrices.

Identity checks prevent stale/cross-entity reuse. They do not prove that a capture
implements the game's model-provider semantics or authenticate an edited scene.
"""
import copy
import hashlib
import json
import math
import re

from .simulated_effect_package import scene_identity

CONVENTION = 'column_vector_3x4_model_rest_to_world_source_units'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _identity(scene, entity_id):
    demo = scene.get('metadata', {}).get('demo_sha256', '')
    if not isinstance(demo, str) or not re.fullmatch('[0-9a-f]{64}', demo):
        raise ValueError('Snapshot binding requires replay SHA-256 identity')
    entities = scene.get('entities', [])
    if len({str(e['id']) for e in entities}) != len(entities):
        raise ValueError('Ambiguous duplicate scene entity identity')
    if not isinstance(entity_id, str) or not any(str(e['id']) == entity_id for e in entities):
        raise ValueError('Snapshot entity is absent from scene')
    return scene_identity(scene, [entity_id])


def _capture_valid(capture, scene):
    if capture.get('schema_version') != 1 or capture.get('matrix_convention') != CONVENTION:
        raise ValueError('Unsupported snapshot bone capture format')
    for field in ('start_tick', 'end_tick', 'tick_rate'):
        if capture.get(field) != scene.get(field):
            raise ValueError('Snapshot timing mismatch: '+field)
    start, end, rate = (capture[k] for k in ('start_tick', 'end_tick', 'tick_rate'))
    if type(start) is not int or type(end) is not int or end < start or end-start > 100000 or type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Invalid snapshot timing')
    coords = scene.get('metadata', {}).get('coordinate_system') or {}
    scale = coords.get('unit_scale', .0254)
    if (capture.get('unit_scale') != scale or type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0
            or coords.get('origin', [0, 0, 0]) != [0, 0, 0] or coords.get('world_yaw_degrees', 0) != 0):
        raise ValueError('Snapshot capture requires matching unshifted source coordinates')
    names = capture.get('bone_names', [])
    if not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('Invalid snapshot bone names')
    frames = capture.get('frames', [])
    if any(type(f.get('tick')) is not int for f in frames) or [f.get('tick') for f in frames] != list(range(start, end+1)):
        raise ValueError('Snapshot frames must cover every tick exactly once')
    for frame in frames:
        if set(frame.get('bones', {})) != set(names):
            raise ValueError('Snapshot frame bone set mismatch')
        for matrix in frame['bones'].values():
            if (not isinstance(matrix, list) or len(matrix) != 3 or
                    any(not isinstance(row, list) or len(row) != 4 for row in matrix) or
                    any(type(x) not in (int, float) or not math.isfinite(x) for row in matrix for x in row)):
                raise ValueError('Snapshot matrix must be a finite 3x4 array')
            gram_error = max(abs(sum(matrix[k][i]*matrix[k][j] for k in range(3))-(i == j))
                             for i in range(3) for j in range(3))
            a,b,c = matrix
            determinant = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
            if gram_error > 2e-4 or abs(determinant-1) > 2e-4:
                raise ValueError('Snapshot matrix is not a proper rigid transform')


def bind_snapshot_capture(capture, scene, entity_id, rig_fingerprint):
    """Bind a caller-produced capture; the export pipeline owns capture provenance."""
    _capture_valid(capture, scene)
    if not isinstance(rig_fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', rig_fingerprint):
        raise ValueError('Snapshot rig fingerprint must be SHA-256')
    payload = {'schema_version': 1, 'backend': 'experimental_snapshot_bones_v1',
               'entity_id': entity_id, 'scene_identity': _identity(scene, entity_id),
               'rig_fingerprint': rig_fingerprint, 'capture': copy.deepcopy(capture)}
    return {**payload, 'content_sha256': _digest(payload)}


def validate_snapshot_capture(package, scene, entity_id, rig_fingerprint):
    if package.get('schema_version') != 1 or package.get('backend') != 'experimental_snapshot_bones_v1':
        raise ValueError('Unsupported snapshot bone package')
    if package.get('entity_id') != entity_id or package.get('rig_fingerprint') != rig_fingerprint:
        raise ValueError('Snapshot entity/rig binding mismatch')
    if package.get('scene_identity') != _identity(scene, entity_id):
        raise ValueError('Snapshot replay, timing, tracks or rig inputs changed; regenerate capture')
    if package.get('content_sha256') != _digest({k:v for k,v in package.items() if k != 'content_sha256'}):
        raise ValueError('Snapshot capture contents changed')
    _capture_valid(package['capture'], scene)
