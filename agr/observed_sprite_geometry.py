"""Ambient manual-frame guard over the existing shared captured geometry.

No tracks, clocks, cache, file writer or playback handler is introduced.
"""
from .observed_slide_geometry_base import (
    F, CORNERS, FACES, require, literal, random_offsets, texture_corners,
    atlas_corners, layer_uvs, _build_geometry,
)


def build_frame(row, renderer, textures, camera):
    """One complete native array -> camera-bound mesh inputs; no track joins."""
    require(renderer.get('_class') == 'C_OP_RenderSprites'
            and renderer.get('m_nAnimationType') == 'ANIMATION_TYPE_MANUAL_FRAMES'
            and renderer.get('m_nOrientationType', 'PARTICLE_ORIENTATION_SCREEN_ALIGNED') == 'PARTICLE_ORIENTATION_SCREEN_ALIGNED',
            'renderer profile differs')
    require(renderer.get('m_bBlendFramesSeq0', True) is True
            and not renderer.get('m_bMaxLuminanceBlendingSequence0'), 'sheet blend mode differs')
    return _build_geometry(row, renderer, textures, camera)
