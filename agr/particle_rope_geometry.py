"""Ordered particle ribbons following VRF 42743fb's RenderRopes geometry.

Geometry only: this does not assert support for the effect's simulation, material
or camera animation. Return source-unit vertices, raw card UVs and linear RGBA.
Reference: RenderRopes.cs and CableMeshBuilder.cs (ValveResourceFormat, MIT).
"""
from __future__ import annotations
import math
import numpy as np
from .installed_effects import UnsupportedEffect, scalar

_F=np.float32
_EPS=1e-12


def _vector(value, count):
    result=np.asarray(value,dtype=np.float32)
    if result.shape!=(count,) or not np.isfinite(result).all():
        raise UnsupportedEffect('Invalid rope vector')
    return result


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise UnsupportedEffect('Invalid rope scalar')
    result=_F(value)
    if not np.isfinite(result):raise UnsupportedEffect('Rope scalar exceeds float32 range')
    return result


def catmull_rom(p0,p1,p2,p3,t):
    t=_F(t);t2=t*t;t3=t2*t
    return _F(.5)*((_F(2)*p1)+((-p0+p2)*t)
        +((_F(2)*p0-_F(5)*p1+_F(4)*p2-p3)*t2)
        +((-p0+_F(3)*p1-_F(3)*p2+p3)*t3))


def catmull_tangent(p0,p1,p2,p3,t):
    t=_F(t)
    return _F(.5)*((-p0+p2)+((_F(4)*p0-_F(10)*p1+_F(8)*p2-_F(2)*p3)*t)
        +((-_F(3)*p0+_F(9)*p1-_F(9)*p2+_F(3)*p3)*(t*t)))


def _normalize(value,fallback):
    length2=np.dot(value,value)
    return value/_F(np.sqrt(length2)) if length2>_EPS else np.array(fallback,dtype=np.float32)


def rope_sample(nodes,t,orientation,camera):
    """Sample one segment; nodes have position/radius/color/normal arrays."""
    n0,n1,n2,n3=nodes;t=_F(t)
    position=catmull_rom(*(n['position'] for n in nodes),t)
    radius=catmull_rom(*(n['radius'] for n in nodes),t)
    tangent=_normalize(catmull_tangent(*(n['position'] for n in nodes),t),(1,0,0))
    if orientation=='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL':
        plane=n1['normal']+t*(n2['normal']-n1['normal'])
    elif orientation=='PARTICLE_ORIENTATION_WORLD_Z_ALIGNED':
        plane=np.array((0,0,1),dtype=np.float32)
    else:plane=camera-position
    width=np.cross(tangent,plane)
    if np.dot(width,width)<=_EPS:
        width=np.cross(tangent,(0,0,1) if abs(tangent[2])<.999 else (1,0,0))
    width=_normalize(width,(0,1,0))
    color=np.clip(n1['color']+t*(n2['color']-n1['color']),0,1)
    return {'position':position,'radius':radius,'width':width,'tangent':tangent,'color':color}


def rope_collection_bounds(particles, definition=None):
    """Native finite leaf bounds: particle radius plus authored box padding."""
    definition = definition or {}
    if definition.get('m_bInfiniteBounds'):
        raise UnsupportedEffect('Infinite rope collection bounds are not captured')
    if definition.get('m_Children') or any(r.get('_class') == 'C_OP_RenderTrails'
            for r in definition.get('m_Renderers', [])):
        raise UnsupportedEffect('Rope child/trail-expanded collection bounds are not captured')
    low_pad = _vector(definition.get('m_BoundingBoxMin', [-10]*3), 3)
    high_pad = _vector(definition.get('m_BoundingBoxMax', [10]*3), 3)
    if np.any(low_pad > high_pad):
        raise UnsupportedEffect('Invalid rope collection bounding-box padding')
    if not particles:
        raise UnsupportedEffect('Empty rope has no finite collection bounds')
    positions = np.array([_vector(p['position'], 3) for p in particles], dtype=np.float32)
    radii = np.array([_number(p['radius']) for p in particles], dtype=np.float32)
    if np.any(radii < 0):
        raise UnsupportedEffect('Negative particle radius cannot establish rope bounds')
    with np.errstate(over='ignore', invalid='ignore'):
        low = (positions-radii[:, None]).min(axis=0)+low_pad
        high = (positions+radii[:, None]).max(axis=0)+high_pad
    if not np.isfinite(low).all() or not np.isfinite(high).all():
        raise UnsupportedEffect('Rope collection bounds exceed float32 range')
    return low, high


