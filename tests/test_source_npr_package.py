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
