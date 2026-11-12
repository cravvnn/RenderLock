"""Prepared Source custom-origin-follow transform, without entity acquisition.

The caller must independently bind the exact owner, acquire a coherent Source
world SRT and establish the authored CP matrix from creation/history. This math
does not admit an attachment, synthesize missing inputs or register an effect.
"""
import numpy as np
from .particle_snapshot_matrices import _matrices


def custom_origin_transform(owner_srt, authored_matrix):
    """Return Source column-vector 3x4: owner world matrix @ authored CP matrix.

    owner_srt is xyz, uniform positive scale, quaternion xyzw. authored_matrix
    is an explicit Source 3x4, including its local offset. All values are Source
    units; scale and the full basis are retained. No quaternion normalization,
    default identity, unit conversion, entity-root alias or pose cache is used.
    """
    owner = np.asarray(owner_srt)
    authored = np.asarray(authored_matrix)
    if (owner.shape != (8,) or authored.shape != (3, 4)
            or owner.dtype.kind not in 'iuf' or authored.dtype.kind not in 'iuf'):
        raise ValueError('Custom-origin follow requires numeric owner SRT and explicit 3x4 CP matrix')
    if not np.isfinite(owner).all() or not np.isfinite(authored).all():
        raise ValueError('Custom-origin transform inputs must be finite')
    with np.errstate(over='ignore', invalid='ignore'):
        owner32 = owner.astype(np.float32)
        authored32 = authored.astype(np.float32)
        if not np.isfinite(owner32).all() or not np.isfinite(authored32).all():
            raise ValueError('Custom-origin inputs exceed finite Source float32')
        world = _matrices(owner32[None])[0]
        local = np.eye(4)
        local[:3] = authored32
        result = np.asarray((world @ local)[:3], dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError('Custom-origin transform overflow')
    return result
