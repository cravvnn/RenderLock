"""Research probe weights: installed shader algebra, local VRF box constants.

The .02-unit bounds expansion and priority order are local renderer mappings,
not captured engine constants. Irradiance color/scale is deliberately separate.
"""
import numpy as np
from agr.source_probe_assets import vector, rotation


def contributing_actor_probes(probes, coverage):
    """Keep every possible contributor over each verified actor trajectory.

    The blend is sequential, including equal-priority siblings. Once an entire
    convex actor box has alpha one, later probes have zero remaining weight.
    Intersecting predecessors are retained even when they cover only part of a
    surface. Conservative box intersection includes triangle interiors.
    """
    from itertools import product
    from .source_probe_assets import bounds_array, intersects_bounds
    probe_weights(probes,np.empty((0,3)))
    ordered=sorted(probes,key=lambda p:(-p['indoor_outdoor_level'],p['array_index']))
    identities=[str(p['hammeruniqueid']) for p in ordered]
    if len(set(identities))!=len(identities):raise ValueError('Duplicate probe identity')
    if (coverage.get('status')!='verified_integer_frame_bounds'
            or coverage.get('scope')!='per_actor_convex_bounds' or not coverage.get('frames')):
        raise ValueError('Verified per-actor surface coverage required')
    selected={};expected=None;frames=set()
    for frame in coverage['frames']:
        if type(frame.get('frame')) is not int or frame['frame'] in frames:
            raise ValueError('Distinct integer coverage frames required')
        frames.add(frame['frame']);seen=set()
        for actor in frame['actors']:
            identity=actor['entity_id']
            if not isinstance(identity,str) or not identity or identity in seen:
                raise ValueError('Distinct actor coverage required')
            seen.add(identity);keep=selected.setdefault(identity,set())
            bounds=bounds_array(np.column_stack((actor['low'],actor['high'])))
            corners=np.array(list(product(*bounds)));covered=False
            for prop in ordered:
                if not intersects_bounds(prop,bounds):continue
                keep.add(str(prop['hammeruniqueid']))
                local=(corners-vector(prop['origin']))@rotation(prop['angles']).T
                low=vector(prop['box_mins'])+vector(prop['edge_fade_dists'])
                high=vector(prop['box_maxs'])-vector(prop['edge_fade_dists'])
                if np.all(local>=low) and np.all(local<=high):
                    covered=True;break
            if not covered:raise ValueError('No fully covering actor probe for graph selection')
        if not seen or (expected is not None and seen!=expected):
            raise ValueError('Actor coverage differs between frames')
        expected=seen
    return {identity:[p for p in ordered if str(p['hammeruniqueid']) in keep]
            for identity,keep in selected.items()}


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
