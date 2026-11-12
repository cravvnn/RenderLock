import copy
from types import SimpleNamespace
import pytest
from agr.installed_effects import UnsupportedEffect
from agr.particle_sheet_uv import (POLICY,SHADERS,validate_shader_inputs,collect_shader_inputs,sheet_random_offset_bypass)


def fixture():
    return (dict(_class='C_OP_RenderSprites'),
            dict(schema_version=1,is_sprite_sheet=True,atlas=True,sequences={'0':dict(status='supported',frames=[{'image':'frame.png'}])}),
            dict(spritecard_sheet_uv=dict(policy=POLICY,sha256=copy.deepcopy(SHADERS))))


def test_only_qualified_sheet_sprite_skips_the_ordinary_random_offset_term():
    renderer,texture,provenance=fixture();before=copy.deepcopy((renderer,texture,provenance))
    assert sheet_random_offset_bypass(renderer,texture,provenance)
    assert (renderer,texture,provenance)==before
    assert not sheet_random_offset_bypass(renderer,texture,{})
    assert not sheet_random_offset_bypass(dict(_class='C_OP_RenderTrails'),texture,provenance)
    assert not sheet_random_offset_bypass(renderer,dict(texture,is_sprite_sheet=False),provenance)


@pytest.mark.parametrize('kind',['policy','vertex','pixel','features','extra','missing','sparse','empty','unsupported','not_atlas','schema_bool','wrong_frame_type'])
def test_changed_shader_or_sheet_metadata_cannot_suppress_a_random_input(kind):
    renderer,texture,provenance=fixture();proof=provenance['spritecard_sheet_uv']
    if kind=='policy':proof['policy']='unknown'
    if kind in ('vertex','pixel','features'):
        index={'features':0,'vertex':1,'pixel':2}[kind];proof['sha256'][list(SHADERS)[index]]='f'*64
    if kind=='extra':proof['extra']=True
    if kind=='missing':proof.pop('sha256')
    if kind=='sparse':texture['sequences']={'4':texture['sequences']['0']}
    if kind=='empty':texture['sequences']['0']['frames']=[]
    if kind=='unsupported':texture['sequences']['0']['status']='partial'
    if kind=='not_atlas':texture['atlas']=False
    if kind=='schema_bool':texture['schema_version']=True
    if kind=='wrong_frame_type':texture['sequences']['0']['frames']={0:{}}
    with pytest.raises(UnsupportedEffect):sheet_random_offset_bypass(renderer,texture,provenance)


def test_collection_uses_exact_provider_identities_and_refuses_changed_code():
    class Provider:
        def __init__(self):self.calls=[]
        def resolve(self,name):self.calls.append(name);return SimpleNamespace(sha256=SHADERS[name])
    provider=Provider();assert collect_shader_inputs(provider)==dict(policy=POLICY,sha256=SHADERS)
    assert provider.calls==list(SHADERS)
    with pytest.raises(UnsupportedEffect):validate_shader_inputs(dict(policy=POLICY,sha256={}))


@pytest.mark.parametrize('backend',['simulated_states_v1','simulated_projectile_flight_v2','simulated_world_origin_v1'])
def test_prepared_package_cannot_declare_a_changed_shader_profile(backend):
    from agr.simulated_effect_package import validate_package
    package=dict(schema_version=1,backend=backend,assets=dict(provenance=dict(spritecard_sheet_uv=dict(policy=POLICY,sha256={}))))
    with pytest.raises(UnsupportedEffect,match='shader inputs'):validate_package(package,{})


def test_declared_null_shader_proof_is_not_legacy_absence():
    renderer,texture,provenance=fixture();provenance['spritecard_sheet_uv']=None
    with pytest.raises(UnsupportedEffect):sheet_random_offset_bypass(renderer,texture,provenance)


def test_incomplete_original_sheet_is_refused_instead_of_guessed():
    renderer,texture,provenance=fixture();texture['source_sheet']={}
    with pytest.raises(UnsupportedEffect,match='Unqualified original'):sheet_random_offset_bypass(renderer,texture,provenance)


def test_original_sheet_random_offset_bypass_requires_exact_compiled_texture_identity():
    renderer,texture,provenance=fixture()
    texture.update(resource='materials/test.vtex_c',source_sheet=dict(schema_version=1,
        policy='agr_source_particle_sheet_v1',input_sha256='a'*64,sequences=[dict(index=0,
        clamp=True,total_time=1,frames=[dict(duration=1,min=[0,0],max=[1,1])])]))
    provenance['input_sha256']={'materials/test.vtex':'a'*64}
    assert sheet_random_offset_bypass(renderer,texture,provenance)
    texture['source_sheet']['input_sha256']='b'*64
    with pytest.raises(UnsupportedEffect,match='source identity'):sheet_random_offset_bypass(renderer,texture,provenance)


@pytest.mark.parametrize('sprite,sheet,randomize,qualified',[
    (True,True,True,True),(True,False,True,False),(False,True,True,False),
    (True,True,False,False)])
def test_asset_preparation_qualifies_only_applicable_shader_branch(tmp_path,sprite,sheet,randomize,qualified):
    from agr.particle_render_assets import prepare_render_assets
    class Provider:
        def __init__(self):self.calls=[]
        def resolve(self,name):
            self.calls.append(name);return SimpleNamespace(sha256=SHADERS.get(name,'a'*64))
    provider=Provider()
    definition=dict(m_Renderers=[dict(_class='C_OP_RenderSprites'if sprite else 'C_OP_RenderTrails',
        m_vecTexturesInput=[dict(m_hTexture='materials/test.vtex',m_TextureControls=dict(m_bRandomizeOffsets=randomize))])])
    decoder=SimpleNamespace(identity='test',dump=lambda *a:definition)
    adapter=SimpleNamespace(texture=lambda *a:(tmp_path,dict(schema_version=1,is_sprite_sheet=sheet,atlas=sheet,
        sequences={'0':dict(status='supported',frames=[dict(image='frame.png')])})))
    assets=prepare_render_assets({'particles/test.vpcf'},decoder,adapter,provider,{})
    assert ('spritecard_sheet_uv'in assets['provenance'])is qualified
    assert all((name in provider.calls)is qualified for name in SHADERS)
