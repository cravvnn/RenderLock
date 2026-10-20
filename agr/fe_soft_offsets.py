"""Ordered FE soft control offsets, independently qualified operation only.

The caller supplies control transforms at the native operation's input phase.
This does not establish where to run it in an offline cloth integration step.
"""
import numpy as np

from .fe_node_rotations import _rotate, _inverse


def apply_soft_offsets(current, constraints, *, offset_scale=1.0, reference=None):
    """Blend child positions toward scaled, rotated parent offsets.

    Source alpha retains the current child; one leaves it unchanged. Positions
    and offsets share Source units, with the separate uniform offset scale
    supplied explicitly. With a reference pose, the native general route derives
    offsets from that pose instead of the authored rest offset; its scale is
    already in the transforms. Rotation and scale of children remain unchanged.
    """
    current=np.asarray(current,dtype=float)
    if (current.ndim!=2 or current.shape[1:]!=(8,) or not 1<=len(current)<=4096
            or not isinstance(constraints,list) or len(constraints)>100000):
        raise ValueError('Bounded FE soft-offset transforms and declarations required')
    if (not np.isfinite(current).all() or np.any(current[:,3]<=0)
            or np.any(abs(np.linalg.norm(current[:,4:],axis=1)-1)>2e-5)):
        raise ValueError('Finite FE soft-offset transforms with unit rotations required')
    if type(offset_scale) not in (float,int) or not np.isfinite(offset_scale) or offset_scale<=0:
        raise ValueError('Positive finite FE soft-offset scale required')
    if reference is not None:
        reference=np.asarray(reference,dtype=float)
        if (reference.shape!=current.shape or not np.isfinite(reference).all()
                or np.any(reference[:,3]<=0)
                or np.any(abs(np.linalg.norm(reference[:,4:],axis=1)-1)>2e-5)):
            raise ValueError('Matching finite FE soft-offset reference transforms required')
        if offset_scale!=1:
            raise ValueError('Reference FE soft offsets already include transform scale')
    for row in constraints:
        for key in ('nCtrlParent','nCtrlChild'):
            if type(row.get(key)) is not int or not 0<=row[key]<len(current):
                raise ValueError('FE soft-offset control outside transform array')
        offset=np.asarray(row.get('vOffset'),dtype=float)
        if offset.shape!=(3,) or not np.isfinite(offset).all():
            raise ValueError('Finite authored FE soft offset required')
        alpha=row.get('flAlpha')
        if type(alpha) not in (float,int) or not np.isfinite(alpha) or not 0<=alpha<=1:
            raise ValueError('FE soft-offset alpha must be finite in0..1')
    result=current.copy()
    for row in constraints:
        parent,child=row['nCtrlParent'],row['nCtrlChild']
        frame=result[parent].copy()
        if reference is None:
            offset=np.asarray(row['vOffset'],dtype=float)*offset_scale
        else:
            original=reference[parent]
            offset=_rotate(_inverse(original[4:]),(reference[child,:3]-original[:3])/original[3])
        target=frame[:3]+frame[3]*_rotate(frame[4:],offset)
        alpha=row['flAlpha']
        result[child,:3]=alpha*result[child,:3]+(1-alpha)*target
    if not np.isfinite(result).all():raise ValueError('FE soft-offset output became nonfinite')
    return result
