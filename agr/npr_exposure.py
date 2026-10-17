"""NPR probe exposure/color reference from C45 installed PBR GLSL.

Requires completed six-face irradiance (including any fallback) and explicit
per-view exposure inputs. This does not infer engine camera/exposure state.
"""
import numpy as np

RGB_TO_XYZ=np.array([[.4124,.3576,.1805],[.2126,.7152,.0722],[.0193,.1192,.9505]])
XYZ_TO_RGB=np.array([[3.2406,-1.5372,-.4986],[-.9689,1.8758,.0415],[.0557,-.204,1.057]])


def _xyy(rgb):
    xyz=rgb@RGB_TO_XYZ.T
    total=xyz.sum(axis=1)
    result=np.zeros_like(xyz)
    active=total>0
    result[active,:2]=xyz[active,:2]/total[active,None]
    result[active,2]=xyz[active,1]
    return result


def _retarget(raw,neutral,target,pbr_blend):
    current=_xyy(raw)
    if np.any(neutral[:,2]<=0):
        raise ValueError('Zero ambient reference luminance has unqualified shader division')
    weight=np.clip(current[:,2]/neutral[:,2],0,1)
    xy=neutral[:,:2]*(1-weight[:,None])+current[:,:2]*weight[:,None]
    xyz=np.zeros_like(raw)
    active=(xy[:,0]!=0)&(xy[:,1]!=0)&(target!=0)
    scale=target[active]/xy[active,1]
    xyz[active,0]=scale*xy[active,0]
    xyz[active,1]=target[active]
    xyz[active,2]=scale*(1-xy[active,0]-xy[active,1])
    return (xyz@XYZ_TO_RGB.T)*(1-pbr_blend)+raw*pbr_blend


def bounce_irradiance(faces,direction,blend,transmissive,*,exposure_enabled,
                      exposure_targets,pre_exposure,exposure_pbr_blend):
    """Faces in +X,+Y,+Z,-X,-Y,-Z order; direction/blend from npr_bounce.

    exposure_enabled must include both shader attribute and per-view gate.
    Returns signed linear RGB; no extra gamut clamp from viewer assumptions.
    """
    faces=np.asarray(faces,dtype=float);direction=np.asarray(direction,dtype=float)
    blend=np.asarray(blend,dtype=float);transmissive=np.asarray(transmissive,dtype=float)
    if faces.ndim!=3 or faces.shape[1:]!=(6,3):raise ValueError('Six RGB faces required')
    count=len(faces)
    if direction.shape!=(count,3) or blend.shape!=(count,) or transmissive.shape!=(count,3):raise ValueError('Invalid bounce input shapes')
    if not all(np.isfinite(v).all() for v in (faces,direction,blend,transmissive)) or np.any(faces<0) or np.any(transmissive<0) or np.any((blend<0)|(blend>1)):
        raise ValueError('Invalid bounce input values')
    if not np.allclose(np.linalg.norm(direction,axis=1),1,atol=1e-5,rtol=0):raise ValueError('Unit sampling directions required')
    if type(exposure_enabled) is not bool:raise ValueError('Explicit exposure gate required')
    targets=np.asarray(exposure_targets,dtype=float)
    if targets.shape!=(3,) or not np.isfinite(targets).all() or np.any(targets<0) or not np.isfinite(pre_exposure) or pre_exposure<=0 or not np.isfinite(exposure_pbr_blend) or not 0<=exposure_pbr_blend<=1:
        raise ValueError('Explicit finite exposure controls required')
    weights=np.concatenate([np.maximum(direction,0)**2,np.minimum(direction,0)**2],axis=1)
    raw=np.sum(faces*weights[:,:,None],axis=1);up=faces[:,2,:]
    if exposure_enabled:
        neutral=_xyy(faces.mean(axis=1))
        vertical=np.where(direction[:,2]>0,targets[0],targets[2])
        desired=(targets[1]*(direction[:,0]**2+direction[:,1]**2)+vertical*direction[:,2]**2)/pre_exposure
        raw=_retarget(raw,neutral,desired,exposure_pbr_blend)
        up=_retarget(up,neutral,np.full(count,targets[0]/pre_exposure),exposure_pbr_blend)
    result=raw+up*transmissive*(1-blend[:,None])
    if not np.isfinite(result).all():raise ValueError('Nonfinite NPR irradiance')
    return result
