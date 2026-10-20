"""Directed FE twist projection qualified against the installed offline solver.

Transforms use Source's [position XYZ, scale, quaternion XYZW] layout. This
operation alone does not implement an FE integrator, effect selection or hinge
limits. Callers must preserve the authored operation order and coordinate frame.
"""
from __future__ import annotations

import numpy as np


def _multiply(a, b):
    return np.r_[a[3]*b[:3]+b[3]*a[:3]+np.cross(a[:3],b[:3]),
                 a[3]*b[3]-a[:3]@b[:3]]


def _inverse(q):
    return q*np.array([-1.,-1.,-1.,1.])


def _rotate(q, v):
    cross=2*np.cross(q[:3],v)
    return v+q[3]*cross+np.cross(q[:3],cross)


def _normal(q, fallback):
    length=float(np.linalg.norm(q))
    return q/length if length>0 else fallback.copy()


def _direction(vector):
    length=float(np.linalg.norm(vector))
    return vector/length if length>0 else np.zeros(3)


def _between(a, b):
    half=(a+b)*.5
    if half@half>2**-23:
        q=np.r_[np.cross(a,half),a@half]
    elif abs(a[0])>.5:
        q=np.array([a[1],-a[0],0.,0.])
    else:
        q=np.array([0.,a[2],-a[1],0.])
    return _normal(q,np.array([0.,0.,0.,1.]))


def apply_twist_constraints(rest, previous, current, positions, constraints):
    """Return projected transforms, retaining duplicate directed declarations.

    ``previous`` supplies the recorded input transform/scale. ``current`` is
    the evolving output of the earlier authored node operations; ``positions``
    contains their simulated particle positions, in the same coordinate frame.
    Input arrays and declarations are never modified.
    """
    rest=np.asarray(rest,dtype=np.float64)
    previous=np.asarray(previous,dtype=np.float64)
    current=np.asarray(current,dtype=np.float64)
    positions=np.asarray(positions,dtype=np.float64)
    if (rest.ndim!=2 or rest.shape[1:]!=(8,) or not 1<=len(rest)<=4096
            or previous.shape!=rest.shape or current.shape!=rest.shape
            or positions.shape!=(len(rest),3)):
        raise ValueError('FE twist transform dimensions differ')
    for values in (rest,previous,current):
        if (not np.isfinite(values).all() or np.any(values[:,3]<=0)
                or np.any(abs(np.linalg.norm(values[:,4:],axis=1)-1)>2e-5)):
            raise ValueError('Finite FE twist transforms with unit rotations required')
    if not np.isfinite(positions).all():
        raise ValueError('Finite FE twist positions required')
    if not isinstance(constraints,list) or len(constraints)>100000:
        raise ValueError('Bounded ordered FE twist declarations required')
    for row in constraints:
        for key in ('nNodeOrient','nNodeEnd'):
            if type(row.get(key)) is not int or not 0<=row[key]<len(rest):
                raise ValueError('FE twist endpoint outside control nodes')
        for key in ('flTwistRelax','flSwingRelax'):
            value=row.get(key)
            if type(value) not in (int,float) or not np.isfinite(value) or not 0<=value<=1:
                raise ValueError('FE twist relaxation must be finite in0..1')
    result=current.copy();identity=np.array([0.,0.,0.,1.])
    for row in constraints:
        a,b=row['nNodeOrient'],row['nNodeEnd']
        local_axis=_rotate(_inverse(rest[a,4:]),_direction(rest[b,:3]-rest[a,:3]))
        original=previous[a,4:].copy()
        result[a,:3]=positions[a];result[a,3]=previous[a,3]
        if a==b:
            target=original
        else:
            target=_multiply(result[b,4:],_multiply(_inverse(rest[b,4:]),rest[a,4:]))
            axis=_rotate(_inverse(target),_direction(positions[b]-positions[a]))
            target=_multiply(target,_between(local_axis,axis))
        twist,swing=row['flTwistRelax'],row['flSwingRelax']
        before=result[a,4:].copy()
        if twist+swing>=2:
            rotated=target
        else:
            if target@before<0:target=-target
            if twist==swing:
                rotated=(1-twist)*before+twist*target
            else:
                delta=_multiply(_inverse(before),target)
                axial=np.r_[local_axis*(local_axis@delta[:3]),delta[3]]
                applied=_normal((1-twist)*identity+twist*axial,identity)
                if swing:
                    transverse=_multiply(delta,_inverse(axial))
                    applied=_multiply(_normal((1-swing)*identity+swing*transverse,identity),applied)
                rotated=_multiply(before,applied)
        result[a,4:]=_normal(rotated,original)
    return result


