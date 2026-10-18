"""Secondary-motion bake entry point: version-matched decompiled cloth proxies.

The experimental native resimulation (Seven waist surface cloth and the Haze
hose prototype) was retired in 0.3.0b1. Only prepared decompiled proxies
(agr_decompiled_fe_proxy_v1) bake; anything else fails explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

from .installed_cloth import UnsupportedCloth


def bake_secondary_motion(rig, definition_path, frames, fps, collection=None, *, unit_scale=.0254):
    """Bake a prepared secondary-motion definition onto its rig; never edit the body action."""
    from .blender_scene import _init_blender
    _init_blender()
    definition = json.loads(Path(definition_path).read_text(encoding='utf-8'))
    if definition.get('schema') == 'agr_decompiled_fe_proxy_v1':
        from .blender_fe_proxy import bake_fe_proxy
        return bake_fe_proxy(rig, definition, frames, fps, collection, unit_scale=unit_scale)
    if definition.get('schema') == 'agr_decompiled_fe_proxy_bundle_v1':
        from .blender_fe_proxy import bake_fe_proxy
        parts = definition.get('parts')
        if not isinstance(parts,list) or not 1 <= len(parts) <= 16 or any(
                p.get('schema') != 'agr_decompiled_fe_proxy_v1' or p.get('physics') != definition.get('physics')
                for p in parts):
            raise UnsupportedCloth('Invalid reconstructed garment bundle')
        completed, withheld = [], []
        frames = list(frames)
        for part in parts:
            try:
                completed.append(bake_fe_proxy(rig,part,frames,fps,collection,unit_scale=unit_scale))
            except (ValueError, RuntimeError, AssertionError, KeyError) as exc:
                withheld.append(part.get('mesh_suffix','unknown garment')+': '+str(exc))
        if not completed:
            raise UnsupportedCloth('No reconstructed garment completed its bake: '+'; '.join(withheld))
        return dict(status='baked_partial',schema=definition['schema'],parts=completed,
            approximation_only=not withheld,
            simulation_vertex_count=sum(p['simulation_vertex_count'] for p in completed),
            affected_render_vertex_count=sum(p['affected_render_vertex_count'] for p in completed),
            objects=[name for p in completed for name in p['objects']],
            body_action_edited=False,unsupported_parts=withheld,
            limitations=list(dict.fromkeys(v for p in completed for v in p['limitations'])))
    raise UnsupportedCloth('Unknown secondary-motion definition')
