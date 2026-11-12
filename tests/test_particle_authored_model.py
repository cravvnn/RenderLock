from copy import deepcopy
import hashlib,json,struct
import pytest
from types import SimpleNamespace
from agr.asset_converter import sha
from agr.installed_effects import UnsupportedEffect
from agr.particle_model_bindings import authored_primitive_bindings,authored_material_slot_bytes,POLICY as INVENTORY_POLICY
from agr.particle_authored_model import (POLICY,MODEL,SOURCE_SHA,RENDERER_SHA,PARTICLES_SHA,FIELD_POLICY,
    validate_authored_model,resolved_sample_selections,is_authored_renderer)
from agr.particle_authored_model import prepare_authored_model
from agr.particle_model_assets import model_resource
from agr.particle_authored_model import validate_authored_model,SCENE_SHA

RENDERER={'_class':'C_OP_RenderModels','m_ModelList':[{'m_model':MODEL}],
    'm_nBodyGroupField':19,'m_nSubModelField':26,'m_bOrientZ':True,'m_modelInput':{},
    'm_bDisableShadows':True,'m_bAcceptsDecals':False,'m_bDoNotDrawInParticlePass':True}

def glb(path,document,binary=b''):
    text=json.dumps(document).encode();text+=b' '*(-len(text)%4);binary+=b'\0'*(-len(binary)%4)
    path.write_bytes(struct.pack('<III',0x46546c67,2,28+len(text)+len(binary))
        +struct.pack('<II',len(text),0x4e4f534a)+text+struct.pack('<II',len(binary),0x004e4942)+binary)
    return path

def fixture(tmp_path):
    binary=struct.pack('<9f',0,0,0,1,0,0,0,1,0)+struct.pack('<3I',0,1,2)
    names=[f'team_{i}'for i in range(6)];order=[2,0,5,1,4,3];masks=[2,1,4,8,16,32]
    rows=[dict(source_mesh_index=i,source_name=f'mesh_{i}',embedded=True,mesh_group_mask=str(masks[i]),
        primitives=[dict(primitive=0,indices=3,indices_sha256=hashlib.sha256(binary[36:]).hexdigest(),
            material=f'materials/m{i}.vmat',tint=[1,1,1,1])])for i in range(6)]
    inventory=dict(schema_version=1,policy=INVENTORY_POLICY,resource=MODEL,source_sha256=SOURCE_SHA,
        mesh_groups=names,body_groups=[dict(name='team',choices=[dict(group_index=i,name=str(i),full_name=names[i])for i in range(6)])],meshes=rows)
    document=dict(asset=dict(version='2.0'),buffers=[dict(byteLength=len(binary))],
        bufferViews=[dict(buffer=0,byteLength=36),dict(buffer=0,byteOffset=36,byteLength=12)],
        accessors=[dict(componentType=5126,count=3,type='VEC3',bufferView=0),dict(componentType=5125,count=3,type='SCALAR',bufferView=1)],
        nodes=[dict(name=f'asset.mesh_{i}',mesh=k)for k,i in enumerate(order)],
        meshes=[dict(primitives=[dict(attributes=dict(POSITION=0),indices=1)])for _ in order],scenes=[dict(nodes=list(range(6)))])
    geometry=glb(tmp_path/'model.glb',document,binary)
    bindings=authored_primitive_bindings(geometry,inventory,resource=MODEL,source_sha256=SOURCE_SHA,geometry_sha256=sha(geometry))
    slots=tmp_path/'slots.glb';slots.write_bytes(authored_material_slot_bytes(geometry,bindings))
    material_names=[f'materials/m{i}.vmat_c'for i in range(6)]
    bundle=glb(tmp_path/'materials.glb',dict(asset=dict(version='2.0'),materials=[dict(extras=dict(vmat=dict(Name=n[:-2])))for n in material_names]))
    return dict(schema_version=1,policy=POLICY,resource=MODEL,source_sha256=SOURCE_SHA,directory=str(tmp_path),
        geometry_file='model.glb',geometry_sha256=sha(geometry),slot_file='slots.glb',slot_sha256=sha(slots),inventory=inventory,bindings=bindings,
        default_mesh_group_mask=1,material_bundle=dict(path=str(bundle),sha256=sha(bundle),materials=material_names),
        native_selector=dict(particles_sha256=PARTICLES_SHA,renderer_sha256=RENDERER_SHA,body_group=0,submodel_field=26,subtype=0,scenesystem_sha256=SCENE_SHA))

