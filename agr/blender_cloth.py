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
    raise UnsupportedCloth('Unknown secondary-motion definition')
