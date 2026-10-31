import copy
import hashlib
import json

import pytest

from agr import map_package as packages


@pytest.fixture
def prepared(tmp_path):
    folder=tmp_path/'map';(folder/'textures').mkdir(parents=True)
    rows=[]
    for name,data in [('RenderLockMap.blend',b'fixture library'),('textures/wall.png',b'fixture pixels')]:
        (folder/name).write_bytes(data)
        rows.append(dict(file=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    doc=dict(format=packages.FORMAT,schema_version=2,package_id='midtown-fixture-v2',
        builder=dict(commit=packages.BUILDER_COMMIT,map_builder=14),
        source=dict(kind='installed_game',game_version='6839',native_blend_sha256='a'*64,
            build_request_sha256='d'*64,map_archive_sha256='e'*64,root_archive_sha256='f'*64,
            source2viewer_sha256=next(iter(packages.CONVERTER_SHA256)),
            adapter_sha256={name:'1'*64 for name in
                ('blender_prepared_map.py','blender_prepared_proxy.py','map_package.py')}),
        map_name='dl_midtown',blender_min=[5,2,0],source_frame='vrf_blender_metres',
        collection=packages.COLLECTION,world='Midtown sky',namespace=packages.NAMESPACE,
        inventory=dict(objects=20,meshes=10,vertices=100,faces=50,file_images=1,geometry_sha256='b'*64),
        proxy=dict(status='ready',algorithm='grid-0.25m-v2',collection='RenderLockProxy',
            source_meshes=8,source_vertices=90,source_faces=40,proxy_meshes=2,proxy_vertices=20,proxy_faces=10,
            full_map_hidden_in_viewport=True,proxy_hidden_in_render=True),
        library=rows[0],files=rows)
    (folder/packages.MANIFEST).write_text(json.dumps(doc))
    return folder,doc


def test_portable_local_package_verifies_every_file_and_ignores_caller_inventory(prepared):
    folder,doc=prepared
    identity=packages.library_identity(folder/packages.LIBRARY)
    identity['objects']=0
    assert packages.library_definition(identity)['objects']==20
    assert packages.load_package(folder)['document']['source']['kind']=='installed_game'
    moved=folder.with_name('relocated');folder.rename(moved)
    assert packages.library_identity(moved/packages.LIBRARY)['sha256']==doc['library']['sha256']


@pytest.mark.parametrize('change',['missing','changed','library','manifest'])
def test_broken_package_is_refused_before_blender(prepared,change):
    folder,doc=prepared
    if change=='missing':(folder/'textures/wall.png').unlink()
    elif change=='changed':(folder/'textures/wall.png').write_bytes(b'wrong file data')
    elif change=='library':(folder/packages.LIBRARY).write_bytes(b'edited library')
    else:(folder/packages.MANIFEST).write_text('{broken')
    with pytest.raises(ValueError,match='External map:'):
        packages.load_package(folder)


@pytest.mark.parametrize('path',['../wall.png','/wall.png','textures/../../wall.png',
                               'C:/wall.png','textures\\wall.png','textures//wall.png','textures/./wall.png'])
def test_resource_paths_cannot_escape_or_normalize_ambiguously(path):
    with pytest.raises(ValueError,match='External map:'):
        packages.relative_path(path)


@pytest.mark.parametrize('change',['builder','coordinates','render','counts','duplicate','library_inventory'])
def test_manifest_cannot_claim_unqualified_builder_visibility_or_inventory(prepared,change):
    _,original=prepared;doc=copy.deepcopy(original)
    if change=='builder':doc['builder']['commit']='unreviewed'
    elif change=='coordinates':doc['source_frame']='guessed'
    elif change=='render':doc['proxy']['proxy_hidden_in_render']=1
    elif change=='counts':doc['proxy']['proxy_faces']=doc['proxy']['source_faces']
    elif change=='duplicate':doc['files'].append(copy.deepcopy(doc['files'][1]))
    else:doc['library']['sha256']='c'*64;doc['files'][0]=copy.deepcopy(original['files'][0])
    with pytest.raises(ValueError,match='External map:'):
        packages.validate_document(doc)
