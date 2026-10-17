"""Prepared bone-emission boxes, checked against the installed client query.

This is not yet a runtime model provider. Callers must supply exact model-order
world transforms and the applicable control-point translation offset. Hitbox
mode, skeleton remapping and engine entity/provider acquisition are separate.
"""
import numpy as np


def bone_boxes(transforms, *, offset=(0, 0, 0), capacity=None):
    """Return bounds and position/scale/quaternion transforms in Source units.

    Bounds are local [-8, 8]. Preserve transform scale and rotation verbatim;
    the client's copy path does not normalize them or take absolute scale.
    No engine hitbox mapping/weight fields are inferred here.
    """
    values = np.asarray(transforms, dtype=np.float32)
    displacement = np.asarray(offset, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != 8 or not np.isfinite(values).all():
        raise ValueError('Expected finite bone transforms with shape (bones, 8)')
    if displacement.shape != (3,) or not np.isfinite(displacement).all():
        raise ValueError('Expected finite Source-unit translation offset')
    if capacity is not None and (type(capacity) is not int or capacity <= 0):
        raise ValueError('Capacity must be a positive integer')
    count = len(values) if capacity is None else min(len(values), capacity)
    result = values[:count].copy()
    result[:, :3] += displacement
    if not np.isfinite(result).all():
        raise ValueError('Bone translation overflow')
    return {'source_bone_indices': np.arange(count, dtype=np.int32),
            'mins': np.full((count, 3), -8, dtype=np.float32),
            'maxs': np.full((count, 3), 8, dtype=np.float32),
            'transforms': result}
