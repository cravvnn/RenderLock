"""Authored hitbox bounds used by the native particle model-input path.

Pure geometry and explicit snapshot binding only: engine entity/model selection,
pose acquisition, emitter distribution and attachment lifetime are not inferred.
Native CHitBox -> shape -> local bounds is qualified separately from generic
bone boxes in particle_bone_shapes.
"""
import math
import numbers
import numpy as np


def model_hitbox_shapes(inventory, pose, *, entity_id, tick, hitbox_set='', control_point=0,
                        volume_weighted=False):
    """Resolve one explicit model input into authored, posed hitbox records.

    Stateless: callers supply a complete identity-bound snapshot on every call.
    No entity search, CP alias, variant selection, missing-bone root fallback or
    previous-frame cache is invented. A missing named set uses authored default,
    as the qualified native set query does. Empty sets remain unsupported.
    """
    if (not isinstance(entity_id, str) or not entity_id
            or type(tick) is not int or tick < 0
            or type(control_point) is not int or not 0 <= control_point <= 63
            or not isinstance(hitbox_set, str) or len(hitbox_set) > 127
            or not isinstance(inventory, dict) or type(inventory.get('schema_version')) is not int or inventory['schema_version'] != 1
            or not isinstance(pose, dict) or pose.get('control_point') != control_point
            or type(pose.get('control_point')) is not int
            or pose.get('entity_id') != entity_id or pose.get('tick') != tick
            or type(pose.get('tick')) is not int):
        raise ValueError('Expected explicit model and control-point inputs')
    sources = inventory.get('sources')
    resource = inventory.get('model')
    if (not isinstance(resource, str) or not resource.endswith('.vmdl')
            or not isinstance(sources, list) or not sources
            or sources[0].get('route') != 'model'
            or sources[0].get('resource') != resource):
        raise ValueError('Missing authored hitbox model source')
    digest = sources[0].get('sha256')
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)
            or pose.get('resource') != resource or pose.get('resource_sha256') != digest):
        raise ValueError('Hitbox model/pose identity mismatch')
    names = inventory.get('model_bone_names')
    if (not isinstance(names, list) or not 0 < len(names) <= 32767
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names) or pose.get('names') != names):
        raise ValueError('Hitbox model bone order mismatch')
    transforms = pose.get('transforms')
    if not isinstance(transforms, (list, tuple, np.ndarray)) or len(transforms) != len(names):
        raise ValueError('Missing model-order pose rows')
    sets = inventory.get('hitbox_sets')
    if (not isinstance(sets, list) or not 0 < len(sets) <= 128
            or any(not isinstance(s, dict) or not isinstance(s.get('name'), str) for s in sets)
            or len({s['name'] for s in sets}) != len(sets)):
        raise ValueError('Ambiguous authored hitbox sets')
    by_name = {s['name']: s for s in sets}
    selected = by_name.get(hitbox_set, by_name.get('default'))
    if selected is None:
        raise ValueError('Requested and default hitbox sets are missing')
    boxes = selected.get('boxes')
    if not isinstance(boxes, list) or not 0 < len(boxes) <= 4096:
        raise ValueError('Selected authored hitbox set is empty or oversized')
    bone_ids = []
    lookup = {name: i for i, name in enumerate(names)}
    for i, box in enumerate(boxes):
        if (not isinstance(box, dict) or type(box.get('index')) is not int
                or box['index'] != i or box.get('bone') not in lookup
                or type(box.get('model_bone_index')) is not int
                or box['model_bone_index'] != lookup[box['bone']]):
            raise ValueError('Hitbox bone binding is missing or inconsistent')
        bone_ids.append(lookup[box['bone']])
    # An unselected cloth/sheared bone may be explicitly unavailable. Never
    # coerce it to a rigid pose, and never admit it when a hitbox selects it.
    paired = pose_rows([transforms[i] for i in bone_ids],len(boxes))
    if (paired.shape != (len(boxes), 8) or not np.isfinite(paired).all() or np.any(paired[:, 3] <= 0)
            or np.any(abs(np.sum(paired[:, 4:]**2, axis=1)-1) > 2e-4)):
        raise ValueError('Selected hitbox poses require finite uniform transforms')
    result = prepare_hitbox_shapes(boxes, paired, volume_weighted=volume_weighted)
    # The CP non-bone cache loop explicitly writes -1, selecting record index
    # routing. The separate non-CP loop does not establish this field.
    return dict(result, mappings=np.full(len(boxes), -1, dtype=np.int32),
                source_bone_indices=np.asarray(bone_ids, dtype=np.int32),
                hitbox_set=selected['name'], resource=resource,
                resource_sha256=digest, control_point=control_point,
                entity_id=entity_id, tick=tick)


