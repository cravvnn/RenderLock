"""Ordered authored three-node FE Kelager bend projection in Source units."""
import numpy as np


def project_kelager_bends(points,constraints):
    """Apply one native bend pass without selecting a simulation schedule.

    The first node's distance from the arithmetic centroid is limited by the
    authored height. Compiled signed weights distribute its correction; they
    are not probabilities and must not be normalized.
    """
    points=np.asarray(points,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or not np.isfinite(points).all() or not isinstance(constraints,list) or len(constraints)>100000):
        raise ValueError('Bounded finite FE bend positions and declarations required')
    plans=[]
    for row in constraints:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=3
                or any(type(i) is not int or not 0<=i<len(points) for i in nodes) or len(set(nodes))!=3):
            raise ValueError('FE bend requires three distinct valid control nodes')
        weights=np.asarray(row.get('flWeight'),dtype=float);height=row.get('flHeight0')
        if (weights.shape!=(3,) or not np.isfinite(weights).all()
                or type(height) not in (float,int) or not np.isfinite(height) or height<0
                or type(row.get('nReserved')) is not int or row['nReserved']!=0):
            raise ValueError('Finite FE bend weights, nonnegative height and reserved zero required')
        plans.append((nodes,weights,height))
    result=points.copy()
    for nodes,weights,height in plans:
        local=result[nodes].copy()
        offset=local[0]-local.sum(axis=0)*float(np.float32(1/3))
        length=float(np.linalg.norm(offset))
        if length>height:
            correction=offset*(1-height/length)
            for node,weight in zip(nodes,weights):result[node]+=weight*correction
    if not np.isfinite(result).all():raise ValueError('FE bend projection became nonfinite')
    return result
