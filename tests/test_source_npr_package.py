import copy
import struct
from types import SimpleNamespace
import pytest
from agr.source_npr_package import build_npr_material_package,validate_npr_material_package,SHADER_RESOURCE,SHADER_SHA256
from agr.source_npr_assets import PARAMETERS
from agr.asset_converter import sha,ConversionError


def inputs(tmp_path):
    # Header fixture tests package identity/dimensions, not PNG pixel decoding.
    path=tmp_path/'header.png';path.write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',1,1)+bytes(9))
    textures={k:dict(resource=k+'.vtex_c',source_sha256='source',path=str(path)) for k in PARAMETERS}
    row=dict(material='a.vmat_c',source_sha256='source',textures=textures,authored={})
    plan=dict(status='sources_resolved_not_decoded',materials=[row])
    decoded=copy.deepcopy(plan);decoded['status']='decoded_requires_pixel_validation'
    for t in decoded['materials'][0]['textures'].values():t.update(sha256=sha(path),size=[1,1])
    provider=SimpleNamespace(resolve=lambda r:SimpleNamespace(sha256=SHADER_SHA256 if r==SHADER_RESOURCE else 'source'))
    return plan,decoded,provider,path


def test_package_requires_explicit_partial_and_preserves_identity(tmp_path):
    plan,decoded,provider,path=inputs(tmp_path)
    with pytest.raises(ConversionError):build_npr_material_package(plan,decoded,provider)
    package=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    assert len(validate_npr_material_package(package))==1
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ConversionError,match='payload'):validate_npr_material_package(package)


def test_source_mismatch_and_metadata_change_rejected(tmp_path):
    plan,decoded,provider,path=inputs(tmp_path)
    package=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    package['materials'][0]['source_sha256']='changed'
    with pytest.raises(ConversionError,match='metadata'):validate_npr_material_package(package)
    decoded['materials'][0]['textures'][PARAMETERS[0]]['resource']='wrong.vtex_c'
    with pytest.raises(ConversionError,match='source mismatch'):build_npr_material_package(plan,decoded,provider,allow_partial=True)


def test_spatial_mask_keeps_dimensions_and_legacy_constant_contract(tmp_path):
    from agr.source_npr_package import LEGACY_BINDINGS
    from agr.source_hero_lighting_package import canonical_sha
    plan,decoded,provider,path=inputs(tmp_path)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',4,2)+bytes(9))
    for texture in decoded['materials'][0]['textures'].values():
        texture.update(sha256=sha(path),size=[4,2])
    package=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    assert package['schema_version']==2
    assert validate_npr_material_package(package)['a.vmat_c']['textures'][PARAMETERS[1]]['size']==[4,2]
    legacy=copy.deepcopy(package);legacy['schema_version']=1;legacy['bindings']=copy.deepcopy(LEGACY_BINDINGS)
    legacy.pop('content_sha256');legacy['content_sha256']=canonical_sha(legacy)
    with pytest.raises(ConversionError,match='Variable rim mask'):
        validate_npr_material_package(legacy)


def test_schema3_mixed_contract_keeps_generic_empty_and_cannot_drop_runtime_status(tmp_path):
    from agr.source_material_routes import source_material_contract
    from agr.source_hero_lighting_package import canonical_sha
    plan,decoded,provider,_=inputs(tmp_path)
    plan['schema_version']=3
    npr=plan['materials'][0]
    npr['authored']=dict(m_shaderName='pbr.vfx',m_intParams=[dict(m_name='F_USE_NPR_LIGHTING',m_nValue=1),dict(m_name='F_NO_SPECULAR_AT_FULL_ROUGHNESS',m_nValue=1)])
    npr.update(source_material_contract(npr['authored']))
    generic=dict(material='glow.vmat_c',source_sha256='source',textures={},
                 authored=dict(m_shaderName='pbr.vfx',m_dynamicParams=[dict(m_name='emission',m_value=[0])]))
    generic.update(source_material_contract(generic['authored']))
    plan['materials'].append(generic)
    decoded['materials'].append(dict(material='glow.vmat_c',source_sha256='source',textures={}))
    package=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    rows=validate_npr_material_package(package)
    assert package['schema_version']==3 and rows['glow.vmat_c']['textures']=={}
    assert rows['a.vmat_c']['no_specular_at_full_roughness'] is True
    package['materials'][1]['runtime']['status']='static_authored_inputs'
    package.pop('content_sha256');package['content_sha256']=canonical_sha(package)
    with pytest.raises(ConversionError,match='route/runtime contract'):
        validate_npr_material_package(package)


def constant_inputs(tmp_path,monkeypatch):
    from agr import material_expressions as expressions
    from agr.source_material_routes import source_material_contract
    from test_material_expressions import vector
    plan,decoded,provider,_=inputs(tmp_path)
    plan['schema_version']=3
    row=plan['materials'][0]
    row['authored']=dict(m_shaderName='pbr.vfx',m_intParams=[dict(m_name='F_USE_NPR_LIGHTING',m_nValue=1)],
        m_dynamicParams=[dict(m_name='g_vColorTint1',m_value=list(vector(1,1,1)))])
    row.update(source_material_contract(row['authored'],resolve_constants=True))
    identity=dict(resource=expressions.MODULE_RESOURCE,sha256=expressions.MODULE_SHA256,semantics_sha256=expressions.MODULE_SHA256)
    row['expression_module']=identity.copy()
    monkeypatch.setattr(expressions,'verify_expression_module',lambda p:identity.copy())
    return plan,decoded,provider,identity


def test_constant_package_keeps_actual_module_provenance_and_reads_legacy_package(tmp_path,monkeypatch):
    from agr.source_hero_lighting_package import canonical_sha
    plan,decoded,provider,identity=constant_inputs(tmp_path,monkeypatch)
    package=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    assert validate_npr_material_package(package)['a.vmat_c']['expression_module']==identity
    package['materials'][0].pop('expression_module')
    package.pop('content_sha256');package['content_sha256']=canonical_sha(package)
    assert validate_npr_material_package(package)['a.vmat_c']['expression_evaluation']['vector_overrides']


def test_constant_module_change_after_planning_rejected_even_if_both_are_qualified(tmp_path,monkeypatch):
    plan,decoded,provider,identity=constant_inputs(tmp_path,monkeypatch)
    identity['sha256']='26a029e4e33db12801ebab80ea2ad65536e6d1e19889c810edc59637edd78e74'
    with pytest.raises(ConversionError,match='changed since planning'):
        build_npr_material_package(plan,decoded,provider,allow_partial=True)


def test_rehashed_package_cannot_claim_unknown_evaluator_identity(tmp_path,monkeypatch):
    from agr.source_hero_lighting_package import canonical_sha
    plan,decoded,provider,_=constant_inputs(tmp_path,monkeypatch)
    package=build_npr_material_package(plan,decoded,provider,allow_partial=True)
    package['materials'][0]['expression_module']['sha256']='f'*64
    package.pop('content_sha256');package['content_sha256']=canonical_sha(package)
    with pytest.raises(ConversionError,match='provenance'):validate_npr_material_package(package)
