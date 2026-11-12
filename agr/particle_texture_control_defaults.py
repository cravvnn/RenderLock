"""Measured TextureControls constructor defaults for extended layer composition.

Only selected constructor argument/literal writes are qualified, not full native
construction, GPU binding, filtering, or game image equivalence.
"""
from pathlib import Path
from .asset_converter import sha
from .installed_effects import UnsupportedEffect
from .particle_texture_layers import enabled_texture_layers,needs_extended_sprite_layers

POLICY='agr_texture_controls_constructor_defaults_v1'
QUALIFIED_PARTICLES={
    '5d5e29757162d86a4bb940c0f0b0189fbf12598d4238a7d27ec4082e9740ff7b',
    '46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f',
}
DEFAULTS={'m_flZoomScale':1.,'m_flDistortion':1.}


def required_defaults(definitions):
    needed=set()
    for definition in definitions.values():
        for renderer in definition.get('m_Renderers',[]):
            if renderer.get('_class') not in ('C_OP_RenderSprites','C_OP_RenderTrails'):
                continue
            layers=renderer.get('m_vecTexturesInput',[])
            if not needs_extended_sprite_layers(layers):continue
            for _,layer in enabled_texture_layers(layers):
                kind=layer.get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')
                controls=layer.get('m_TextureControls',{})
                if not isinstance(controls,dict):raise UnsupportedEffect('Invalid extended texture controls')
                fields=[]
                if kind in ('SPRITECARD_TEXTURE_UVDISTORTION','SPRITECARD_TEXTURE_UVDISTORTION_ZOOM'):
                    fields.append('m_flDistortion')
                if kind in ('SPRITECARD_TEXTURE_ZOOM','SPRITECARD_TEXTURE_UVDISTORTION_ZOOM'):
                    fields.append('m_flZoomScale')
                for field in fields:
                    if field not in controls:
                        needed.add(field)
                    elif controls[field] is None:
                        raise UnsupportedEffect('Null authored texture controls cannot inherit a native default')
                    elif (isinstance(controls[field],dict)
                            and controls[field].get('m_nType','PF_TYPE_LITERAL')=='PF_TYPE_LITERAL'
                            and 'm_flLiteralValue' not in controls[field]):
                        needed.add(field)
    return needed


def prepare_defaults(definitions,provider):
    needed=required_defaults(definitions)
    if not needed:return None
    module=Path(provider.install_path)/'game/bin/win64/particles.dll'
    digest=sha(module) if module.is_file() else None
    if digest not in QUALIFIED_PARTICLES:
        raise UnsupportedEffect('Extended particle texture defaults require a qualified particle module')
    return dict(schema_version=1,policy=POLICY,particles_sha256=digest,values=dict(DEFAULTS))


def validate_defaults(definitions,provenance):
    if not required_defaults(definitions):return
    row=provenance.get('texture_control_defaults')
    values=row.get('values') if isinstance(row,dict) else None
    if (not isinstance(row,dict) or type(row.get('schema_version')) is not int or row['schema_version']!=1
            or row.get('policy')!=POLICY or row.get('particles_sha256') not in QUALIFIED_PARTICLES
            or values!=DEFAULTS or not isinstance(values,dict)
            or any(type(value) not in (int,float) for value in values.values())):
        raise UnsupportedEffect('Extended texture composition lacks qualified constructor-default provenance')
