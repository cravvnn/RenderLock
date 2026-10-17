"""Exact recorded render-state metadata; does not infer GPU instance state."""
import json


def render_state_track(entity):
    samples=entity.get('samples',[])
    if not any('network_render_state' in s for s in samples):
        return None
    rows=[];previous=None
    for sample in sorted(samples,key=lambda s:s['tick']):
        tick=sample['tick']
        if type(tick) is not int or tick<0 or (previous is not None and tick<=previous):
            raise ValueError('Render-state track needs distinct nonnegative integer ticks')
        previous=tick
        value=sample.get('network_render_state')
        if value is not None:
            if not isinstance(value,dict) or not value or set(value)-{'rgba8_packed','mode'}:
                raise ValueError('Invalid network render-state record')
            if any(type(v) is not int or not 0<=v<=0xffffffff for v in value.values()):
                raise ValueError('Network render-state values must be uint32')
            value=dict(value)
        rows.append(dict(tick=tick,state=value))
    return dict(schema_version=1,entity_id=str(entity['id']),samples=rows,
                scope='Exact observed network fields; null means unavailable. No interpolation, gap filling, shader binding or runtime proxy inference.')


def attach_render_state(root,entity):
    """Store exact integers as JSON, avoiding Blender float F-curve rounding."""
    track=render_state_track(entity)
    if track is None:
        return dict(status='not_recorded')
    root['agr_network_render_state']=json.dumps(track,separators=(',',':'))
    return dict(status='recorded_metadata',samples=len(track['samples']),
                available_samples=sum(r['state'] is not None for r in track['samples']),
                property='agr_network_render_state',shader_applied=False)
