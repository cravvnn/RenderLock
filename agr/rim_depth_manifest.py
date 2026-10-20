"""Research-only identity gate for saved-scene, frame-specific Cycles depth.

This binds a saved baseline and projection, not arbitrary unsaved geometry,
visibility, modifier or material edits. Capture must precede those edits.
"""
import hashlib
import io
import json
from pathlib import Path

import numpy as np


ENCODING = 'top-down axial camera depth in Source units; Cycles Z / unit_scale'
CYCLES_BACKGROUND = 'cycles_z_miss_1e10_reference'


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(*, scene_sha256, frame, camera, source_row_matrix, width, height,
             pixel_aspect, coordinates, blender_version, engine, camera_clip_source=None):
    if (not isinstance(scene_sha256, str) or len(scene_sha256) != 64
            or any(c not in '0123456789abcdef' for c in scene_sha256)):
        raise ValueError('Invalid saved-scene hash')
    if type(frame) is not int or any(type(v) is not int or v <= 0 for v in (width, height)):
        raise ValueError('Integer frame and positive dimensions required')
    matrix = np.asarray(source_row_matrix, dtype=float)
    aspect = np.asarray(pixel_aspect, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or np.linalg.matrix_rank(matrix) != 4:
        raise ValueError('Finite invertible projection required')
    if aspect.shape != (2,) or not np.isfinite(aspect).all() or np.any(aspect <= 0):
        raise ValueError('Invalid pixel aspect')
    if not camera or not isinstance(camera, str) or not blender_version or not isinstance(blender_version, str) or engine != 'CYCLES':
        raise ValueError('Named camera, Blender version and Cycles required')
    if set(coordinates) != {'unit_scale', 'origin', 'world_yaw_degrees', 'model_yaw_degrees'}:
        raise ValueError('Explicit coordinate contract required')
    origin = np.asarray(coordinates['origin'], dtype=float)
    scalars = np.asarray([coordinates[k] for k in ('unit_scale', 'world_yaw_degrees', 'model_yaw_degrees')], dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all() or not np.isfinite(scalars).all() or scalars[0] <= 0:
        raise ValueError('Invalid coordinate contract')
    result = dict(scene_sha256=scene_sha256, frame=frame, camera=camera,
                  source_row_matrix=matrix.tolist(), width=width, height=height,
                  pixel_aspect=aspect.tolist(), coordinates=coordinates,
                  blender_version=blender_version, engine=engine)
    if camera_clip_source is not None:
        clipping = np.asarray(camera_clip_source, dtype=float)
        if (clipping.shape != (2,) or not np.isfinite(clipping).all()
                or not 0 < clipping[0] < clipping[1] < 1e8):
            raise ValueError('Bounded positive camera clipping range required')
        result['camera_clip_source'] = clipping.tolist()
    return json.loads(json.dumps(result, allow_nan=False))


def _read_depth(path, expected):
    # Hash and load the same bytes so replacement cannot separate the checks.
    payload = Path(path).read_bytes()
    array = np.load(io.BytesIO(payload), allow_pickle=False)
    if (not isinstance(array, np.ndarray) or array.dtype.kind != 'f'
            or array.shape != (expected['height'], expected['width'])
            or not np.isfinite(array).all() or np.any(array <= 0)):
        raise ValueError('Depth must be positive finite floating point with matching viewport')
    return array, hashlib.sha256(payload).hexdigest()


def background_policy(array, captured_identity):
    """Qualify the explicit Cycles no-hit reference, without engine parity.

    Preserve its measured Z=1e10 value. Only actual hits inside this camera's
    clipping interval and that exact no-hit value are accepted. Arbitrary large
    depths are not background. The payload hash authenticates all classifications.
    """
    clipping = captured_identity.get('camera_clip_source')
    if clipping is None:
        raise ValueError('Camera clipping identity required for background reference')
    near, far = clipping
    miss = float(np.float32(1e10)) / captured_identity['coordinates']['unit_scale']
    background = array == miss
    # Z is stored in float32 before unit conversion; tolerate that rounding only.
    hit = (array >= near * (1 - 2e-6)) & (array <= far * (1 + 2e-6))
    if not np.all(background | hit):
        raise ValueError('Depth contains an unqualified hit or background value')
    return dict(kind=CYCLES_BACKGROUND, background_source=miss,
                background_pixels=int(np.count_nonzero(background)),
                camera_clip_source=list(clipping))


def make_manifest(depth_path, captured_identity, *, allow_cycles_background=False):
    checked = identity(**captured_identity)
    array, digest = _read_depth(depth_path, checked)
    manifest = dict(schema=1, encoding=ENCODING, identity=checked,
                    depth_sha256=digest, dtype=array.dtype.str)
    if allow_cycles_background:
        manifest.update(schema=2, background_policy=background_policy(array, checked))
    return manifest


def load_verified(depth_path, manifest, current_identity, *, return_policy=False):
    """Reject stale identity and substituted depth before returning array data.

    Caller must freshly hash the saved baseline and capture current projection.
    A historical manifest supplied by itself is not current-state verification.
    """
    current = identity(**current_identity)
    keys = {'schema', 'encoding', 'identity', 'depth_sha256', 'dtype'}
    schema = manifest.get('schema')
    if schema == 2:
        keys.add('background_policy')
    if set(manifest) != keys or type(schema) is not int or schema not in (1, 2) or manifest['encoding'] != ENCODING:
        raise ValueError('Unsupported depth manifest')
    recorded = identity(**manifest['identity'])
    if recorded != current:
        changed = [key for key in current if recorded[key] != current[key]]
        raise ValueError('Stale depth identity: ' + ', '.join(changed))
    array, digest = _read_depth(depth_path, current)
    if digest != manifest['depth_sha256'] or array.dtype.str != manifest['dtype']:
        raise ValueError('Depth payload identity mismatch')
    policy = None
    if schema == 2:
        policy = background_policy(array, current)
        if manifest['background_policy'] != policy:
            raise ValueError('Depth background policy mismatch')
    return (array, policy) if return_policy else array
