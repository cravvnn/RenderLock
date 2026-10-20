import copy
import json
import struct
from types import SimpleNamespace
import pytest
from agr.model_mesh_groups import mesh_group_contract,annotate_document,uint64,write_glb_json
from agr.blender_mesh_groups import selected_visibility,validate_imported_mesh_groups


def inputs():
    data=dict(m_meshGroups=['body','attachment'],m_refMeshGroupMasks=[1,2,1],
              m_refLODGroupMasks=[1,1,2],m_nDefaultMeshGroupMask=1,m_refMeshes=[])
    ctrl=dict(embedded_meshes=[dict(m_Name='body',m_nMeshIndex=0),dict(m_Name='attachment',m_nMeshIndex=1),dict(m_Name='body_lod1',m_nMeshIndex=2)])
    doc=dict(nodes=[dict(name='owned.vmdl_c.body',mesh=0,skin=0),dict(name='owned.vmdl_c.attachment',mesh=1,skin=0)],meshes=[{},{}])
    return data,ctrl,doc


def test_source_masks_select_default_without_removing_geometry_or_changing_rig():
    data,ctrl,doc=inputs();before=copy.deepcopy(doc)
    contract=mesh_group_contract(data,ctrl,doc,source_sha256='source',base_sha256='base')
    result=annotate_document(doc,contract)
    assert doc==before and result['meshes']==doc['meshes']
    assert [n['skin'] for n in result['nodes']]==[0,0]
    assert [n['extras']['agr_mesh_group_mask'] for n in result['nodes']]==['1','2']
    assert contract['exporter_lod']==0 and len(contract['meshes'])==2
    meshes=[]
    for node in result['nodes']:
        class Mesh(dict):type='MESH'
        meshes.append(Mesh(node['extras']))
    validate_imported_mesh_groups(meshes,contract)
    meshes[1]['agr_source_mesh_index']=0
    with pytest.raises(ValueError,match='coverage'):validate_imported_mesh_groups(meshes,contract)


def test_glb_annotation_preserves_all_binary_chunks_and_unmodified_source(tmp_path):
    data,ctrl,document=inputs()
    document.update(skins=[dict(joints=[2,3])],accessors=[dict(bufferView=0)],
                    buffers=[dict(byteLength=16)],bufferViews=[dict(buffer=0,byteOffset=0,byteLength=16)])
    encoded=json.dumps(document).encode();encoded+=b' '*(-len(encoded)%4)
    # Keep a second opaque chunk as well as BIN: only JSON may change.
    tail=struct.pack('<I4s',16,b'BIN\x00')+bytes(range(16))+struct.pack('<I4s',4,b'OWND')+b'keep'
    original=struct.pack('<4sIII4s',b'glTF',2,20+len(encoded)+len(tail),len(encoded),b'JSON')+encoded+tail
    source=tmp_path/'source.glb';output=tmp_path/'annotated.glb';source.write_bytes(original)
    contract=mesh_group_contract(data,ctrl,document,source_sha256='source',base_sha256='base')
    annotated=annotate_document(document,contract);write_glb_json(source,output,annotated)
    result=output.read_bytes();size=struct.unpack_from('<I',result,12)[0]
    assert source.read_bytes()==original and result[20+size:]==tail
    assert len(result)==struct.unpack_from('<I',result,8)[0]
    for node in annotated['nodes']:
        for key in list(node['extras']):
            if key.startswith('agr_'):del node['extras'][key]
        if not node['extras']:del node['extras']
    assert annotated==document


@pytest.mark.parametrize('mutation',['mask','default','identity','lod','duplicate','unmapped'])
def test_unsupported_metadata_or_ambiguous_mapping_fails_closed(mutation):
    data,ctrl,doc=inputs()
    if mutation=='mask':data['m_refMeshGroupMasks'][0]=-1
    if mutation=='default':data['m_nDefaultMeshGroupMask']=True
    if mutation=='identity':doc['nodes'][0]['name']='display_name_guess'
    if mutation=='lod':data['m_refLODGroupMasks']=[0,0,0]
    if mutation=='duplicate':ctrl['embedded_meshes'][1]['m_nMeshIndex']=0
    if mutation=='unmapped':doc['nodes'].pop()
    with pytest.raises(ValueError):mesh_group_contract(data,ctrl,doc,source_sha256='source',base_sha256='base')


def test_recorded_uint64_selection_and_lifetime_both_restrict_visibility():
    obj=dict(agr_mesh_group_mask=str(1<<63),agr_mesh_default_mask='0',agr_mesh_groups_unconditional=False)
    data=dict(start_tick=10,end_tick=12,tick_rate=64)
    samples=[dict(tick=10,mesh_group_mask=str((1<<64)-1)),dict(tick=11,mesh_group_mask='0'),dict(tick=12,mesh_group_mask=str(1<<63))]
    assert selected_visibility(obj,{},samples,data,64,[(1,False),(3,True)])==[(1,False),(2,True)]
    samples[1].pop('mesh_group_mask')
    with pytest.raises(ValueError,match='coverage has gaps'):selected_visibility(obj,{},samples,data,64,[(1,False)])


def test_single_group_mesh_still_respects_a_recorded_zero_mask():
    data,ctrl,document=inputs();data['m_meshGroups']=['body'];data['m_refMeshGroupMasks']=[1,1,1]
    contract=mesh_group_contract(data,ctrl,document,source_sha256='source',base_sha256='base')
    assert contract['unconditional'] is False
    obj=dict(agr_mesh_group_mask='1',agr_mesh_default_mask='1',agr_mesh_groups_unconditional=contract['unconditional'])
    samples=[dict(tick=10,mesh_group_mask='1'),dict(tick=11,mesh_group_mask='0')]
    assert selected_visibility(obj,{},samples,dict(start_tick=10,end_tick=11,tick_rate=64),64,[(1,False)])==[(1,False),(2,True)]


@pytest.mark.parametrize('value',[-1,1<<64,True,1.5,'01',' 1','+1'])
def test_mesh_group_masks_preserve_uint64_bounds_and_canonical_text(value):
    with pytest.raises(ValueError):uint64(value)
