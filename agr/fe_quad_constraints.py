"""Compiled FE quad operations, qualified separately by mass layout."""
import numpy as np


_EPSILON=2.**-23


def _basis(primary,secondary):
    norm2=float(primary@primary)
    x=primary/np.sqrt(norm2) if norm2>=_EPSILON else np.array([1.,0,0])
    y=secondary-x*(x@secondary)
    norm2=float(y@y)
    if norm2<_EPSILON:
        y=np.array([-x[1],x[0],0]) if abs(x[2])<abs(x[0]) else np.array([0,-x[2],x[1]])
        norm2=float(y@y)
    y=y/np.sqrt(norm2)
    return np.stack((x,y,np.cross(x,y)))


def project_two_pin_quads(points,blocks,*,length_scales=None):
    """Project free C/D vertices; preserve pinned A/B and compiled write order.

    Positions and compiled shapes use Source units. This ports one native
    operation, including native fallback bases, without choosing its schedule.
    Other mass layouts and nonzero authored slack remain unqualified.
    """
    points=np.asarray(points,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or not np.isfinite(points).all() or not isinstance(blocks,list) or len(blocks)>100000):
        raise ValueError('Bounded finite FE quad positions and compiled blocks required')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    if scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Positive finite four-lane FE quad length scales required')
    plans=[]
    for block in blocks:
        nodes=np.asarray(block['nNode'])
        if (nodes.shape!=(4,4) or nodes.dtype.kind not in 'iu' or np.any(nodes<0)
                or np.any(nodes>=len(points)) or any(len(set(nodes[:,i]))!=4 for i in range(4))):
            raise ValueError('FE quad indices must name four distinct valid nodes per lane')
        shape=np.asarray(block['vShape'],dtype=float)
        weights=np.asarray(block['f4Weights'],dtype=float)
        slack=np.asarray(block['f4Slack'],dtype=float)
        if (shape.shape!=(4,12) or weights.shape!=(4,4) or slack.shape!=(4,)
                or not all(np.isfinite(a).all() for a in (shape,weights,slack))):
            raise ValueError('Finite compiled FE quad shape, weights and slack required')
        if (np.any(weights[:2]!=0) or np.any(weights[2:]<0)
                or np.any(abs(weights[2:].sum(axis=0)-1)>2e-5) or np.any(slack!=0)):
            raise ValueError('Only two pinned FE quad nodes with zero slack are qualified')
        plans.append((nodes,shape.reshape(4,3,4).transpose(2,0,1),weights))
    result=points.copy()
    for (nodes,shape,weights),scale in zip(plans,scales):
        before=result[nodes].copy();outputs=np.empty((2,4,3))
        for lane in range(4):
            a,b,c,d=before[:,lane]
            middle=(a+b)*.5
            basis=_basis(b-a,c+d-2*middle)
            local=np.stack((c-middle,d-middle))@basis.T
            authored=shape[lane,2:]
            mass=weights[2:,lane]
            cosine=float(np.sum(mass*(authored[:,1]*local[:,1]+authored[:,2]*local[:,2])))
            sine=float(np.sum(mass*(authored[:,2]*local[:,1]-authored[:,1]*local[:,2])))
            norm2=cosine*cosine+sine*sine
            if norm2>_EPSILON:
                inverse=1/np.sqrt(norm2);cosine*=inverse;sine*=inverse
            else:cosine,sine=1.,0.
            rotated=authored.copy()
            rotated[:,1]=authored[:,1]*cosine+authored[:,2]*sine
            rotated[:,2]=authored[:,2]*cosine-authored[:,1]*sine
            outputs[:,lane]=middle+(rotated@basis)*scale[lane]
        # All lanes gather before writing. Padding and shared-node overwrites
        # follow the original C-lanes-then-D-lanes order.
        for side in range(2):
            for lane in range(4):result[nodes[side+2,lane]]=outputs[side,lane]
    if not np.isfinite(result).all():raise ValueError('FE quad output became nonfinite')
    return result


