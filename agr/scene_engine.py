"""Render engine the saved scene is built for.

Engine-dependent lights and materials are built once, so the choice is made before any content is created.
A prepared map is authored for EEVEE: in Cycles its surfaces render far too bright (review finding, cause in
the map materials not established). Cycles stays the default for scenes without a map or character shader.
"""


def scene_render_engine(character_shader=None, external_map=None, map_pending=False):
    """map_pending: a prepared map is composed onto this scene after the characters and effects are built."""
    with_map = external_map is not None or bool(map_pending)
    return 'BLENDER_EEVEE' if character_shader is not None or with_map else 'CYCLES'
