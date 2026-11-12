from copy import deepcopy
from types import SimpleNamespace
import pytest

from agr.installed_effects import UnsupportedEffect
from agr import particle_texture_control_defaults as defaults
from agr.particle_texture_layers import needs_extended_sprite_layers,gradient_pixels


def definition(controls=None,*,blend=None):
    second=dict(m_nTextureType='SPRITECARD_TEXTURE_UVDISTORTION')
    if controls is not None:second['m_TextureControls']=controls
    if blend is not None:second['m_nTextureBlendMode']=blend
    return {'effects/test.vpcf':{'m_Renderers':[dict(_class='C_OP_RenderSprites',
        m_vecTexturesInput=[{},second])]}}


def test_released_replacement_and_multiplied_graphs_stay_on_the_legacy_path():
    d=definition(blend='SPRITECARD_TEXTURE_BLEND_REPLACE')
    assert defaults.required_defaults(d)==set()
    assert defaults.prepare_defaults(d,object()) is None
    assert not needs_extended_sprite_layers([{},{}])
    assert not needs_extended_sprite_layers(d['effects/test.vpcf']['m_Renderers'][0]['m_vecTexturesInput'])
    assert defaults.required_defaults(definition())=={'m_flDistortion'}


def test_explicit_zero_is_preserved_and_does_not_inherit_one():
    assert not defaults.required_defaults(definition({'m_flDistortion':0.}))
    assert not defaults.required_defaults(definition({'m_flDistortion':{'m_flLiteralValue':0.}}))
    assert defaults.required_defaults(definition({'m_flDistortion':{}}))=={'m_flDistortion'}
    with pytest.raises(UnsupportedEffect,match='Null'):
        defaults.required_defaults(definition({'m_flDistortion':None}))


def test_constructor_provenance_is_required_and_cannot_be_replaced_by_an_unknown_module(tmp_path,monkeypatch):
    provider=SimpleNamespace(install_path=tmp_path)
    module=tmp_path/'game/bin/win64/particles.dll';module.parent.mkdir(parents=True);module.write_bytes(b'fixture')
    with pytest.raises(UnsupportedEffect,match='qualified particle module'):
        defaults.prepare_defaults(definition(),provider)
    measured=next(iter(defaults.QUALIFIED_PARTICLES))
    monkeypatch.setattr(defaults,'sha',lambda _:measured)
    row=defaults.prepare_defaults(definition(),provider)
    defaults.validate_defaults(definition(),{'texture_control_defaults':row})
    changed=deepcopy(row);changed['values']['m_flDistortion']=True
    with pytest.raises(UnsupportedEffect):
        defaults.validate_defaults(definition(),{'texture_control_defaults':changed})
    row['values']['m_flDistortion']=0
    assert defaults.DEFAULTS['m_flDistortion']==1


@pytest.mark.parametrize('change',[
    {'particles_sha256':'0'*64},{'schema_version':True},{'policy':'unknown'},
    {'values':{}},{'values':{'m_flZoomScale':1.,'m_flDistortion':0.}}
])
def test_changed_defaults_header_refuses(change):
    row=dict(schema_version=1,policy=defaults.POLICY,particles_sha256=next(iter(defaults.QUALIFIED_PARTICLES)),
        values=dict(defaults.DEFAULTS));row.update(change)
    with pytest.raises(UnsupportedEffect):
        defaults.validate_defaults(definition(),{'texture_control_defaults':row})
    with pytest.raises(UnsupportedEffect):defaults.validate_defaults(definition(),{})


def test_generated_gradient_is_bounded_and_never_overflows_float32():
    with pytest.raises(UnsupportedEffect):gradient_pixels({'m_Stops':[{}]*257})
    with pytest.raises(UnsupportedEffect):gradient_pixels({'m_Stops':[{'m_flPosition':1e300}]})


@pytest.mark.parametrize('layers',[[],[{}],[{'m_hTexture':'materials/explicit.vtex'},{}]])
def test_omitted_enabled_texture_never_turns_into_a_generic_flash(layers):
    from agr.blender_simulated_effects import validate_renderer_classes
    assets={'definitions':{'effects/test.vpcf':{'m_Renderers':[dict(
        _class='C_OP_RenderSprites',m_vecTexturesInput=layers)]}}}
    with pytest.raises(UnsupportedEffect,match='native fallback'):
        validate_renderer_classes({('instance','root','effects/test.vpcf',0):[]},assets)
