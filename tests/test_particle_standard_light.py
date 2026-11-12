import math
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_standard_light import parameters,profile,LOOKUP_ABSOLUTE_ERROR
from agr.blender_simulated_effects import validate_renderer_classes
from agr.particle_standard_light import PROFILE_KEY,PROFILE_POLICY,PARTICLES_SHA,validate_native_inputs


def particle(**values):
    return dict(color=[.8941177129745483,.8352941870689392,.6980392336845398],
                alpha=1.,alpha_alternate=1.,radius=20.,**values)


def test_recorded_point_color_with_declared_lookup_error_budget():
    renderer=dict(_class='C_OP_RenderStandardLight',m_flIntensity=3.)
    result=parameters(renderer,particle(),.0254)
    native=[23274.673828125,19961.625,13356.037109375]
    error_bound=30000*LOOKUP_ABSOLUTE_ERROR+.004
    assert max(abs(a-b)for a,b in zip(result['source_rgb'],native))<=error_bound
    assert result['source_range']==160.
    assert result['falloff_offset']==pytest.approx(1/160**2,rel=1e-7)
    assert not result['casts_shadows']


def test_radius_changes_range_without_inventing_extra_intensity():
    renderer=dict(_class='C_OP_RenderStandardLight')
    a=parameters(renderer,particle(),.0254)
    p=particle();p['radius']=40.
    b=parameters(renderer,p,.0254)
    assert a['energy']==b['energy']
    assert b['range']==2*a['range']
    p['alpha_alternate']=0.
    assert parameters(renderer,p,.0254)['energy']==b['energy']


def test_fx_uses_native_linear_normalization_and_forced_no_shadow():
    renderer=dict(_class='C_OP_RenderStandardLight',m_bCastShadows=True)
    a=parameters(renderer,particle(),.0254)
    b=parameters(dict(renderer,m_nLightType='PARTICLE_LIGHT_TYPE_FX'),particle(),.0254)
    assert a['attenuation']=='quadratic'and a['casts_shadows']
    assert b['attenuation']=='linear'and not b['casts_shadows']
    assert a['energy']/b['energy']==pytest.approx(100.,rel=2e-7)
    assert b['falloff_offset']==pytest.approx(1/160,rel=1e-7)


def test_units_preserve_source_range_and_scale_blender_energy_by_area():
    a=parameters(dict(_class='C_OP_RenderStandardLight'),particle(),.01)
    b=parameters(dict(_class='C_OP_RenderStandardLight'),particle(),.02)
    assert a['source_range']==b['source_range']
    assert b['energy']==4*a['energy']and b['range']==2*a['range']


def test_zero_alpha_is_finite_and_zero_radius_selects_native_automatic_range():
    p=particle();p['alpha']=0.
    a=parameters(dict(_class='C_OP_RenderStandardLight'),p,.0254)
    assert a['energy']==a['range']==a['falloff_offset']==0.
    assert all(math.isfinite(v)for v in a['color'])
    p=particle();p['radius']=0.
    assert parameters(dict(_class='C_OP_RenderStandardLight'),p,.0254)['source_range']>160


@pytest.mark.parametrize('override',[dict(m_nLightType='PARTICLE_LIGHT_TYPE_SPOT'),dict(m_nLightType=True),
    dict(m_nAttenuationStyle=1),dict(m_flFalloffLinearity=.5),dict(m_vecColorScale={'m_nType':'PVEC_TYPE_CONTROL_POINT_VALUE'}),
    dict(m_bRenderDiffuse=False),dict(m_bDynamicBounce=True),dict(m_unknown=True)])
def test_unknown_profiles_refuse_through_normal_preallocation_boundary(override):
    renderer=dict(_class='C_OP_RenderStandardLight',**override)
    with pytest.raises(UnsupportedEffect):profile(renderer)
    with pytest.raises(UnsupportedEffect):
        validate_renderer_classes({('root','path','light.vpcf',0):[]},dict(definitions={'light.vpcf':dict(m_Renderers=[renderer])},
            provenance={PROFILE_KEY:dict(policy=PROFILE_POLICY,particles_sha256=PARTICLES_SHA)}))


