"""Ordered compiled four-lane FE rod projection, in Source distance units."""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass


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
    return prepare_compiled_rods(len(points),blocks).project(points,length_scales=length_scales)


def prepare_compiled_rods(node_count,blocks):
    """Snapshot and validate authored blocks once for a bounded solver call.

    No global cache: the next hero/export builds its own plan. Copies prevent
    later edits to caller-owned metadata from changing an already prepared pass.
    """
    if (type(node_count) is not int or not 1<=node_count<=4096
            or not isinstance(blocks,list) or len(blocks)>25000):
        raise ValueError('Finite bounded FE compiled rod positions/blocks required')
    prepared=[]
    for row in blocks:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=2
                or any(not isinstance(side,list) or len(side)!=4 for side in nodes)
                or any(type(i) is not int or not 0<=i<node_count for side in nodes for i in side)):
            raise ValueError('Compiled FE rod lane indices differ from control array')
        values=np.asarray([row[k] for k in ('f4MaxDist','f4MinDist','f4Weight0','f4RelaxationFactor')],dtype=float)
        if (values.shape!=(4,4) or not np.isfinite(values).all() or np.any(values[:2]<0)
                or np.any(values[1]>values[0]) or np.any(values[2:]<0) or np.any(values[2:]>1)):
            raise ValueError('Invalid compiled FE rod interval/weight/relaxation')
        # Immutable byte-backed arrays cannot be made writable by a consumer.
        prepared.append((np.frombuffer(np.asarray(nodes,dtype=np.int64).tobytes(),dtype=np.int64).reshape(2,4),
                         np.frombuffer(values.tobytes(),dtype=float).reshape(4,4)))
    groups=[];batch=[];occupied=set();offset=0
    def append_group():
        nodes=np.asarray([row[0] for row in batch],dtype=np.int64)
        values=np.asarray([row[1] for row in batch],dtype=float)
        # Repeated compiler pad lanes must keep their last native endpoint write.
        last={int(node):index for index,node in enumerate(nodes.ravel())}
        destinations=np.asarray(list(last),dtype=np.int64)
        sources=np.asarray(list(last.values()),dtype=np.int64)
        def freeze(array):
            return np.frombuffer(array.tobytes(),dtype=array.dtype).reshape(array.shape)
        groups.append((offset,freeze(nodes),freeze(values),freeze(destinations),freeze(sources)))
    for row in prepared:
        members=set(row[0].ravel())
        if batch and occupied & members:
            append_group();offset+=len(batch);batch=[];occupied=set()
        batch.append(row);occupied.update(members)
    if batch:append_group()
    return _CompiledRodPlan(node_count,tuple(prepared),tuple(groups))


@dataclass(frozen=True)
class _CompiledRodPlan:
    node_count: int
    blocks: tuple
    groups: tuple

    def project(self,points,*,length_scales=None):
        points=np.asarray(points,dtype=float)
        if points.shape!=(self.node_count,3) or not np.isfinite(points).all():
            raise ValueError('Finite bounded FE compiled rod positions/blocks required')
        scales=np.ones((len(self.blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
        if scales.shape!=(len(self.blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
            raise ValueError('Positive authored FE rod length scales required')
        result=points.copy()
        for offset,nodes,values,destinations,sources in self.groups:
            scale=scales[offset:offset+len(nodes)]
            maximum,minimum,weight,relax=values.transpose(1,0,2)
            start=result[nodes[:,0]].copy();end=result[nodes[:,1]].copy();delta=end-start
            length=np.sqrt(np.maximum(np.sum(delta*delta,axis=2),2**-30))
            target=np.clip(length,minimum*scale,maximum*scale)
            correction=delta*((target/length-1)*relax)[:,:,None]
            first=start-correction*weight[:,:,None]
            second=end+correction*(1-weight)[:,:,None]
            # Blocks in a group share no nodes. Group boundaries preserve every
            # dependency; unique scatter destinations preserve last pad writes.
            writes=np.stack((first,second),axis=1).reshape(-1,3)
            result[destinations]=writes[sources]
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
