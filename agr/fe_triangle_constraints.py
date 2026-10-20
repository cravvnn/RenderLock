"""Compiled FE triangle projections, qualified separately by pin count."""
from __future__ import annotations

import numpy as np


def project_two_pin_triangles(points,blocks,*,length_scales=None):
    """Return one ordered four-lane pass without altering either pinned node.

    Inputs use Source distance units. The compiler's third node is free, w1=0
    and w2=1. Other triangle mass layouts require their own native projection.
    Length-scale binding belongs to the caller; default one is model space.
    """
    points=np.asarray(points,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or not np.isfinite(points).all() or not isinstance(blocks,list) or len(blocks)>25000):
        raise ValueError('Finite bounded FE triangle positions/blocks required')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    if scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Positive FE triangle length scales required')
    prepared=[]
    for row in blocks:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=3
                or any(not isinstance(side,list) or len(side)!=4 for side in nodes)
                or any(type(i) is not int or not 0<=i<len(points) for side in nodes for i in side)
                or any(len({nodes[k][lane] for k in range(3)})!=3 for lane in range(4))):
            raise ValueError('Compiled FE triangle node indices differ')
        values=np.asarray([row['w1'],row['w2'],row['v1x'],row['v2']['x'],row['v2']['y']],dtype=float)
        if (values.shape!=(5,4) or not np.isfinite(values).all()
                or np.any(values[0]!=0) or np.any(values[1]!=1)
                or np.any(values[2]<=0) or np.any(values[4]<0)):
            raise ValueError('Qualified two-pin FE triangle parameters required')
        prepared.append((np.asarray(nodes,dtype=int),values))
    result=points.copy()
    for (nodes,values),scale in zip(prepared,scales):
        a=result[nodes[0]].copy();b=result[nodes[1]].copy();c=result[nodes[2]].copy()
        axis=b-a;length2=np.sum(axis*axis,axis=1);valid=length2>2**-23
        length=np.ones(4);length[valid]=np.sqrt(length2[valid])
        axis[valid]/=length[valid,None];axis[~valid]=[1,0,0]
        offset=c-a;normal=offset-axis*np.sum(offset*axis,axis=1)[:,None]
        normal2=np.sum(normal*normal,axis=1);valid=normal2>2**-23
        normal[valid]/=np.sqrt(normal2[valid,None]);normal[~valid]=[0,1,0]
        axial=((length-values[2])*.5+values[3])*scale
        target=a+axis*axial[:,None]+normal*(values[4]*scale)[:,None]
        for lane,node in enumerate(nodes[2]):result[node]=target[lane]
    if not np.isfinite(result).all():raise ValueError('Compiled FE triangle projection became nonfinite')
    return result


