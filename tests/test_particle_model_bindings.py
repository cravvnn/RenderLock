from copy import deepcopy
import hashlib
import json
import struct

import pytest

from agr.installed_effects import UnsupportedEffect
from agr.particle_model_bindings import authored_primitive_bindings, authored_material_slot_document, POLICY

RESOURCE='models/test/model.vmdl'
SOURCE='1'*64


def fixture(tmp_path):
    indices=struct.pack('<6I',0,1,2,2,1,0)
    document={'buffers':[{'byteLength':len(indices)}],
        'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':len(indices)}],
        'accessors':[{'type':'SCALAR','componentType':5125,'count':3,'bufferView':0,'byteOffset':0},
                     {'type':'SCALAR','componentType':5125,'count':3,'bufferView':0,'byteOffset':12},
                     {'type':'VEC3','componentType':5126,'count':4}],
        'nodes':[{'name':'model.mesh_B','mesh':0},{'name':'model.mesh_A','mesh':1}],
        'meshes':[{'primitives':[{'indices':1,'attributes':{'POSITION':2}}]},
                  {'primitives':[{'indices':0,'attributes':{'POSITION':2}}]}]}
    rows=[]
    for i,label in enumerate(('A','B')):
        rows.append({'source_mesh_index':i,'source_name':'mesh_'+label,'embedded':True,
            'mesh_group_mask':str(1<<i),'primitives':[{'primitive':0,'vertices':3,'indices':3,
                'indices_sha256':hashlib.sha256(indices[i*12:(i+1)*12]).hexdigest(),
                'material':'materials/'+label+'.vmat','tint':[1.,1.,1.,1.]}]})
    inventory={'schema_version':1,'policy':POLICY,'resource':RESOURCE,'source_sha256':SOURCE,'meshes':rows}
    def save():
        text=json.dumps(document,separators=(',',':')).encode();text+=b' '*(-len(text)%4)
        raw=struct.pack('<III',0x46546c67,2,28+len(text)+len(indices))+struct.pack('<II',len(text),0x4e4f534a)+text+struct.pack('<II',len(indices),0x004e4942)+indices
        path=tmp_path/'model.glb';path.write_bytes(raw)
        return path,hashlib.sha256(raw).hexdigest()
    return document,inventory,save


def test_maps_materials_by_source_mesh_and_index_stream_despite_reordered_meshes_and_shared_vertices(tmp_path):
    document,inventory,save=fixture(tmp_path);before=deepcopy(inventory);path,digest=save()
    result=authored_primitive_bindings(path,inventory,resource=RESOURCE,source_sha256=SOURCE,geometry_sha256=digest)
    assert [row['gltf_mesh_index']for row in result['meshes']]==[1,0]
    assert [row['primitives'][0]['material']for row in result['meshes']]==['materials/a.vmat','materials/b.vmat']
    assert [row['mesh_group_mask']for row in result['meshes']]==['1','2']
    assert inventory==before


@pytest.mark.parametrize('change',[
    lambda d,i:i['meshes'][0]['primitives'][0].update(indices_sha256='0'*64),
    lambda d,i:i['meshes'][1].update(source_mesh_index=0),
    lambda d,i:i.update(source_sha256='2'*64),
    lambda d,i:i.update(schema_version=True),
    lambda d,i:i['meshes'][0]['primitives'][0].update(primitive=True),
    lambda d,i:d['buffers'][0].update(uri='external.bin'),
    lambda d,i:d['bufferViews'][0].update(byteLength=4),
    lambda d,i:d['nodes'].append({'name':'unmatched','mesh':0}),
])
def test_changed_identity_geometry_and_unqualified_layout_refuse(tmp_path,change):
    document,inventory,save=fixture(tmp_path);change(document,inventory);path,digest=save()
    with pytest.raises(UnsupportedEffect):
        authored_primitive_bindings(path,inventory,resource=RESOURCE,source_sha256=SOURCE,geometry_sha256=digest)


def test_changed_geometry_file_fingerprint_refuses_before_binding(tmp_path):
    _,inventory,save=fixture(tmp_path);path,digest=save();path.write_bytes(path.read_bytes()+b'bad!')
    with pytest.raises(UnsupportedEffect,match='fingerprint'):
        authored_primitive_bindings(path,inventory,resource=RESOURCE,source_sha256=SOURCE,geometry_sha256=digest)


def test_authored_slots_preserve_geometry_and_use_exact_source_materials(tmp_path):
    document,inventory,save=fixture(tmp_path);path,digest=save()
    bound=authored_primitive_bindings(path,inventory,resource=RESOURCE,source_sha256=SOURCE,geometry_sha256=digest)
    original=path.read_bytes();result=authored_material_slot_document(path,bound)
    assert path.read_bytes()==original
    assert result['accessors']==document['accessors'] and result['bufferViews']==document['bufferViews']
    assert [m['extras']['agr_authored_vmat']for m in result['materials']]==['materials/a.vmat','materials/b.vmat']
    assert result['meshes'][0]['primitives'][0]['material']==1
    assert result['meshes'][1]['primitives'][0]['material']==0
    assert all('material'not in p for m in document['meshes']for p in m['primitives'])


def test_authored_slots_refuse_changed_file_or_incomplete_bindings(tmp_path):
    _,inventory,save=fixture(tmp_path);path,digest=save()
    bound=authored_primitive_bindings(path,inventory,resource=RESOURCE,source_sha256=SOURCE,geometry_sha256=digest)
    wrong=deepcopy(bound);wrong['meshes'][0]['primitives']=[]
    with pytest.raises(UnsupportedEffect):authored_material_slot_document(path,wrong)
    path.write_bytes(path.read_bytes()+b'bad!')
    with pytest.raises(UnsupportedEffect):authored_material_slot_document(path,bound)
