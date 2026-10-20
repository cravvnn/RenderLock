"""Bounded four-point FE hinge projection in Source distance units.

Flag zero and authored reference/arm blends are qualified. This isolated
operation does not establish an FE integration clock or constraint schedule.
"""
from __future__ import annotations

import math
import numpy as np


def _geometry(points,center):
    axis=points[1]-points[0];length=float(np.linalg.norm(axis))
    if length<=1e-3:return None
    axis/=length
    reference=points[2]-points[0];arm=points[3]-points[0]
    along_reference=float(reference@axis);along_arm=float(arm@axis)
    reference-=along_reference*axis;arm-=along_arm*axis
    ref2=float(reference@reference);arm2=float(arm@arm)
    if ref2<1e-4 or arm2<1e-4 or length<.01:return None
    inv_reference=1/math.sqrt(ref2);inv_arm=1/math.sqrt(arm2)
    reference*=inv_reference;arm*=inv_arm
    normal=np.cross(axis,reference)
    cosine=float(reference@arm);sine=float(normal@arm)
    # Source's angle built-in and wrap comparisons use float32. Keep the
    # half-turn branch consistent with that storage before iterative updates.
    angle=float(np.float32(np.float32(math.atan2(sine,cosine))-np.float32(center)))
    pi=float(np.float32(math.pi));tau=np.float32(2*math.pi)
    if angle>pi:angle=float(np.float32(np.float32(angle)-tau))
    elif angle<-pi:angle=float(np.float32(np.float32(angle)+tau))
    return dict(length=length,inv_length=1/length,inv_reference=inv_reference,
                inv_arm=inv_arm,along_reference=along_reference,along_arm=along_arm,
                cosine=cosine,sine=sine,reference=reference,normal=normal,angle=angle)


def _project(points,masses,geo,angle_step):
    length=geo['length'];inverse=geo['inv_length']
    r,v=geo['inv_reference'],geo['inv_arm']
    c,d=geo['along_reference'],geo['along_arm']
    cosine,sine=geo['cosine'],geo['sine']
    a=(r-cosine*v-(r*c-cosine*v*d)*inverse)*masses[0]
    b=sine*v*(1-d*inverse)*masses[0]
    e=(r*c-cosine*v*d)*inverse*masses[1]
    f=sine*v*d*inverse*masses[1]
    ref_mass=r*masses[2];arm_mass=v*masses[3]
    denominator=(r*ref_mass+v*arm_mass+c*e*r*inverse
                 +(length-c)*a*r*inverse+(d-length)*(cosine*a-sine*b)*inverse*v
                 +d*(sine*f-cosine*e)*inverse*v)
    if abs(denominator)<2**-23:return False
    scale=angle_step/denominator
    normal,reference=geo['normal'],geo['reference']
    points[0]+=scale*(a*normal+b*reference)
    points[1]+=scale*(e*normal+f*reference)
    points[2]-=scale*ref_mass*normal
    points[3]+=scale*arm_mass*(cosine*normal-sine*reference)
    return True


def project_hinge_limits(points,inverse_masses,constraints):
    """Project ordered authored hinges and return a new point array/diagnostics.

    Positions and geometry thresholds use Source units. Inverse masses retain
    their relative authored values; zero-mass points remain pinned exactly.
    """
    points=np.asarray(points,dtype=float);masses=np.asarray(inverse_masses,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or masses.shape!=(len(points),) or not np.isfinite(points).all()
            or not np.isfinite(masses).all() or np.any(masses<0)):
        raise ValueError('Finite bounded FE hinge positions/inverse masses required')
    if not isinstance(constraints,list) or len(constraints)>100000:
        raise ValueError('Bounded ordered FE hinge declarations required')
    for row in constraints:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=6
                or any(type(i) is not int or not 0<=i<len(points) for i in nodes)
                or nodes[0]==nodes[1]):
            raise ValueError('FE hinge nodes outside control array or axis degenerate')
        if (type(row.get('nFlags')) is not int or row['nFlags']!=0
                or any(type(row.get(k)) not in (float,int) or not math.isfinite(row[k])
                       or not 0<=row[k]<1 for k in ('flWeight4','flWeight5'))):
            raise ValueError('FE hinge flag zero and finite endpoint blend weights below one required')
        center,extent=row.get('flAngleCenter'),row.get('flAngleExtents')
        if (type(center) not in (int,float) or not math.isfinite(center) or abs(center)>math.pi
                or type(extent) not in (int,float) or not math.isfinite(extent) or not 0<=extent<=math.pi):
            raise ValueError('Finite FE hinge angle interval in radians required')
    result=points.copy();iterations=0;degenerate=0;residual=0.
    tolerance=math.radians(1)
    for row in constraints:
        nodes=row['nNode'];indices=nodes[:4];local=result[indices].copy();weights=masses[indices].copy()
        blends=(row['flWeight4'],row['flWeight5'])
        for side,blend in enumerate(blends,2):
            if blend>0:local[side]=local[side]*(1-blend)+result[nodes[side+2]]*blend
            weights[side]*=1-blend
        geo=_geometry(local,row['flAngleCenter'])
        if geo is None:
            degenerate+=1;continue
        if abs(geo['angle'])>row['flAngleExtents']+tolerance:
            target=math.copysign(row['flAngleExtents'],geo['angle'])
            last_delta=None
            for _ in range(5):
                change=target-geo['angle']
                before=local.copy()
                if not _project(local,weights,geo,float(np.clip(change,-math.pi/4,math.pi/4))):break
                last_delta=local-before
                iterations+=1
                if abs(change)<math.radians(4):break
                geo=_geometry(local,row['flAngleCenter'])
                if geo is None or abs(geo['angle']-target)<=tolerance:break
            if last_delta is not None:
                for side,(index,point,weight) in enumerate(zip(indices,local,weights)):
                    if weight<=0:continue
                    blend=blends[side-2] if side>=2 else 0
                    if blend>0:
                        # Source stores the last iterative endpoint correction,
                        # then applies these ratios to the two original nodes.
                        result[index]+=last_delta[side]*(blend/(1-blend))
                        result[nodes[side+2]]+=last_delta[side]*((1-blend)/blend)
                    else:result[index]=point
        final=result[indices].copy()
        for side,blend in enumerate(blends,2):
            if blend>0:final[side]=final[side]*(1-blend)+result[nodes[side+2]]*blend
        after=_geometry(final,row['flAngleCenter'])
        if after is not None:residual=max(residual,max(0.,abs(after['angle'])-row['flAngleExtents']))
    if not np.isfinite(result).all():raise ValueError('FE hinge projection produced nonfinite positions')
    return result,dict(iterations=iterations,degenerate_hinges=degenerate,
                       maximum_angle_residual_radians=residual)