def rope_subdivisions(particles, renderer, camera_position=None, camera_projection=None, definition=None):
    """Current native collection-level projected-size density, rounded upward to a power of two."""
    minimum = renderer.get('m_nMinTesselation', 1)
    maximum = renderer.get('m_nMaxTesselation', 128)
    if (type(minimum) is not int or type(maximum) is not int
            or any(not -2**31 <= value < 2**31 for value in (minimum, maximum))):
        raise UnsupportedEffect('Invalid rope tessellation bounds')
    if minimum == maximum and 1 <= minimum <= 128:
        return 1 << (minimum-1).bit_length()
    if camera_position is None or not isinstance(camera_projection, dict):
        raise UnsupportedEffect('Adaptive rope tessellation requires a sampled camera projection')
    camera = _vector(camera_position, 3)
    low, high = rope_collection_bounds(particles, definition)
    with np.errstate(over='ignore', invalid='ignore'):
        delta = high-low
        diagonal = _F(np.sqrt(_F(_F(delta[2]*delta[2]+delta[1]*delta[1])+delta[0]*delta[0])))
        centre = (low+high)*_F(.5)
    if not np.isfinite(diagonal) or not np.isfinite(centre).all():
        raise UnsupportedEffect('Rope projected bounds exceed float32 range')
    if camera_projection.get('type') == 'ORTHO':
        height = _number(camera_projection.get('height'))
        if height <= 0:
            raise UnsupportedEffect('Invalid orthographic rope projection height')
        coverage = np.clip(_F(_F(2)*diagonal/height), 0, 1)
    elif camera_projection.get('type') == 'PERSP':
        projection_y = _number(camera_projection.get('projection_y'))
        if projection_y <= 0:
            raise UnsupportedEffect('Invalid perspective rope projection scale')
        with np.errstate(over='ignore', invalid='ignore'):
            distance = centre-camera
            distance2 = _F(_F(distance[1]*distance[1]+distance[0]*distance[0])+distance[2]*distance[2])
        if diagonal*diagonal > distance2:
            coverage = _F(1)
        elif distance2 == 0:
            coverage = _F(0)
        else:
            coverage = np.clip(_F(_F(diagonal*projection_y)*_F(1/np.sqrt(distance2))), 0, 1)
    else:
        raise UnsupportedEffect('Unsupported rope camera projection')
    scale = _number(renderer.get('m_flTessScale', 1))
    metric = _F(_F(np.sqrt(_F(coverage)))*_F(1024))
    metric = _F(metric*_F(_F(1)/_F(len(particles))))
    with np.errstate(over='ignore', invalid='ignore'):
        measured = _F(_F(metric*scale)*_F(.1))
    if not np.isfinite(measured) or not -2**31 <= measured < 2**31:
        raise UnsupportedEffect('Rope tessellation metric exceeds native integer range')
    raw = int(measured)
    count = max(1, min(128, max(minimum, min(maximum, raw))))
    return 1 << (count-1).bit_length()


