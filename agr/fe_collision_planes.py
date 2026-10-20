"""FE parent-local plane projection, without world-collision inference."""
import numpy as np

from .fe_node_rotations import _rotate


def project_collision_planes(points,controls,planes):
    """Apply ordered authored one-sided planes in Source units.

    Controls must be those available at the native collision phase. Native uses
    parent rotation and position; it does not multiply plane offset by parent
    transform scale. This helper does not choose that phase or model-scale policy.
    """
    points=np.asarray(points,dtype=float);controls=np.asarray(controls,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or controls.ndim!=2 or controls.shape[1:]!=(8,) or not 1<=len(controls)<=4096
            or not isinstance(planes,list) or len(planes)>100000):
        raise ValueError('Bounded FE plane positions, control frames and declarations required')
    if (not np.isfinite(points).all() or not np.isfinite(controls).all()
            or np.any(controls[:,3]<=0) or np.any(abs(np.linalg.norm(controls[:,4:],axis=1)-1)>2e-5)):
        raise ValueError('Finite FE plane data with unit control rotations required')
    for row in planes:
        for key,count in [('nCtrlParent',len(controls)),('nChildNode',len(points))]:
            if type(row.get(key)) is not int or not 0<=row[key]<count:
                raise ValueError('FE plane control or particle outside input arrays')
        normal=np.asarray(row['m_Plane']['m_vNormal'],dtype=float)
        offset=row['m_Plane']['m_flOffset'];strength=row['flStrength']
        if (normal.shape!=(3,) or not np.isfinite(normal).all() or abs(np.linalg.norm(normal)-1)>2e-5
                or type(offset) not in (float,int) or not np.isfinite(offset)
                or type(strength) not in (float,int) or not np.isfinite(strength) or not 0<=strength<=1):
            raise ValueError('Finite unit FE collision plane and strength in0..1 required')
    result=points.copy()
    for row in planes:
        parent=controls[row['nCtrlParent']];child=row['nChildNode']
        normal=_rotate(parent[4:],np.asarray(row['m_Plane']['m_vNormal'],dtype=float))
        signed=float((result[child]-parent[:3])@normal-row['m_Plane']['m_flOffset'])
        if signed<0:result[child]-=normal*(signed*row['flStrength'])
    if not np.isfinite(result).all():raise ValueError('FE collision-plane output became nonfinite')
    return result