def hitbox_bounds(hitbox):
    """Return Source-unit float32 bounds, including authored capsule radius.

    Capsule endpoints may arrive in either order. A sphere uses its first
    endpoint as centre, as the native conversion does. Boxes retain authored
    bounds. The result does not describe the native sampling distribution.
    """
    if not isinstance(hitbox, dict) or hitbox.get('shape') not in ('Box', 'Sphere', 'Capsule'):
        raise ValueError('Expected an authored box, sphere or capsule hitbox')
    vectors = [hitbox.get(key) for key in ('min', 'max')]
    if any(not isinstance(v, (list, tuple)) or len(v) != 3 or
           any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e6 for x in v)
           for v in vectors):
        raise ValueError('Hitbox endpoints must contain three finite Source-unit coordinates')
    radius = hitbox.get('radius')
    if type(radius) not in (int, float) or not math.isfinite(radius) or not 0 <= radius <= 1e6:
        raise ValueError('Hitbox radius must be finite and nonnegative')
    first, second = np.asarray(vectors, dtype=np.float32)
    radius = np.float32(radius)
    if hitbox['shape'] == 'Capsule':
        lower, upper = np.minimum(first, second) - radius, np.maximum(first, second) + radius
    elif hitbox['shape'] == 'Sphere':
        lower, upper = first - radius, first + radius
    else:
        lower, upper = first, second
        if np.any(lower > upper):
            raise ValueError('Authored box bounds are reversed')
    return {'min': lower, 'max': upper}


def pose_rows(values,count):
    if (not isinstance(values,(list,tuple,np.ndarray)) or len(values)!=count
            or any(not isinstance(row,(list,tuple,np.ndarray)) or len(row)!=8
                or any(isinstance(v,(bool,np.bool_)) or not isinstance(v,numbers.Real) for v in row)
                for row in values)):
        raise ValueError('Hitbox poses require explicit numeric eight-value rows')
    with np.errstate(over='ignore',invalid='ignore'):
        result=np.asarray(values,dtype=np.float32)
    if not np.isfinite(result).all():raise ValueError('Hitbox poses exceed finite float32 bounds')
    return result


def prepare_hitbox_shapes(hitboxes, transforms, *, volume_weighted=False):
    """Prepare already-paired authored hitboxes and Source-world pose rows.

    Copies pose rows verbatim and computes native float32 bounds/weights. This
    does not choose a set, resolve bone names or produce the native cache's
    mapping field. The caller must qualify those and the poses before emission.
    Degenerate volume weights remain zero; no uniform-weight fallback is made.
    """
    if not isinstance(hitboxes, (list, tuple)) or not 0 < len(hitboxes) <= 4096:
        raise ValueError('Expected 1..4096 already-selected authored hitboxes')
    if type(volume_weighted) is not bool:
        raise ValueError('Volume weighting must be explicit boolean')
    poses = pose_rows(transforms,len(hitboxes))
    if poses.shape != (len(hitboxes), 8) or not np.isfinite(poses).all():
        raise ValueError('Hitboxes require one finite paired pose row each')
    bounds = [hitbox_bounds(box) for box in hitboxes]
    lower = np.array([b['min'] for b in bounds], dtype=np.float32)
    upper = np.array([b['max'] for b in bounds], dtype=np.float32)
    extent = upper-lower
    weights = (extent[:, 1]*extent[:, 0])*extent[:, 2] if volume_weighted else np.ones(len(hitboxes), dtype=np.float32)
    total = np.float32(0)
    for weight in weights:
        total = np.float32(total+weight)
    if not np.isfinite(weights).all() or not np.isfinite(total):
        raise ValueError('Hitbox weight overflow')
    return dict(mins=lower, maxs=upper, transforms=poses.copy(),
                weights=weights, total_weight=total)
