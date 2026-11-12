"""Projective proxy for unclamped spritecard depth bias.

Preserve screen XY and use the depth of P+viewDirection*bias. Carry the proxy/original
clip-W ratio so shader UV/color interpolation remains that of the original mesh.
Near/far or depth-floor cases are refused without native projection metadata.
"""
import math
import numpy as np
from .installed_effects import UnsupportedEffect

XY_CULL_POLICY='agr_rope_homogeneous_xy_trivial_reject_v1'


def discard_xy_clipped_triangles(geometry,projection):
    """Remove only triangles proven outside unchanged homogeneous XY/W planes.

    Depth bias changes Z, not X/Y/W. No Z-plane/depth-floor/frustum guesses,
    new clip vertices, attribute interpolation or partial-triangle cropping.
    Every remaining vertex still goes through strict depth-bias qualification.
    A conservative float32 dot/add error envelope retains uncertain boundaries.
    """
    if (not isinstance(geometry,dict) or set(geometry)-{'vertices','faces','uv','colors','subdivisions'}):
        raise UnsupportedEffect('Unknown depth-cull geometry streams')
    points=np.asarray(geometry['vertices'],dtype=float)
    if not len(points):return dict(geometry,depth_xy_cull=dict(policy=XY_CULL_POLICY,input_triangles=0,culled_triangles=0))
    matrix=np.asarray((projection or {}).get('world_to_clip'),dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or matrix.shape!=(4,4)or not np.isfinite(points).all()or not np.isfinite(matrix).all():
        raise UnsupportedEffect('Invalid homogeneous rope clipping inputs')
    homogeneous=np.column_stack((points,np.ones(len(points))));clip=homogeneous@matrix.T
    products=abs(homogeneous)@abs(matrix).T
    if (not np.isfinite(clip).all()or not np.isfinite(products).all()
        or np.any(abs(homogeneous)>np.finfo(np.float32).max/32)
        or np.any(abs(matrix)>np.finfo(np.float32).max/32)
        or np.any(products>np.finfo(np.float32).max/32)):
        raise UnsupportedEffect('Rope clipping exceeds finite float32 bounds')
    # gamma16 exceeds the operation depth of each float32 dot and plane add.
    # Uncertain/touching edges are retained, including subnormal-scale inputs.
    eps=np.finfo(np.float32).eps;gamma=16*eps/(1-16*eps)
    planes=np.column_stack((clip[:,3]+clip[:,0],clip[:,3]-clip[:,0],clip[:,3]+clip[:,1],clip[:,3]-clip[:,1],clip[:,3]))
    bounds=np.column_stack((products[:,3]+products[:,0],products[:,3]+products[:,0],products[:,3]+products[:,1],products[:,3]+products[:,1],products[:,3]))
    errors=gamma*bounds+1e-7;triangles=[]
    for face in geometry['faces']:
        if len(face)not in(3,4)or any(type(i)is not int or not 0<=i<len(points)for i in face):
            raise UnsupportedEffect('Invalid depth-cull rope topology')
        triangles.extend([tuple(face)]if len(face)==3 else[(face[0],face[1],face[2]),(face[0],face[2],face[3])])
    keep=[f for f in triangles if not np.any(np.all(planes[list(f)] < -errors[list(f)],axis=0))]
    used=sorted({i for face in keep for i in face});indices={old:new for new,old in enumerate(used)}
    result=dict(geometry,vertices=points[used],faces=[tuple(indices[i]for i in f)for f in keep],
        depth_xy_cull=dict(policy=XY_CULL_POLICY,input_triangles=len(triangles),culled_triangles=len(triangles)-len(keep)))
    for name,width in(('uv',2),('colors',4)):
        values=np.asarray(geometry[name],dtype=float)
        if values.shape!=(len(points),width)or not np.isfinite(values).all():raise UnsupportedEffect('Incomplete depth-cull rope attributes')
        result[name]=values[used]
    return result

def projective_depth_bias(vertices,bias,projection):
    points=np.asarray(vertices,dtype=float)
    if not len(points):return points,np.empty(0)
    if (points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all()
            or type(bias) not in (int,float) or not math.isfinite(bias) or abs(bias)>1e5):
        raise UnsupportedEffect('Invalid particle depth bias input')
    matrix=np.asarray((projection or {}).get('world_to_clip'),dtype=float)
    forward=np.asarray((projection or {}).get('forward'),dtype=float)
    if (matrix.shape!=(4,4) or forward.shape!=(3,) or not np.isfinite(matrix).all()
            or not np.isfinite(forward).all() or abs(np.linalg.norm(forward)-1)>1e-5):
        raise UnsupportedEffect('Depth-biased ropes require the sampled view projection and direction')
    try:inverse=np.linalg.inv(matrix)
    except np.linalg.LinAlgError as exc:raise UnsupportedEffect('Singular depth-bias view projection') from exc
    homogeneous=np.column_stack((points,np.ones(len(points))))
    original=homogeneous@matrix.T
    shifted=np.column_stack((points+forward*bias,np.ones(len(points))))@matrix.T
    # Depth bias never changes clip W. A whole mesh strictly behind the view
    # remains clipped for every possible biased Z; retain it without a proxy.
    # Mixed camera-plane geometry still requires explicit clipping support.
    if np.all(original[:,3]<-1e-8):
        return points.copy(),np.ones(len(points))
    if np.any(original[:,3]<=1e-8) or np.any(shifted[:,3]<=1e-8):
        raise UnsupportedEffect('Depth-biased rope crosses the camera plane')
    before=original[:,2]/original[:,3];after=shifted[:,2]/shifted[:,3]
    # The source floor is in zero-to-one clip depth. Admit only the branch where
    # it is inactive for either depth direction; exported Blender cameras use GL
    # projection. This avoids guessing a native near/far/reversed-depth profile.
    for sign in (1,-1):
        z0=original[:,3]*(1+sign*before)*.5
        z1=original[:,3]*(1+sign*after)*.5
        if np.any(z1<np.minimum(.001,z0)-1e-10):
            raise UnsupportedEffect('Clamped rope depth bias requires native projection metadata')
    if np.any(abs(before)>=1-1e-7) or np.any(abs(after)>=1-1e-7):
        raise UnsupportedEffect('Depth-biased rope reaches an unqualified near/far clip boundary')
    target=original.copy();target[:,2]=after*original[:,3]
    result=target@inverse.T
    if not np.isfinite(result).all() or np.any(result[:,3]<=1e-8):
        raise UnsupportedEffect('Invalid depth-bias unprojection')
    ratio=1/result[:,3];positions=result[:,:3]/result[:,3,None]
    if np.any(abs(positions)>1e8) or np.any(ratio>1e6):raise UnsupportedEffect('Depth-bias proxy exceeds bounds')
    return positions,ratio


def qualify_depth_card(vertices,bias,projection):
    """Keep strict proxy bounds for visible cards; prove invisible XY cases.

    The live graph keeps behind-camera vertices unchanged. With nonnegative
    bias, every front-camera vertex is multiplied by a positive projective
    factor. Thus each unchanged homogeneous XY/W plane sign is preserved.
    Entire triangles outside those planes cannot become visible. No source
    vertex is deleted, no near/depth-floor branch or partial clipping is guessed.
    """
    if type(bias)not in(int,float)or not math.isfinite(bias)or abs(bias)>1e5:
        raise UnsupportedEffect('Invalid particle depth bias input')
    if bias>=0:
        geometry=dict(vertices=vertices,faces=[(0,1,2),(0,2,3)],uv=[[0,0]]*4,colors=[[1,1,1,1]]*4,subdivisions=1)
        clipped=discard_xy_clipped_triangles(geometry,projection)
        if not clipped['faces']:return 'xy_clipped'
    projective_depth_bias(vertices,bias,projection)
    return 'depth_proxy'