def project_mass_weighted_triangles(points,blocks,*,pinned_nodes,length_scales=None):
    """Project the free or one-pinned four-lane Source triangle operation.

    The compiled weights locate the triangle's center of mass. A planar rigid
    fit retains that center for free triangles, or the first point for one-pin
    triangles. Four lanes gather before any writes, including padded lanes.
    This operation does not establish the simulation clock or solver order.
    """
    if type(pinned_nodes) is not int or pinned_nodes not in (0,1):
        raise ValueError('Free or one-pinned FE triangle operation required')
    points=np.asarray(points,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or not np.isfinite(points).all() or not isinstance(blocks,list) or len(blocks)>25000):
        raise ValueError('Finite bounded FE triangle positions/blocks required')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    if scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Positive FE triangle length scales required')
    prepared=[]
    for row in blocks:
        nodes=row.get('nNode')
        if (not isinstance(nodes,list) or len(nodes)!=3
                or any(not isinstance(side,list) or len(side)!=4 for side in nodes)
                or any(type(i) is not int or not 0<=i<len(points) for side in nodes for i in side)
                or any(len({nodes[k][lane] for k in range(3)})!=3 for lane in range(4))):
            raise ValueError('Compiled FE triangle node indices differ')
        values=np.asarray([row['w1'],row['w2'],row['v1x'],row['v2']['x'],row['v2']['y']],dtype=float)
        if (values.shape!=(5,4) or not np.isfinite(values).all()
                or np.any(values[:2]<=0) or np.any(values[2]<=0) or np.any(values[4]<0)
                or (pinned_nodes==0 and np.any(values[:2].sum(axis=0)>=1))
                or (pinned_nodes==1 and np.any(abs(values[:2].sum(axis=0)-1)>2e-5))):
            raise ValueError('Qualified mass-weighted FE triangle parameters required')
        prepared.append((np.asarray(nodes,dtype=int),values))
    result=points.copy()
    for (nodes,values),scale in zip(prepared,scales):
        a=result[nodes[0]].copy();b=result[nodes[1]].copy();c=result[nodes[2]].copy()
        axis=b-a;length2=np.sum(axis*axis,axis=1);valid=length2>2**-23
        length=np.ones(4);length[valid]=np.sqrt(length2[valid])
        axis[valid]/=length[valid,None];axis[~valid]=[1,0,0]
        offset=c-a;axial=np.sum(offset*axis,axis=1)
        normal=offset-axis*axial[:,None];normal2=np.sum(normal*normal,axis=1)
        valid=normal2>2**-23;altitude=np.ones(4);altitude[valid]=np.sqrt(normal2[valid])
        normal[valid]/=altitude[valid,None];normal[~valid]=[0,1,0]
        w1,w2,edge,x,y=values
        if pinned_nodes==1:
            # Uniform positive scale cancels when normalizing the angle.
            cosine=length*edge*w1+(axial*x+altitude*y)*w2
            sine=(altitude*x-axial*y)*w2
            origin=a
        else:
            edge=edge*scale;x=x*scale;y=y*scale
            weights=np.stack([1-w1-w2,w1,w2],axis=1)
            current=np.stack([np.zeros((4,2)),np.c_[length,np.zeros(4)],np.c_[axial,altitude]],axis=1)
            authored=np.stack([np.zeros((4,2)),np.c_[edge,np.zeros(4)],np.c_[x,y]],axis=1)
            current_center=np.sum(current*weights[:,:,None],axis=1)
            authored_center=np.sum(authored*weights[:,:,None],axis=1)
            current-=current_center[:,None,:];authored-=authored_center[:,None,:]
            cosine=np.sum(np.sum(current*authored,axis=2)*weights,axis=1)
            sine=np.sum((authored[:,:,0]*current[:,:,1]-authored[:,:,1]*current[:,:,0])*weights,axis=1)
        norm2=cosine*cosine+sine*sine;valid=norm2>1e-14
        norm=np.ones(4);norm[valid]=np.sqrt(norm2[valid])
        cosine[valid]/=norm[valid];sine[valid]/=norm[valid]
        cosine[~valid]=1;sine[~valid]=0
        if pinned_nodes==0:
            rotated_x=authored_center[:,0]*cosine-authored_center[:,1]*sine
            rotated_y=authored_center[:,0]*sine+authored_center[:,1]*cosine
            origin=a+axis*(current_center[:,0]-rotated_x)[:,None]+normal*(current_center[:,1]-rotated_y)[:,None]
        else:
            edge=edge*scale;x=x*scale;y=y*scale
        next_b=origin+axis*(edge*cosine)[:,None]+normal*(edge*sine)[:,None]
        next_c=origin+axis*(x*cosine-y*sine)[:,None]+normal*(x*sine+y*cosine)[:,None]
        if pinned_nodes==0:
            for lane,node in enumerate(nodes[0]):result[node]=origin[lane]
        for lane,node in enumerate(nodes[1]):result[node]=next_b[lane]
        for lane,node in enumerate(nodes[2]):result[node]=next_c[lane]
    if not np.isfinite(result).all():raise ValueError('Compiled FE triangle projection became nonfinite')
    return result


def project_compiled_triangles(points,blocks,*,two_pin_count,one_pin_count,length_scales=None):
    """Dispatch cumulative compiled pin-count prefixes in Source's order.

    ``one_pin_count`` includes the two-pin prefix. Padding remains in its
    authored block; no lane is deduplicated or moved between pin layouts.
    """
    if (not isinstance(blocks,list) or type(two_pin_count) is not int
            or type(one_pin_count) is not int or not 0<=two_pin_count<=one_pin_count<=len(blocks)):
        raise ValueError('Ordered compiled FE triangle pin-count prefixes required')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    if scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Positive FE triangle length scales required')
    result=project_two_pin_triangles(points,blocks[:two_pin_count],length_scales=scales[:two_pin_count])
    result=project_mass_weighted_triangles(result,blocks[two_pin_count:one_pin_count],pinned_nodes=1,
                                         length_scales=scales[two_pin_count:one_pin_count])
    return project_mass_weighted_triangles(result,blocks[one_pin_count:],pinned_nodes=0,
                                          length_scales=scales[one_pin_count:])
