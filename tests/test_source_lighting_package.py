import json
import pytest

from agr.source_lightmap import SHADER_SHA256
from agr.source_lighting_package import file_sha, validate_lighting_package, build_lighting_package


def fixture(tmp_path):
    geometry=tmp_path/'region.gltf'
    geometry.write_text(json.dumps(dict(buffers=[dict(uri='region.bin')],images=[dict(uri='color.png')])))
    for name in ('region.bin','color.png','DC','R','G','B'):
        (tmp_path/name).write_bytes(name.encode())
    package=dict(schema_version=1,mode='static_diffuse',allow_partial=True,
        shader_sha256=SHADER_SHA256,world_sha256='a'*64,gltf_sha256=file_sha(geometry),
        map_files={n:file_sha(tmp_path/n) for n in ('region.bin','color.png')},
        lightmaps={n:dict(path=n,sha256=file_sha(tmp_path/n),size=[8192,8192],layer=0) for n in ('DC','R','G','B')})
    return package,dict(source_world_sha256='a'*64),geometry


def test_valid_package_resolves_all_images(tmp_path):
    p,d,g=fixture(tmp_path)
    assert validate_lighting_package(p,d,g,tmp_path)=={n:tmp_path/n for n in ('DC','R','G','B')}


@pytest.mark.parametrize('field,value', [('allow_partial',False),('allow_partial',1),
    ('mode','complete'),('world_sha256','b'*64),('shader_sha256','b'*64),('gltf_sha256','b'*64),('schema_version',True)])
def test_unqualified_or_implicit_selection_rejected(tmp_path,field,value):
    p,d,g=fixture(tmp_path);p[field]=value
    with pytest.raises(ValueError):validate_lighting_package(p,d,g,tmp_path)


@pytest.mark.parametrize('filename', ['region.bin','color.png','DC','R','G','B'])
def test_changed_payload_rejected(tmp_path,filename):
    p,d,g=fixture(tmp_path);(tmp_path/filename).write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):validate_lighting_package(p,d,g,tmp_path)


def test_missing_texture_or_inventory_rejected(tmp_path):
    p,d,g=fixture(tmp_path);del p['lightmaps']['B']
    with pytest.raises(ValueError,match='four'):validate_lighting_package(p,d,g,tmp_path)
    p,d,g=fixture(tmp_path);del p['map_files']['color.png']
    with pytest.raises(ValueError,match='inventory'):validate_lighting_package(p,d,g,tmp_path)


def test_unqualified_layer_rejected(tmp_path):
    p,d,g=fixture(tmp_path);p['lightmaps']['G']['layer']=1
    with pytest.raises(ValueError,match='size/layer'):validate_lighting_package(p,d,g,tmp_path)


def test_automatic_binding_requires_exact_world_and_payloads(tmp_path):
    p,d,g=fixture(tmp_path)
    d.update(model_file=str(g),world_resource='maps/test/world.vwrld_c')
    assets=dict(world_resource=d['world_resource'],world_sha256=d['source_world_sha256'],
                shader_sha256=p['shader_sha256'],lightmaps=p['lightmaps'])
    with pytest.raises(ValueError,match='explicit'):build_lighting_package(d,assets,tmp_path)
    assert build_lighting_package(d,assets,tmp_path,allow_partial=True)==p
    assets['world_sha256']='b'*64
    with pytest.raises(ValueError,match='identity'):build_lighting_package(d,assets,tmp_path,allow_partial=True)
    assets['world_sha256']=d['source_world_sha256'];assets['world_resource']='other.vwrld_c'
    with pytest.raises(ValueError,match='resource'):build_lighting_package(d,assets,tmp_path,allow_partial=True)
