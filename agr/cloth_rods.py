"""Preserve scalar authored FE rods for explicit proxy solver experiments.

The projection is an approximate sequential position constraint, not a claim
about Source's integration, SIMD ordering or collision response. No exporter
enables it automatically. SIMD rod arrays duplicate the scalar constraints.
"""
from dataclasses import dataclass
import itertools
import math
import re

import numpy as np


@dataclass(frozen=True)
class ProxyRod:
    vertices: tuple[int, int]
    minimum_m: float
    maximum_m: float
    weight0: float
    relaxation: float


def decode_proxy_rods(definition, unit_scale=.0254):
    """Map named controls, checking rest positions; never guess nearest nodes."""
    if type(unit_scale) not in (int,float) or not math.isfinite(unit_scale) or unit_scale <= 0:
        raise ValueError('Invalid cloth unit scale')
    proxy=definition['proxy']
    fe=definition['physics']['m_pFeModel']
    vertices=np.asarray(proxy['vertices'],dtype=float)
    poses=np.asarray(fe['m_InitPose'],dtype=float)
    names=fe['m_CtrlName']
    if (vertices.ndim!=2 or vertices.shape[1]!=3 or poses.shape!=(len(names),8)
            or not np.isfinite(vertices).all() or not np.isfinite(poses).all()):
        raise ValueError('Invalid proxy/control rest arrays')
    enable=np.asarray(proxy['paint']['cloth_enable$0'],dtype=float)
    if enable.shape!=(len(vertices),) or not np.isfinite(enable).all() or np.any((enable<0)|(enable>1)):
        raise ValueError('Invalid proxy enable paint')
    mapping={}
    for node,name in enumerate(names):
        match=re.fullmatch(r'\$cloth_m0p(\d+)',name)
        if match is None:
            continue
        vertex=int(match[1])
        if not 0<=vertex<len(vertices):
            raise ValueError('Cloth control index outside proxy')
        if vertex in mapping.values():
            raise ValueError('Duplicate cloth control identity')
        if np.linalg.norm(vertices[vertex]-poses[node,:3])>1e-4:
            raise ValueError('Cloth control/rest position mismatch: '+name)
        mapping[node]=vertex
    rods=[]
    for row in fe['m_Rods']:
        nodes=row['nNode']
        if (len(nodes)!=2 or any(type(n) is not int or n not in mapping for n in nodes)
                or nodes[0]==nodes[1]):
            raise ValueError('Rod endpoint has no unambiguous proxy control')
        lo,hi,w,r=(row[k] for k in ('flMinDist','flMaxDist','flWeight0','flRelaxationFactor'))
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (lo,hi,w,r))
                or not 0<=lo<=hi or not 0<=w<=1 or not 0<=r<=1):
            raise ValueError('Invalid authored rod limits/weights')
        a,b=(mapping[n] for n in nodes)
        if (enable[a]==0 and w!=0) or (enable[b]==0 and w!=1):
            raise ValueError('Rod weights would move a static proxy pin')
        minimum,maximum=lo*unit_scale,hi*unit_scale
        if not math.isfinite(maximum):raise ValueError('Rod unit conversion overflow')
        rods.append(ProxyRod((a,b),minimum,maximum,w,r))
    return tuple(rods)


def project_rods(points, rods, iterations=1):
    """Return projected positions without mutating input; report residuals separately.

    Coincident endpoints with a positive minimum have no uniquely defined
    correction direction. Reject them rather than manufacture an orientation.
    """
    result=np.array(points,dtype=float,copy=True)
    if result.ndim!=2 or result.shape[1]!=3 or not np.isfinite(result).all():
        raise ValueError('Invalid rod projection points')
    if type(iterations) is not int or not 1<=iterations<=1000:
        raise ValueError('Invalid rod projection iteration count')
    for rod in rods:
        a,b=rod.vertices
        if not 0<=a<len(result) or not 0<=b<len(result) or a==b:
            raise ValueError('Rod index outside point array')
        if (not all(math.isfinite(v) for v in (rod.minimum_m,rod.maximum_m,rod.weight0,rod.relaxation))
                or not 0<=rod.minimum_m<=rod.maximum_m or not 0<=rod.weight0<=1 or not 0<=rod.relaxation<=1):
            raise ValueError('Invalid projection rod')
    for _ in range(iterations):
        for rod in rods:
            a,b=rod.vertices
            delta=result[b]-result[a]
            distance=float(np.linalg.norm(delta))
            target=min(rod.maximum_m,max(rod.minimum_m,distance))
            if distance<1e-12:
                if target>0 and rod.relaxation>0:
                    raise ValueError('Collapsed rod needs an explicit orientation')
                continue
            correction=delta*((distance-target)*rod.relaxation/distance)
            result[a]+=correction*rod.weight0
            result[b]-=correction*(1-rod.weight0)
    if not np.isfinite(result).all():raise ValueError('Rod solve produced non-finite points')
    return result


def rod_residuals(points, rods):
    """Unsigned distance outside each authored min/max interval, in metres."""
    points=np.asarray(points,dtype=float)
    return np.array([max(rod.minimum_m-(length:=float(np.linalg.norm(points[rod.vertices[1]]-points[rod.vertices[0]]))),
                         length-rod.maximum_m,0) for rod in rods])


def noncofacial_rod_edges(proxy, rods):
    """Missing connectivity for a native spring study, not rod-limit mapping."""
    cofacial=set()
    for face in proxy['faces']:
        cofacial.update(tuple(sorted(pair)) for pair in itertools.combinations(face,2))
    return sorted({tuple(sorted(rod.vertices)) for rod in rods}-cofacial)
