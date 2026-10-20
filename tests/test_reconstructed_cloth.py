from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from agr import installed_cloth, reconstructed_cloth
from agr.coverage_diagnostics import merge_secondary_motion_coverage, scene_coverage_warnings


class Converter:
    def __init__(self, root): self.root = root
    def _entry(self, operation, inputs, settings, build):
        output = self.root/'conversion'; output.mkdir()
        build(output)
        return output


def test_native_cloth_result_refuses_unsafe_filename_before_parsing(tmp_path, monkeypatch):
    probe = tmp_path/'probe.dll'; probe.write_bytes(b'fixture')
    provider = SimpleNamespace(install_path=tmp_path, resolve=lambda _:SimpleNamespace(sha256='a'*64))
    parsed=[]
    monkeypatch.setattr(reconstructed_cloth,'read_painted_mesh',lambda p:parsed.append(p))
    def native(command, **kwargs):
        out=Path(command[-1])
        report=dict(schema_version=1,resource='models/test.vmdl',animation_files=0,fe_node_count=3,
            files=[dict(path='../escape.dmx',bytes=1,sha256='x')])
        (out/'cloth-extraction.json').write_text(json.dumps(report))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(reconstructed_cloth.subprocess,'run',native)
    with pytest.raises(ValueError,match='integrity'):
        reconstructed_cloth.reconstruct('models/test.vmdl_c',provider,Converter(tmp_path),
            {'m_pFeModel':{'m_nNodeCount':3}},command=[str(probe)])
    assert parsed==[]


def test_missing_registered_proxy_is_reconstructed_once_and_bound_to_physics(tmp_path, monkeypatch):
    physics={'m_pFeModel':{'m_nNodeCount':3}}
    calls=[]
    monkeypatch.setattr(installed_cloth,'decode_physics',lambda *a:(physics,'a'*64))
    definition=dict(schema='agr_decompiled_fe_proxy_bundle_v1',physics=physics,
        source_sha256={'native':'verified'},scope='fixture approximation',
        parts=[dict(schema='agr_decompiled_fe_proxy_v1',physics=physics,mesh_suffix='.body')])
    def generate(*args,**kwargs):
        calls.append(1)
        return {'status':'prepared','definition':definition}
    monkeypatch.setattr(reconstructed_cloth,'reconstruct',generate)
    provider=SimpleNamespace(cache_dir=tmp_path/'cache')
    bindings=[{'resource':'models/test.vmdl_c','id':'8:1'}]
    def prepare(folder):
        return installed_cloth.prepare_cloth_assets({'entities':[{'id':'8:1','hero_name':'McGinnis'}]},
            bindings,provider,None,tmp_path/folder,log=lambda _:None)
    first=prepare('first'); second=prepare('second')
    assert calls==[1]
    assert first['status']==second['status']=='prepared'
    assert first['supported_entities'][0]['status']=='ready_to_bake'
    assert first.get('baked_entity_count',0)==0


def test_model_without_fe_nodes_is_not_an_unsupported_cloth_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(installed_cloth,'decode_physics',lambda *a:({},'b'*64))
    monkeypatch.setattr(reconstructed_cloth,'reconstruct',lambda *a,**k:
        {'status':'not_applicable','reason':'Installed model has no FE cloth nodes'})
    report=installed_cloth.prepare_cloth_assets({'entities':[{'id':'15:1','hero_name':'Bebop'}]},
        [{'id':'15:1','resource':'models/test.vmdl_c'}],SimpleNamespace(cache_dir=tmp_path/'cache'),
        None,tmp_path/'pack',log=lambda _:None)
    assert report['status']=='not_applicable'
    assert report['unsupported_entities']==[]
    assert len(report['not_applicable_entities'])==1
    merged=merge_secondary_motion_coverage(report,[])
    assert merged['status']=='not_applicable' and merged['baked_entity_count']==0
    assert scene_coverage_warnings({'secondary_motion':merged})==[]