def apply_parent_locks(current,previous,constraints):
    """Apply ordered parent-offset position locks after rotation operations.

    Positions/offsets share Source units. Child orientation is retained and its
    scale comes from the recorded parent, while its position follows the evolving
    parent's scaled/rotated authored offset.
    """
    current=np.asarray(current,dtype=float);previous=np.asarray(previous,dtype=float)
    if (current.ndim!=2 or current.shape[1:]!=(8,) or not 1<=len(current)<=4096
            or previous.shape!=current.shape or not isinstance(constraints,list)
            or len(constraints)>100000):
        raise ValueError('Bounded FE parent-lock transform/declaration arrays required')
    for values in (current,previous):
        if (not np.isfinite(values).all() or np.any(values[:,3]<=0)
                or np.any(abs(np.linalg.norm(values[:,4:],axis=1)-1)>2e-5)):
            raise ValueError('Finite FE parent-lock transforms with unit rotations required')
    for row in constraints:
        for key in ('nCtrlParent','nCtrlChild'):
            if type(row.get(key)) is not int or not 0<=row[key]<len(current):
                raise ValueError('FE parent-lock control outside transform array')
        offset=np.asarray(row.get('vOffset'),dtype=float)
        if offset.shape!=(3,) or not np.isfinite(offset).all():
            raise ValueError('Finite authored FE parent offset required')
    result=current.copy()
    for row in constraints:
        parent,child=row['nCtrlParent'],row['nCtrlChild']
        frame=result[parent].copy()
        result[child,:3]=frame[:3]+frame[3]*_rotate(frame[4:],np.asarray(row['vOffset'],dtype=float))
        result[child,3]=previous[parent,3]
    if not np.isfinite(result).all():raise ValueError('FE parent-lock output became nonfinite')
    return result


def apply_goal_locks(current,previous,positions,indices):
    """Copy final particle positions for the native post-rotation goal locks.

    This tail operation retains the current orientation and recorded scale. Its
    input is the final simulated position array, not an invented animation goal.
    Source executes it immediately before parent-offset locks.
    """
    current=np.asarray(current,dtype=float);previous=np.asarray(previous,dtype=float)
    positions=np.asarray(positions,dtype=float)
    if (current.ndim!=2 or current.shape[1:]!=(8,) or not 1<=len(current)<=4096
            or previous.shape!=current.shape or positions.shape!=(len(current),3)
            or not isinstance(indices,list) or len(indices)>100000):
        raise ValueError('Bounded FE goal-lock transforms, positions and indices required')
    for values in (current,previous):
        if (not np.isfinite(values).all() or np.any(values[:,3]<=0)
                or np.any(abs(np.linalg.norm(values[:,4:],axis=1)-1)>2e-5)):
            raise ValueError('Finite FE goal-lock transforms with unit rotations required')
    if not np.isfinite(positions).all():raise ValueError('Finite FE goal-lock positions required')
    if any(type(index) is not int or not 0<=index<len(current) for index in indices):
        raise ValueError('FE goal-lock control outside transform array')
    result=current.copy()
    for index in indices:
        result[index,:3]=positions[index]
        result[index,3]=previous[index,3]
    return result
