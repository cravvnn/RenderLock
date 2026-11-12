"""Current typed rope seed/blend and spritecard shader provenance.

Prepared native slices qualify seed zero across all eight rope writers and the
blend parameter table. Installed shader identities bind the retained UV/depth/
PS equations. Source GPU blend-state/framebuffer parity and lighting remain
separate; the Blender material is an explicit premultiplied approximation.
No new mutable capture, cache, RNG or event lifecycle is introduced.
"""
from pathlib import Path
from .asset_converter import sha
from .installed_effects import UnsupportedEffect
from .particle_sheet_uv import SHADERS
from .particle_render_controls import render_scalar
from .particle_texture_layers import enabled_texture_layers

POLICY='agr_rope_zero_seed_shader_inputs_v1'
PROFILE_KEY='native_rope_shader'
PARTICLES_SHA='46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f'


def needs_inputs(renderer):
    depth=renderer.get('m_flDepthBias',0)
    try:depth=render_scalar(depth,default=0)
    except ValueError:return True
    return depth!=0 or renderer.get('m_nOutputBlendMode')=='PARTICLE_OUTPUT_BLEND_MODE_BLEND_ADD' or any(
        layer.get('m_TextureControls',{}).get('m_bRandomizeOffsets')
        or layer.get('m_TextureControls',{}).get('m_bClampUVs')
        or layer.get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')!='SPRITECARD_TEXTURE_COLOR'
        or layer.get('m_nTextureBlendMode','SPRITECARD_TEXTURE_BLEND_MULTIPLY')!='SPRITECARD_TEXTURE_BLEND_MULTIPLY'
        for _,layer in enabled_texture_layers(renderer.get('m_vecTexturesInput',[])))


def validate_inputs(proof):
    expected=dict(policy=POLICY,particles_sha256=PARTICLES_SHA,shader_sha256=SHADERS,segment_seed=0,
        blend_alpha_parameters={'ALPHA':1.,'ADD':0.,'BLEND_ADD':-1.})
    if (not isinstance(proof,dict) or proof!=expected or type(proof.get('segment_seed'))is not int
        or any(type(v)is not float for v in proof.get('blend_alpha_parameters',{}).values())):
        raise UnsupportedEffect('Unqualified rope native/shader provenance')
    return proof


def collect_inputs(provider):
    module=Path(provider.install_path)/'game/bin/win64/particles.dll'
    if not module.is_file() or sha(module)!=PARTICLES_SHA:
        raise UnsupportedEffect('Rope inputs require the qualified installed particle module')
    return validate_inputs(dict(policy=POLICY,particles_sha256=PARTICLES_SHA,
        shader_sha256={name:provider.resolve(name).sha256 for name in SHADERS},segment_seed=0,
        blend_alpha_parameters={'ALPHA':1.,'ADD':0.,'BLEND_ADD':-1.}))
