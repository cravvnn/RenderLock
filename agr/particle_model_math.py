"""Qualified model-particle normal frame and local Euler placement."""
import math
import numpy as np
from .installed_effects import UnsupportedEffect

def component_scale(renderer):
    value=renderer.get('m_vecComponentScale')
    if value is None:return np.ones(3)
    if not isinstance(value,dict) or value.get('m_nType','PVEC_TYPE_LITERAL')!='PVEC_TYPE_LITERAL':
        raise UnsupportedEffect('Model component scale requires an explicit literal vector')
    result=value.get('m_vLiteralValue')
    if (not isinstance(result,(list,tuple)) or len(result)!=3
            or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1e6 for v in result)
            or type(renderer.get('m_bLocalScale',False)) is not bool):
        raise UnsupportedEffect('Model component scale requires finite nonnegative axes and a boolean scale mode')
    return np.asarray(result,dtype=np.float64)

def model_normal_frame(normal,renderer):
    normal=np.asarray(normal,dtype=np.float64)
    if normal.shape!=(3,)or not np.isfinite(normal).all():raise UnsupportedEffect('Invalid model particle normal')
    if renderer.get('m_bIgnoreNormal') or np.linalg.norm(normal)==0:return np.eye(3)
    length=np.linalg.norm(normal)
    if not 1e-6<=length<=1e6:raise UnsupportedEffect('Model orientation normal exceeds qualified magnitude bounds')
    normal=normal/length
    x,y,z=normal
    side=np.array([z+(1-z)*y*y,0,-x]);length=np.linalg.norm(side)
    if length>0:side/=length
    side-=np.dot(side,normal)*normal;length=np.linalg.norm(side)
    if length==0:raise UnsupportedEffect('Degenerate model particle orientation')
    side/=length;up=np.cross(normal,side)
    return np.column_stack((up,-side,normal))if renderer.get('m_bOrientZ',False)else np.column_stack((normal,side,up))

def model_matrix(particle,renderer):
    rotation=np.asarray(particle['rotation'],dtype=np.float64)
    position=np.asarray(particle['position'],dtype=np.float64)
    radius=particle['radius']
    if any(v.shape!=(3,) or not np.isfinite(v).all() for v in (rotation,position)) or type(radius) not in (int,float) or not math.isfinite(radius) or radius<0:
        raise UnsupportedEffect('Invalid model particle transform')
    frame=model_normal_frame(particle['normal'],renderer)
    yaw,pitch,roll=rotation
    sy,cy=math.sin(yaw),math.cos(yaw);sp,cp=math.sin(pitch),math.cos(pitch);sr,cr=math.sin(roll),math.cos(roll)
    euler=np.array([[cy*cp,cy*sp*sr-sy*cr,cy*sp*cr+sy*sr],
                    [sy*cp,sy*sp*sr+cy*cr,sy*sp*cr-cy*sr],[-sp,cp*sr,cp*cr]])
    scale=np.diag(component_scale(renderer))
    # Installed m_bLocalScale=true scales model axes after Euler rotation;
    # false scales the normal frame before Euler rotation. Not world-axis scale.
    linear=frame@euler@scale if renderer.get('m_bLocalScale',False) else frame@scale@euler
    matrix=np.eye(4);matrix[:3,:3]=linear*(1 if renderer.get('m_bIgnoreRadius') else radius);matrix[:3,3]=position
    return matrix