def build_rope_geometry(particles,renderer,age=0.,camera_position=None,*,camera_projection=None,definition=None):
    """Build fixed or sampled adaptive ribbons, preserving live array order.

    CP-driven texture coordinates, non-normal vector fields and camera fading
    remain explicit gaps. Bookworm's authored geometry uses
    fixed tessellation 2, normal orientation and scratch-float scalar coordinates.
    """
    subdivisions = rope_subdivisions(particles, renderer, camera_position, camera_projection, definition) if len(particles) >= 2 else 1
    if renderer.get('m_bEnableFadingAndClamping') or renderer.get('m_flStartFadeDot',1)<1:
        raise UnsupportedEffect('Camera-dependent rope fading is not implemented')
    if renderer.get('m_nTextureVParamsCP',-1)!=-1 or any(renderer.get(k,0)!=0 for k in
            ('m_flScaleVSizeByControlPointDistance','m_flScaleVScrollByControlPointDistance','m_flScaleVOffsetByControlPointDistance')):
        raise UnsupportedEffect('Control-point rope texture scaling is not implemented')
    if renderer.get('m_nVectorFieldForOrientation',21)!=21:
        raise UnsupportedEffect('Rope orientation vector field is not captured')
    orientation=renderer.get('m_nOrientationType','PARTICLE_ORIENTATION_SCREEN_ALIGNED')
    if orientation not in ('PARTICLE_ORIENTATION_SCREEN_ALIGNED','PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL','PARTICLE_ORIENTATION_WORLD_Z_ALIGNED'):
        raise UnsupportedEffect('Unsupported rope orientation: '+orientation)
    if orientation=='PARTICLE_ORIENTATION_SCREEN_ALIGNED' and camera_position is None:
        raise UnsupportedEffect('Screen-aligned rope requires a sampled camera position')
    camera=_vector((0,0,0) if camera_position is None else camera_position,3)
    use_scalar=renderer.get('m_bUseScalarForTextureCoordinate',False)
    scalar_field=renderer.get('m_nScalarFieldForTextureCoordinate',8)
    fields={1:'life',3:'radius',7:'alpha',8:'creation_time',10:'trail',16:'alpha_alternate',18:'scratch_float0'}
    if use_scalar and scalar_field not in fields:raise UnsupportedEffect('Rope UV scalar field is not captured')
    radius_scale=_number(scalar(renderer.get('m_flRadiusScale'),default=1))
    alpha_scale=_number(scalar(renderer.get('m_flAlphaScale'),default=1))
    scalar_scale=_number(renderer.get('m_flScalarAttributeTextureCoordScale',1))
    nodes=[];identities=set()
    for p in particles:
        if p['id'] in identities:raise UnsupportedEffect('Duplicate rope particle identity')
        identities.add(p['id'])
        if use_scalar and fields[scalar_field] not in p:raise UnsupportedEffect('Missing rope UV scalar state')
        alpha=_number(p['alpha'])*_number(p['alpha_alternate'])*alpha_scale
        nodes.append({'position':_vector(p['position'],3),'radius':_number(p['radius'])*radius_scale,
                      'normal':_vector(p['normal'],3),'color':np.r_[_vector(p['color'],3),alpha].astype(np.float32),
                      'uv_scalar':_number(p[fields[scalar_field]])*scalar_scale if use_scalar else _F(0)})
    if renderer.get('m_bReverseOrder'):nodes.reverse()
    result={'vertices':[],'faces':[],'uv':[],'colors':[],'subdivisions':subdivisions}
    count=len(nodes)
    if count<2:return result
    closed=renderer.get('m_bClosedLoop',False);segments=count if closed else count-1
    if segments*subdivisions>10922:raise UnsupportedEffect('Rope exceeds supported quad budget')
    def node(index):return nodes[index%count if closed else max(0,min(count-1,index))]
    worldsize=_number(scalar(renderer.get('m_flTextureVWorldSize'),default=10))
    if worldsize<=0:raise UnsupportedEffect('Nonpositive rope texture world size')
    arc=_number(scalar(renderer.get('m_flTextureVScrollRate'),default=0))*_number(age)
    offset=_number(scalar(renderer.get('m_flTextureVOffset'),default=0))
    def map_v(v):
        value=v if use_scalar else v*_F(_F(1)/worldsize)+offset
        return float(np.clip(value,0,1) if renderer.get('m_bClampV') else value)
    previous_width=np.zeros(3,dtype=np.float32)
    def aligned_sample(control,t):
        nonlocal previous_width
        sample=rope_sample(control,t,orientation,camera)
        if np.dot(sample['width'],previous_width)<0:sample['width']=-sample['width']
        previous_width=sample['width'];return sample
    for segment in range(segments):
        control=[node(segment+i) for i in (-1,0,1,2)];n1,n2=control[1:3]
        v0=arc;v1=arc+_F(np.linalg.norm(n2['position']-n1['position']));arc=v1
        if use_scalar:v0=n1['uv_scalar'];v1=n2['uv_scalar']
        previous=aligned_sample(control,0)
        for step in range(1,subdivisions+1):
            t=_F(step/subdivisions);current=aligned_sample(control,t)
            low=map_v(v0+(v1-v0)*_F((step-1)/subdivisions));high=map_v(v0+(v1-v0)*t)
            a=previous;b=current;start=len(result['vertices'])
            result['vertices'].extend(v.tolist() for v in (a['position']-a['width']*a['radius'],
                b['position']-b['width']*b['radius'],b['position']+b['width']*b['radius'],a['position']+a['width']*a['radius']))
            result['faces'].append([start+i for i in range(4)])
            result['uv'].extend(((0,low),(0,high),(1,high),(1,low)))
            result['colors'].extend(c.tolist() for c in (a['color'],b['color'],b['color'],a['color']))
            previous=current
    return result