def project_compiled_quads(points,blocks,*,two_pin_count,one_pin_count,length_scales=None):
    """Dispatch authenticated two-pin and free compiled prefixes in order."""
    if (not isinstance(blocks,list) or type(two_pin_count) is not int
            or type(one_pin_count) is not int or not 0<=two_pin_count==one_pin_count<=len(blocks)):
        raise ValueError('Qualified FE quad prefixes require no one-pinned blocks')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    if scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Positive finite four-lane FE quad length scales required')
    result=project_two_pin_quads(points,blocks[:two_pin_count],length_scales=scales[:two_pin_count])
    return project_free_quads(result,blocks[two_pin_count:],length_scales=scales[two_pin_count:])


def project_free_quads(points,blocks,*,length_scales=None,relaxation=None):
    """Project a compiled free quad with Source's linear angular correction.

    Each lane uses its current diagonal basis and mass center. The original
    operation solves the current point inertia for an angular correction; it
    does not perform a full rigid-fit SVD. Zero slack is the qualified domain.
    """
    points=np.asarray(points,dtype=float)
    if (points.ndim!=2 or points.shape[1:]!=(3,) or not 1<=len(points)<=4096
            or not np.isfinite(points).all() or not isinstance(blocks,list) or len(blocks)>100000):
        raise ValueError('Bounded finite FE quad positions and compiled blocks required')
    scales=np.ones((len(blocks),4)) if length_scales is None else np.asarray(length_scales,dtype=float)
    relax=np.ones((len(blocks),4)) if relaxation is None else np.asarray(relaxation,dtype=float)
    if (scales.shape!=(len(blocks),4) or not np.isfinite(scales).all() or np.any(scales<=0)
            or relax.shape!=(len(blocks),4) or not np.isfinite(relax).all() or np.any((relax<0)|(relax>1))):
        raise ValueError('Positive quad scales and bounded relaxation required')
    plans=[]
    for block in blocks:
        nodes=np.asarray(block['nNode'])
        if (nodes.shape!=(4,4) or nodes.dtype.kind not in 'iu' or np.any(nodes<0)
                or np.any(nodes>=len(points)) or any(len(set(nodes[:,i]))!=4 for i in range(4))):
            raise ValueError('FE quad indices must name four distinct valid nodes per lane')
        shape=np.asarray(block['vShape'],dtype=float);weights=np.asarray(block['f4Weights'],dtype=float)
        slack=np.asarray(block['f4Slack'],dtype=float)
        if (shape.shape!=(4,12) or weights.shape!=(4,4) or slack.shape!=(4,)
                or not all(np.isfinite(a).all() for a in (shape,weights,slack))
                or np.any(weights<=0) or np.any(abs(weights.sum(axis=0)-1)>2e-5) or np.any(slack!=0)):
            raise ValueError('Finite free FE quad shape, positive mass weights and zero slack required')
        plans.append((nodes,shape.reshape(4,3,4).transpose(2,0,1),weights))
    result=points.copy()
    for (nodes,shape,weights),scale,blend in zip(plans,scales,relax):
        before=result[nodes].copy();outputs=np.empty_like(before)
        for lane in range(4):
            positions=before[:,lane];mass=weights[:,lane]
            basis=_basis(positions[2]-positions[0],positions[3]-positions[1])
            center=np.sum(positions*mass[:,None],axis=0)
            current=positions-center;authored=(shape[lane]@basis)*scale[lane]
            inertia=sum(weight*(np.eye(3)*(point@point)-np.outer(point,point))
                        for point,weight in zip(current,mass))
            torque=np.sum(np.cross(current,authored)*mass[:,None],axis=0)
            omega=np.zeros(3)
            try:
                factor=np.linalg.cholesky(inertia)
                if np.sum(1/np.diag(factor))<1e7:
                    omega=np.linalg.solve(factor.T,np.linalg.solve(factor,torque))
            except np.linalg.LinAlgError:
                pass # Native inverse-factor validity mask also clears this lane.
            corrected=authored-np.cross(omega,authored)
            outputs[:,lane]=center+current*(1-blend[lane])+corrected*blend[lane]
        for side in range(4):
            for lane in range(4):result[nodes[side,lane]]=outputs[side,lane]
    if not np.isfinite(result).all():raise ValueError('FE quad output became nonfinite')
    return result