@pytest.mark.parametrize('key,value',[('alpha',float('nan')),('alpha',-1),('radius',-1),('alpha',True),('color',[2.,0.,0.])])
def test_invalid_sample_does_not_fall_back_to_calibrated_light(key,value):
    p=particle();p[key]=value
    with pytest.raises(UnsupportedEffect):parameters(dict(_class='C_OP_RenderStandardLight'),p,.0254)


@pytest.mark.parametrize('proof',[None,{},dict(policy=PROFILE_POLICY,particles_sha256='a'*64),
    dict(policy='unknown',particles_sha256=PARTICLES_SHA)])
def test_changed_declared_native_profile_is_refused_before_package_dispatch(proof):
    from agr.simulated_effect_package import validate_package
    with pytest.raises(UnsupportedEffect,match='native default'):
        validate_package(dict(assets=dict(provenance={PROFILE_KEY:proof})),{})
    with pytest.raises(UnsupportedEffect):validate_native_inputs(proof)


@pytest.mark.parametrize('key',['m_flFalloffLinearity','m_vecColorScale','m_flIntensity','m_flRadiusMultiplier'])
def test_declared_null_settings_are_not_absent_constructor_defaults(key):
    with pytest.raises(UnsupportedEffect):profile(dict(_class='C_OP_RenderStandardLight',**{key:None}))


def test_legacy_unqualified_prepared_assets_retain_their_existing_preflight():
    # Old packets have no native-profile claim. New asset preparation always
    # qualifies the module; absence here preserves legacy rendering behavior.
    renderer=dict(_class='C_OP_RenderStandardLight',m_nLightType='PARTICLE_LIGHT_TYPE_SPOT')
    validate_renderer_classes({('root','path','light.vpcf',0):[]},dict(definitions={'light.vpcf':dict(m_Renderers=[renderer])}))


def test_new_asset_preparation_requires_the_actual_qualified_module(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import agr.asset_converter as converter
    from agr.particle_standard_light import collect_native_inputs
    provider=SimpleNamespace(install_path=tmp_path)
    with pytest.raises(UnsupportedEffect,match='unavailable'):collect_native_inputs(provider)
    module=tmp_path/'game/bin/win64/particles.dll';module.parent.mkdir(parents=True);module.write_bytes(b'changed module')
    with pytest.raises(UnsupportedEffect,match='unqualified'):collect_native_inputs(provider)
    seen=[]
    def qualified(path):seen.append(path);return PARTICLES_SHA
    monkeypatch.setattr(converter,'sha',qualified)
    assert collect_native_inputs(provider)==dict(policy=PROFILE_POLICY,particles_sha256=PARTICLES_SHA)
    assert seen==[module]


def test_normal_asset_preparation_advertises_only_the_applicable_light_profile(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import agr.particle_standard_light as light
    from agr.particle_render_assets import prepare_render_assets
    proof=dict(policy=PROFILE_POLICY,particles_sha256=PARTICLES_SHA);calls=[]
    def collect(provider):calls.append(provider);return proof.copy()
    monkeypatch.setattr(light,'collect_native_inputs',collect)
    provider=SimpleNamespace(resolve=lambda *args:SimpleNamespace(sha256='a'*64))
    decoder=SimpleNamespace(identity={},dump=lambda *args:dict(m_Renderers=[dict(_class='C_OP_RenderStandardLight')]))
    assets=prepare_render_assets({'particles/test.vpcf'},decoder,None,provider,{})
    assert assets['provenance'][PROFILE_KEY]==proof and calls==[provider]
    decoder.dump=lambda *args:dict(m_Renderers=[]);calls.clear()
    assets=prepare_render_assets({'particles/test.vpcf'},decoder,None,provider,{})
    assert PROFILE_KEY not in assets['provenance'] and not calls
