"""Bind source mesh-group masks to unchanged exported geometry and rig data."""
import copy
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import struct
import subprocess
from .asset_converter import ConversionError,compiled,read_glb,sha
from .game_assets import safe_resource_path
from .map_data import dump_map_data
from .map_text import parse_map_kv3
from .source_hero_lighting_package import canonical_sha


def uint64(value):
    if isinstance(value,str):
        if not value or not value.isascii() or not value.isdecimal() or (len(value)>1 and value[0]=='0'):
            raise ValueError('Canonical uint64 mesh-group mask required')
        value=int(value)
    if type(value) is not int or not 0<=value<1<<64:raise ValueError('Bounded uint64 mesh-group mask required')
    return value


def mesh_group_contract(data,ctrl,document,*,source_sha256,base_sha256):
    names=data.get('m_meshGroups',[]);masks=data.get('m_refMeshGroupMasks',[]);lods=data.get('m_refLODGroupMasks',[])
    if (not isinstance(names,list) or len(names)>64
            or any(not isinstance(name,str) or not name for name in names) or len(set(names))!=len(names)
            or not isinstance(masks,list) or not isinstance(lods,list) or max(len(masks),len(lods))>4096):
        raise ConversionError('Unsupported bounded model mesh-group inventory')
    masks=[uint64(mask) for mask in masks];lods=[uint64(mask) for mask in lods]
    # The viewer's <=1-group shortcut is a UI selection policy. Current native
    # model code still intersects real mesh masks with the selected uint64 mask.
    default=uint64(data.get('m_nDefaultMeshGroupMask',0));unconditional=not masks
    combined=0
    for mask in lods:combined|=mask
    level=(combined&-combined).bit_length()-1 if combined else 0
    meshes=[];indices=set()
    embedded=ctrl.get('embedded_meshes',[]);references=data.get('m_refMeshes',[])
    if not isinstance(embedded,list) or not isinstance(references,list) or len(embedded)+len(references)>4096:
        raise ConversionError('Bounded model mesh references required')
    for row in embedded:
        index=row.get('mesh_index',row.get('m_nMeshIndex'));name=row.get('name',row.get('m_Name'))
        if type(index) is not int or not 0<=index<4096 or index in indices or not isinstance(name,str) or not name:
            raise ConversionError('Distinct typed embedded mesh identities required')
        indices.add(index);meshes.append((index,name,True))
    for index,name in enumerate(references):
        if not name:continue
        if not isinstance(name,str) or index in indices:raise ConversionError('Invalid or overlapping referenced mesh identity')
        safe_resource_path(compiled(name));indices.add(index)
        meshes.append((index,PurePosixPath(name).stem,False))
    expected=[]
    for index,name,embedded in meshes:
        if index<len(lods) and not (lods[index]&(1<<level)):continue
        if not unconditional and index>=len(masks):raise ConversionError('Source mesh lacks a group-mask entry')
        expected.append(dict(source_mesh_index=index,source_name=name,embedded=embedded,
                             mesh_group_mask=str(masks[index] if index<len(masks) else 0)))
    if not expected:raise ConversionError('No source meshes in exporter LOD selection')
    nodes=document.get('nodes',[]);rows=[];seen=set()
    for index,node in enumerate(nodes):
        if 'mesh' not in node:continue
        name=node.get('name')
        if not isinstance(name,str):raise ConversionError('Exported mesh node lacks source identity')
        candidates=[row for row in expected if (name.endswith('.'+row['source_name']) if row['embedded'] else name==row['source_name'])]
        if len(candidates)!=1 or candidates[0]['source_mesh_index'] in seen:
            raise ConversionError('Exported mesh/source mask identity is missing or ambiguous: '+name)
        row=dict(candidates[0]);seen.add(row['source_mesh_index']);row.update(gltf_node_index=index,exported_name=name)
        rows.append(row)
    if seen!={row['source_mesh_index'] for row in expected}:
        raise ConversionError('Source/exported LOD mesh coverage differs')
    result=dict(schema_version=1,mode='source_mesh_group_visibility',source_sha256=source_sha256,
        base_model_sha256=base_sha256,group_names=names,default_mask=str(default),unconditional=unconditional,
        exporter_lod=level,meshes=rows,
        scope='Exact exported mesh identities and Source group masks; recorded selection is applied separately. Material skins and dynamic LOD selection are unchanged.')
    result['content_sha256']=canonical_sha(result)
    return result


