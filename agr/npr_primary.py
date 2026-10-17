"""Pinned client primary-light arithmetic; camera field semantics remain explicit."""
import numpy as np


def primary_direction(camera_xy,weight):
    """Use values loaded from client view fields +0x184/+0x188, not guessed angles.

    Normal finite domain only. Does not emulate the client's exceptional-length
    fallback helper or establish how a replay camera maps to these fields.
    """
    xy=np.asarray(camera_xy,dtype=np.float64)
    if xy.ndim!=2 or xy.shape[1]!=2 or not np.isfinite(xy).all():
        raise ValueError('Finite Nx2 explicit camera components required')
    if not np.isfinite(weight) or not 0<=weight<=1:
        raise ValueError('Bounded camera weight required')
    def normalize(v):
        lengths=np.linalg.norm(v,axis=1)
        if np.any((lengths<1e-17)|(lengths>1e17)):
            raise ValueError('Unqualified normalization fallback domain')
        return v/lengths[:,None]
    projected=normalize(np.column_stack((-xy,np.full(len(xy),float(np.float32(1e-8))))))
    return normalize(projected*weight+np.array([0.,0.,1.])*(1-weight))
