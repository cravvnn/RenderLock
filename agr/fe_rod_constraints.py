"""Ordered compiled four-lane FE rod projection, in Source distance units."""
from __future__ import annotations

import numpy as np


def project_compiled_rods(points,blocks,*,length_scales=None):
    """Return one native-order projection pass, including authored soft rods.

    All four lanes read a common pre-block state before their endpoint writes.
    Raw debug/declaration order is not the compiled simulation block order.
    This operation does not promise zero residual after a finite solver pass.
    """
    points=np.asarray(points,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or not np.isfinite(points).all() or not isinstance(blocks,list)
            or len(blocks)>25000):
        raise ValueError('Finite bounded FE compiled rod positions/blocks required')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    if scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Positive authored FE rod length scales required')
    prepared=[]
    for row in blocks:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=2
                or any(not isinstance(side,list) or len(side)!=4 for side in nodes)
                or any(type(i) is not int or not 0<=i<len(points) for side in nodes for i in side)):
            raise ValueError('Compiled FE rod lane indices differ from control array')
        values=np.asarray([row[k] for k in ('f4MaxDist','f4MinDist','f4Weight0','f4RelaxationFactor')],dtype=float)
        if (values.shape!=(4,4) or not np.isfinite(values).all() or np.any(values[:2]<0)
                or np.any(values[1]>values[0]) or np.any(values[2:]<0) or np.any(values[2:]>1)):
            raise ValueError('Invalid compiled FE rod interval/weight/relaxation')
        prepared.append((np.asarray(nodes,dtype=int),values))
    result=points.copy()
    for (nodes,values),scale in zip(prepared,scales):
        maximum,minimum,weight,relax=values
        start=result[nodes[0]].copy();end=result[nodes[1]].copy();delta=end-start
        length=np.sqrt(np.maximum(np.sum(delta*delta,axis=1),2**-30))
        target=np.clip(length,minimum*scale,maximum*scale)
        correction=delta*((target/length-1)*relax)[:,None]
        first=start-correction*weight[:,None]
        second=end+correction*(1-weight)[:,None]
        # Preserve the native write order even for explicit repeated/pad lanes.
        for lane,node in enumerate(nodes[0]):result[node]=first[lane]
        for lane,node in enumerate(nodes[1]):result[node]=second[lane]
    if not np.isfinite(result).all():raise ValueError('Compiled FE rod projection became nonfinite')
    return result


def project_animated_rods(points,blocks,goal_positions):
    """Project authored animated-length rods against the current goal sample.

    The target lengths come from the recorded animated control transforms,
    rather than from rest lengths or from the simulated particle positions.
    """
    points=np.asarray(points,dtype=float);goals=np.asarray(goal_positions,dtype=float)
    if (goals.shape!=points.shape or goals.ndim!=2 or goals.shape[1:]!=(3,)
            or not 1<=len(goals)<=4096 or not np.isfinite(goals).all()
            or not isinstance(blocks,list) or len(blocks)>25000):
        raise ValueError('Finite bounded FE animated rod goal positions required')
    converted=[]
    for row in blocks:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=2
                or any(not isinstance(side,list) or len(side)!=4 for side in nodes)
                or any(type(i) is not int or not 0<=i<len(points) for side in nodes for i in side)):
            raise ValueError('Animated FE rod lane indices differ from control array')
        delta=goals[nodes[1]]-goals[nodes[0]]
        lengths=np.sqrt(np.maximum(np.sum(delta*delta,axis=1),2**-30)).tolist()
        converted.append(dict(nNode=nodes,f4MaxDist=lengths,f4MinDist=lengths,
            f4Weight0=row['f4Weight0'],f4RelaxationFactor=row['f4RelaxationFactor']))
    return project_compiled_rods(points,converted)
