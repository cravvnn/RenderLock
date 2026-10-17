"""Research probe weights: installed shader algebra, local VRF box constants.

The .02-unit bounds expansion and priority order are local renderer mappings,
not captured engine constants. Irradiance color/scale is deliberately separate.
"""
import numpy as np
from agr.source_probe_assets import vector, rotation


def probe_weights(probes, positions):
    points=np.asarray(positions,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():
        raise ValueError('Invalid Source positions')
    indices=[p['array_index'] for p in probes]
    if any(type(i) is not int or i<0 for i in indices) or len(set(indices))!=len(indices):
        raise ValueError('Probe array indices must be unique nonnegative integers')
    if any(type(p['indoor_outdoor_level']) is not int for p in probes):
        raise ValueError('Invalid probe priority')
    ordered=sorted(probes,key=lambda p:(-p['indoor_outdoor_level'],p['array_index']))
    remaining=np.ones(len(points));rows=[]
    for p in ordered:
        if p.get('parentname') or p.get('uselocaloffset') or p.get('startdisabled'):
            raise ValueError('Unqualified authored probe state')
        low,high=vector(p['box_mins']),vector(p['box_maxs']);fade=vector(p['edge_fade_dists'])
        if np.any(high<=low) or np.any(fade<0):raise ValueError('Invalid probe box/fade')
        local=(points-vector(p['origin']))@rotation(p['angles']).T
        left=np.clip((local-low+.02)/(fade+.02),0,1)
        right=np.clip((high+.02-local)/(fade+.02),0,1)
        edge=np.minimum(left,right).min(axis=1)
        alpha=edge*edge*(3-2*edge)
        weight=alpha*remaining
        remaining*=1-alpha
        rows.append({'id':str(p['hammeruniqueid']),'alpha':alpha,'weight':weight})
    return rows,remaining