def mixed_proxy_modeldoc():
    return {'children': [
        {'_class': 'ClothProxyMeshFile', 'filename': 'models/test/cable.dmx',
         'back_solve_joints': True, 'back_solve_joints_drive_meshes': True,
         'add_bones_to_render_mesh': False},
        {'_class': 'ClothVertexMap', 'name': 'pants_ClothVertexMap', 'children': [
            {'_class': 'ClothProxyMeshFile', 'filename': 'models/test/pants.dmx',
             'back_solve_joints': False, 'back_solve_joints_drive_meshes': False,
             'add_bones_to_render_mesh': True}]}]}


@pytest.mark.parametrize('files', [['cable.dmx', 'pants.dmx'], ['pants.dmx', 'cable.dmx']])
def test_generated_surface_uses_authored_route_instead_of_first_proxy(files):
    selected, pending = reconstructed_cloth.select_surface_proxy(mixed_proxy_modeldoc(), files)
    assert selected == ('pants.dmx', 'pants_ClothVertexMap')
    assert len(pending) == 1 and pending[0].startswith('cable.dmx:')


def test_proxy_deformation_route_and_inventory_must_be_qualified():
    modeldoc = mixed_proxy_modeldoc()
    with pytest.raises(ValueError, match='inventory'):
        reconstructed_cloth.select_surface_proxy(modeldoc, ['pants.dmx'])
    modeldoc['children'][0]['add_bones_to_render_mesh'] = True
    with pytest.raises(ValueError, match='deformation route'):
        reconstructed_cloth.select_surface_proxy(modeldoc, ['cable.dmx', 'pants.dmx'])


def test_multiple_generated_surfaces_need_their_own_mapping():
    modeldoc = mixed_proxy_modeldoc()
    modeldoc['children'][1]['children'].append(dict(
        modeldoc['children'][1]['children'][0], filename='models/test/other.dmx'))
    with pytest.raises(ValueError, match='proxy-to-garment mapping'):
        reconstructed_cloth.select_surface_proxy(modeldoc, ['cable.dmx', 'pants.dmx', 'other.dmx'])


def test_generated_proxy_without_named_map_is_not_guessed_from_position():
    modeldoc = mixed_proxy_modeldoc()
    del modeldoc['children'][1]['name']
    with pytest.raises(ValueError, match='vertex-map identity'):
        reconstructed_cloth.select_surface_proxy(modeldoc, ['cable.dmx', 'pants.dmx'])


def test_native_fe_node_identity_is_rest_bound_and_never_guessed():
    physics={'m_pFeModel':dict(m_nNodeCount=3,m_CtrlName=['anchor','a','b'],
        m_InitPose=[[0,0,0,1,0,0,0,1],[1,0,0,1,0,0,0,1],[0,1,0,1,0,0,0,1]])}
    proxy=dict(vertices=[[0,0,0],[1,0,0],[0,1,0]],faces=[[0,1,2]])
    assert reconstructed_cloth.bind_proxy_node_indices(proxy,[0,1,2],physics)['fe_node_indices']==[0,1,2]
    with pytest.raises(ValueError,match='rest position'):
        reconstructed_cloth.bind_proxy_node_indices(proxy,[0,2,1],physics)
    with pytest.raises(ValueError,match='control-node identity'):
        reconstructed_cloth.bind_proxy_node_indices(proxy,[0,-1,2],physics)


def test_dmx_joint_binds_keep_hierarchy_and_allow_unrelated_duplicate_mesh_names():
    import numpy as np
    class Element(dict):
        def __init__(self,name,**kwargs):super().__init__(kwargs);self.name=name
    def node(name,position,children=(),q=(0,0,0,1)):
        return Element(name,transform=dict(position=position,orientation=q),children=list(children))
    child=node('weapon',[1,0,0])
    root=node('body',[0,0,2],[child],(0,0,2**-.5,2**-.5))
    model=node('body',[0,0,0],[root]);model['jointList']=[model,root,child]
    document=SimpleNamespace(root={'model':model})
    binds=reconstructed_cloth.read_joint_rest_transforms(document)
    np.testing.assert_allclose(np.asarray(binds[2])[:3,3],[0,1,2],atol=1e-12)
    child['children']=[root]
    with pytest.raises(ValueError,match='Cycle'):
        reconstructed_cloth.read_joint_rest_transforms(document)