def annotate_document(document,contract):
    result=copy.deepcopy(document)
    for row in contract['meshes']:
        node=result['nodes'][row['gltf_node_index']]
        if node.get('name')!=row['exported_name']:raise ConversionError('Mesh node identity changed before annotation')
        extras=node.setdefault('extras',{})
        values=dict(agr_source_mesh_index=row['source_mesh_index'],agr_mesh_group_mask=row['mesh_group_mask'],
                    agr_mesh_groups_unconditional=contract['unconditional'],agr_mesh_default_mask=contract['default_mask'],
                    agr_mesh_group_contract=contract['content_sha256'])
        if set(extras)&set(values):raise ConversionError('Unexpected preexisting model mesh-group annotation')
        extras.update(values)
    return result


def write_glb_json(source,output,document):
    """Stream unchanged non-JSON chunks; preserve all buffer offsets and bytes."""
    source=Path(source);output=Path(output)
    with source.open('rb') as stream:
        header=stream.read(20)
        if len(header)!=20:raise ConversionError('Truncated model GLB')
        magic,version,length,size,kind=struct.unpack('<4sIII4s',header)
        if magic!=b'glTF' or version!=2 or kind!=b'JSON' or length!=source.stat().st_size or size%4 or 20+size>length:
            raise ConversionError('Unsupported model GLB bounds')
        encoded=json.dumps(document,separators=(',',':'),allow_nan=False).encode('utf-8');encoded+=b' '*(-len(encoded)%4)
        stream.seek(20+size)
        with output.open('xb') as target:
            target.write(struct.pack('<4sIII4s',magic,version,length-size+len(encoded),len(encoded),kind));target.write(encoded)
            shutil.copyfileobj(stream,target,1024*1024)


def _ctrl(decoder,resource,provider):
    source=provider.resolve(resource)
    def build(temp):
        proc=subprocess.run([str(decoder.executable),'-i',str(source.local_path),'-b','CTRL'],capture_output=True,timeout=120,cwd=temp)
        if proc.returncode or len(proc.stdout)>16*1024*1024:raise ConversionError('Bounded model CTRL decoding failed')
        data=parse_map_kv3(proc.stdout.decode('utf-8-sig'))
        (temp/'ctrl.json').write_text(json.dumps(data,allow_nan=False),encoding='utf-8')
    directory=decoder._entry('model_mesh_CTRL_v1',{resource:source.sha256},{'adapter_sha256':sha(Path(__file__))},build)
    return json.loads((directory/'ctrl.json').read_text(encoding='utf-8'))


def prepare_model_mesh_groups(model,resource,provider,decoder):
    model=Path(model);resource=compiled(resource);source=provider.resolve(resource)
    document=read_glb(model);data=dump_map_data(decoder,resource,provider);ctrl=_ctrl(decoder,resource,provider)
    contract=mesh_group_contract(data,ctrl,document,source_sha256=source.sha256,base_sha256=sha(model))
    annotated=annotate_document(document,contract)
    manifest=model.parent/'conversion.json';record=json.loads(manifest.read_text(encoding='utf-8'))
    inventory=record.get('files')
    if not isinstance(inventory,dict) or not inventory:raise ConversionError('Verified base model inventory required')
    files=[]
    for relative,digest in inventory.items():
        safe_resource_path(relative);path=model.parent/relative
        if not path.resolve().is_relative_to(model.parent.resolve()) or sha(path)!=digest:
            raise ConversionError('Base model dependency identity changed')
        files.append((relative,path,digest))
    def build(temp):
        for relative,path,digest in files:
            if path==model:continue
            target=temp/relative;target.parent.mkdir(parents=True,exist_ok=True)
            # These content-verified cache files are immutable. Hardlinks retain
            # independent paths/manifests without another copy of all textures.
            try:os.link(path,target)
            except OSError:shutil.copyfile(path,target)
            if sha(target)!=digest:raise ConversionError('Model dependency changed during mesh annotation')
        write_glb_json(model,temp/model.name,annotated)
        (temp/'model_mesh_groups.json').write_text(json.dumps(contract,indent=2),encoding='utf-8')
    directory=decoder._entry('model_mesh_groups_v1',{'base_conversion_sha256':sha(manifest),resource:source.sha256},
        {'contract_sha256':contract['content_sha256'],'adapter_sha256':sha(Path(__file__))},build)
    output=directory/model.name
    return output,dict(contract,model_sha256=sha(output))
