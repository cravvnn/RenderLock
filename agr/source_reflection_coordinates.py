"""Installed shader reflection direction, with local VRF authored box mapping."""
import numpy as np
from agr.source_probe_assets import vector,rotation


def normalized(value):
    value=np.asarray(value,dtype=float);length=np.linalg.norm(value,axis=-1)
    if not np.isfinite(value).all() or np.any(length<1e-12):raise ValueError('Invalid reflection direction')
    return value/length[...,None]


def coordinates(properties,positions,normals,view_directions,roughness,*,roughness_floor=.05,mip_scale=6):
    if not np.isfinite(roughness_floor) or not 0<=roughness_floor<=1 or not np.isfinite(mip_scale) or mip_scale<0:
        raise ValueError('Invalid reflection sampling parameters')
    p=properties
    if p.get('parentname') or p.get('uselocaloffset'):raise ValueError('Parented cubemap unqualified')
    points=np.asarray(positions,dtype=float);n=np.asarray(normals,dtype=float);view=np.asarray(view_directions,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all() or n.shape!=points.shape or view.shape!=points.shape:raise ValueError('Invalid reflection inputs')
    for direction in (n,view):
        if not np.isfinite(direction).all() or not np.allclose(np.linalg.norm(direction,axis=1),1,atol=1e-5,rtol=0):raise ValueError('Expected unit world directions')
    rough=np.broadcast_to(np.asarray(roughness,dtype=float),(len(points),))
    if not np.isfinite(rough).all() or np.any(rough<0) or np.any(rough>1):raise ValueError('Invalid roughness')
    rough=np.maximum(rough,roughness_floor);squared=rough*rough;smooth=1-squared
    dominant=smooth*(np.sqrt(smooth)+squared)
    reflected=2*np.sum(n*view,axis=1)[:,None]*n-view
    ray=normalized(n+(reflected-n)*dominant[:,None])
    axes=rotation(p['angles']);local=(points-vector(p['origin']))@axes.T;local_ray=ray@axes.T
    low,high=vector(p['box_mins'])-.02,vector(p['box_maxs'])+.02
    if np.any(high<=low) or np.any(local<=low) or np.any(local>=high):raise ValueError('Box projection requires a point strictly inside the active expanded volume')
    with np.errstate(divide='ignore',invalid='ignore'):
        far=np.maximum((high-local)/local_ray,(low-local)/local_ray)
    far=np.where(local_ray==0,np.inf,far)
    distance=np.min(far,axis=1)
    corrected=normalized(local+local_ray*abs(distance[:,None]))
    sample=normalized(corrected+(local_ray-corrected)*rough[:,None])
    return dict(direction=sample,lod=mip_scale*np.sqrt(rough),dominant_direction=ray,parallax_direction=corrected)


def normalization_sh(coefficients):
    """Local VRF radiance coefficient mapping; engine constant mapping separate."""
    c=np.asarray(coefficients,dtype=float)
    if c.shape!=(27,) or not np.isfinite(c).all():raise ValueError('Expected 27 radiance coefficients')
    luma=np.array([.2125,.7154,.0721])@c.reshape(3,9)[:,:4]
    return np.array([luma[3]*.325735,luma[1]*.325735,luma[2]*.325735,luma[0]*.282095])
