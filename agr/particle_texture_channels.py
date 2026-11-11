"""Bounded texture channel inputs used by the qualified bat-impact tree.

Matches RenderLock's existing shader-layer arithmetic, without importing its
broader research renderer. Unknown channel selectors remain explicit failures.
"""
from .installed_effects import UnsupportedEffect

RGBA='SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA'
RGB_ALPHA='SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA_RGBALPHA'
LUMA=(.299,.587,.114)

def texture_channel_mode(layer):
 mode=layer.get('m_nTextureChannels',RGBA)
 if mode not in(RGBA,RGB_ALPHA):
  raise UnsupportedEffect('Unsupported simulated texture channel selector '+str(mode))
 return mode

def texture_alpha(mode,rgba):
 """Numerical reference for the same alpha selection as the Blender nodes."""
 if mode==RGBA:return rgba[3]
 if mode==RGB_ALPHA:return sum(a*b for a,b in zip(rgba[:3],LUMA))
 raise UnsupportedEffect('Unsupported simulated texture channel selector '+str(mode))