def test_exact_profile_preserves_authored_slots_and_reordered_native_variant_selection(tmp_path):
    descriptor=fixture(tmp_path)
    assert is_authored_renderer(RENDERER)
    assert validate_authored_model(RENDERER,descriptor)is descriptor
    samples=[(i,0,dict(color=[1,1,1],alpha=1,alpha_alternate=1,scratch_float1=v))for i,v in enumerate((0,1,1.9,-1,6))]
    selected=resolved_sample_selections(descriptor,samples,{'model_material_fields_policy':FIELD_POLICY})
    assert [r['source_mesh_indices']for r in selected]==[[1],[0],[0],[1],[1]]
    assert [r['gltf_mesh_indices']for r in selected][:2]==[[3],[1]]

@pytest.mark.parametrize('change',[
    lambda d:d.update(source_sha256='0'*64),
    lambda d:d.update(schema_version=True),
    lambda d:d['native_selector'].update(particles_sha256='0'*64),
    lambda d:d['native_selector'].update(body_group=1),
    lambda d:d.update(default_mesh_group_mask=63),
    lambda d:d['inventory']['meshes'][0].update(mesh_group_mask='1'),
    lambda d:d['bindings']['meshes'][0]['primitives'][0].update(material='materials/other.vmat'),
    lambda d:d['material_bundle']['materials'].pop(),
])
def test_changed_source_selector_material_and_group_metadata_refuses(tmp_path,change):
    descriptor=fixture(tmp_path);change(descriptor)
    with pytest.raises(UnsupportedEffect):validate_authored_model(RENDERER,descriptor)

@pytest.mark.parametrize('filename,key',[('model.glb','geometry_sha256'),('slots.glb','slot_sha256'),('materials.glb',None)])
def test_changed_asset_bytes_refuse_even_when_other_metadata_is_unchanged(tmp_path,filename,key):
    descriptor=fixture(tmp_path);p=tmp_path/filename;p.write_bytes(p.read_bytes()+b'changed!')
    with pytest.raises(UnsupportedEffect):validate_authored_model(RENDERER,descriptor)

@pytest.mark.parametrize('patch',[{'alpha':.5},{'alpha_alternate':0},{'color':[.5,1,1]},{'scratch_float1':float('nan')},{'scratch_float1':None}])
def test_missing_selector_and_unqualified_tint_alpha_are_not_silently_ignored(tmp_path,patch):
    descriptor=fixture(tmp_path);p=dict(color=[1,1,1],alpha=1,alpha_alternate=1,scratch_float1=0);p.update(patch)
    with pytest.raises(UnsupportedEffect):resolved_sample_selections(descriptor,[(0,0,p)],{'model_material_fields_policy':FIELD_POLICY})

def test_selector_policy_and_unknown_renderer_do_not_gain_support(tmp_path):
    descriptor=fixture(tmp_path)
    with pytest.raises(UnsupportedEffect):resolved_sample_selections(descriptor,[],{})
    changed=deepcopy(RENDERER);changed['m_nSubModelField']=13
    assert not is_authored_renderer(changed)
    with pytest.raises(UnsupportedEffect):validate_authored_model(changed,descriptor)

def test_annotated_glb_keeps_original_binary_and_labels_source_meshes(tmp_path):
    descriptor=fixture(tmp_path);source=(tmp_path/'model.glb').read_bytes();slots=(tmp_path/'slots.glb').read_bytes()
    old_json=struct.unpack_from('<I',source,12)[0];new_json=struct.unpack_from('<I',slots,12)[0]
    assert slots[20+new_json:]==source[20+old_json:]
    document=json.loads(slots[20:20+new_json]);assert [n['extras']['agr_source_mesh_index']for n in document['nodes']]==[2,0,5,1,4,3]

def test_changed_native_module_refuses_before_any_converter_or_model_resolution(tmp_path):
    module=tmp_path/'game/bin/win64/particles.dll';module.parent.mkdir(parents=True);module.write_bytes(b'unqualified module')
    with pytest.raises(UnsupportedEffect,match='qualified installed particle module'):
        prepare_authored_model(None,RENDERER,SimpleNamespace(install_path=tmp_path),'unused-helper.dll')

@pytest.mark.parametrize('field,value',[('skin',0),('children',[1]),('translation',[0,0,0])])
def test_inactive_looking_node_features_are_not_silently_discarded(tmp_path,field,value):
    descriptor=fixture(tmp_path);path=tmp_path/'model.glb';raw=path.read_bytes();size=struct.unpack_from('<I',raw,12)[0]
    doc=json.loads(raw[20:20+size]);doc['nodes'][0][field]=value;glb(path,doc,raw[28+size:]);descriptor['geometry_sha256']=sha(path)
    with pytest.raises(UnsupportedEffect,match='inert source frame'):validate_authored_model(RENDERER,descriptor)
