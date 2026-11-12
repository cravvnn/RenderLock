"""Prepared snapshot skinning math, independent of runtime provider selection.

Inputs use exact model bone order, Source units, and xyz/scale/quaternion-xyzw
rows. The caller owns model/variant identity, pose acquisition, CP translation
and current/previous scheduling. This module does not enable an effect.
"""
import numpy as np


def _matrices(transforms):
    rows = np.asarray(transforms, dtype=np.float32)
    if (rows.ndim != 2 or rows.shape[1] != 8 or len(rows) > 4096
            or not np.isfinite(rows).all()):
        raise ValueError('Expected at most 4096 finite snapshot transforms (bones, 8)')
    # Preserve the supplied quaternion and scale; do not silently normalize.
    if (np.any(rows[:, 3] <= 0)
            or np.any(abs(np.sum(rows[:, 4:].astype(float)**2, axis=1)-1) > 1e-3)):
        raise ValueError('Snapshot transforms require positive scale and unit quaternions')
    result = np.repeat(np.eye(4)[None], len(rows), axis=0)
    x, y, z, w = rows[:, 4:].astype(float).T
    result[:, :3, :3] = np.stack([
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y),
    ], axis=1).reshape(-1, 3, 3) * rows[:, 3, None, None]
    result[:, :3, 3] = rows[:, :3]
    return result


def snapshot_skinning_matrices(inverse_rest, current, indices, weights, *, previous=None):
    """Return current/optional previous 3x4 matrices, or (None, None).

    Four uint16 bone indices use 65535 as invalid. Three supplied weights leave
    a clamped float32 remainder for the fourth slot. A single valid influence
    ignores its weight; multiple valid influences retain their weights without
    renormalizing. With previous requested, both outputs use the intersection
    of all three array bounds. This is the native matrix helper's behavior.
    """
    if (not isinstance(indices, (list, tuple)) or len(indices) != 4
            or any(type(i) is not int or not 0 <= i <= 65535 for i in indices)):
        raise ValueError('Snapshot indices must be four uint16 integers')
    supplied = np.asarray(weights, dtype=np.float32)
    if supplied.shape != (3,) or not np.isfinite(supplied).all():
        raise ValueError('Snapshot requires three finite weights')
    a, b = _matrices(inverse_rest), _matrices(current)
    old = None if previous is None else _matrices(previous)
    valid = [(slot, i) for slot, i in enumerate(indices)
             if i != 65535 and i < len(a) and i < len(b)
             and (old is None or i < len(old))]
    if not valid:
        return None, None
    remainder = np.float32(1)
    for weight in supplied:
        remainder = np.float32(remainder-weight)
    all_weights = [*supplied, np.clip(remainder, 0, 1)]

    def blend(poses):
        matrix = sum((1 if len(valid) == 1 else all_weights[slot])
                     * (poses[i] @ a[i])[:3] for slot, i in valid)
        result = np.asarray(matrix, dtype=np.float32)
        if not np.isfinite(result).all():
            raise ValueError('Snapshot matrix overflow')
        return result

    return blend(b), None if old is None else blend(old)
