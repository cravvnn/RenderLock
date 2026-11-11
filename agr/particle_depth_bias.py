"""Projective proxy for unclamped spritecard depth bias.

Preserve screen XY and use the depth of P+viewDirection*bias. Carry the proxy/original
clip-W ratio so shader UV/color interpolation remains that of the original mesh.
Near/far or depth-floor cases are refused without native projection metadata.
"""
import math
import numpy as np
from .installed_effects import UnsupportedEffect

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
