"""Bind authored VMAT slots to unchanged geometry using source index streams.

The caller verifies the inventory helper and source fingerprints. This mapping
does not qualify particle placement, native submodel selection or material shading.
"""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import re
import struct
import numpy as np

from .game_assets import safe_resource_path
from .installed_effects import UnsupportedEffect
from .model_mesh_groups import uint64

POLICY='agr_particle_authored_mesh_material_inventory_v1'


def _require(value,reason):
    if not value:raise UnsupportedEffect(reason)


def _integer(value,low,high):
    _require(type(value) is int and low<=value<=high,'Invalid authored model index/range')
    return value


def authored_primitive_bindings(path,inventory,*,resource,source_sha256,geometry_sha256):
    path=Path(path)
    _require(20<=path.stat().st_size<=64*1024*1024,'Authored model geometry exceeds64MiB')
    raw=path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest()==geometry_sha256,'Authored model geometry fingerprint changed')
    _require(struct.unpack_from('<III',raw)==(0x46546c67,2,len(raw)),'Invalid authored model GLB header')
    chunks=[];offset=12
    while offset<len(raw):
        _require(offset+8<=len(raw),'Truncated authored model GLB chunk')
        size,kind=struct.unpack_from('<II',raw,offset);offset+=8
        _require(size%4==0 and offset+size<=len(raw),'Invalid authored model GLB chunk range')
        chunks.append((kind,raw[offset:offset+size]));offset+=size
    _require([k for k,_ in chunks]==[0x4e4f534a,0x004e4942],'Authored model requires embedded GLB data')
    document=json.loads(chunks[0][1]);binary=chunks[1][1]
    buffers=document.get('buffers',[])
    _require(len(buffers)==1 and 'uri' not in buffers[0] and not document.get('extensionsRequired'),
             'External or extended authored model buffers are unqualified')
    length=_integer(buffers[0].get('byteLength'),len(binary)-3,len(binary));binary=binary[:length]
    _require(type(inventory.get('schema_version')) is int and inventory['schema_version']==1 and inventory.get('policy')==POLICY
             and inventory.get('resource')==resource and inventory.get('source_sha256')==source_sha256,
             'Authored model inventory/source identity differs')
    _require(safe_resource_path(resource).endswith('.vmdl'),'Expected authored model resource')
    rows=inventory.get('meshes');nodes=document.get('nodes',[]);meshes=document.get('meshes',[])
    _require(isinstance(rows,list) and 1<=len(rows)<=256 and len(meshes)<=256 and len(nodes)<=512,
             'Invalid bounded authored model mesh inventory')
    result=[];seen=set();source_indices=set()
    for row in rows:
        source_index=_integer(row.get('source_mesh_index'),0,4095)
        _require(source_index not in source_indices,'Duplicate authored source mesh identity');source_indices.add(source_index)
        name=row.get('source_name');embedded=row.get('embedded')
        _require(isinstance(name,str) and name and type(embedded) is bool,'Missing authored mesh name/kind')
        matching=[(i,n) for i,n in enumerate(nodes) if 'mesh' in n and isinstance(n.get('name'),str)
                  and (n['name'].endswith('.'+name) if embedded else n['name']==name)]
        _require(len(matching)==1,'Authored mesh/exported node identity is missing or ambiguous')
        node_index,node=matching[0];mesh_index=_integer(node['mesh'],0,len(meshes)-1)
        _require(mesh_index not in seen,'Authored model instancing requires separate qualification');seen.add(mesh_index)
        primitives=meshes[mesh_index].get('primitives',[]);calls=row.get('primitives')
        _require(isinstance(calls,list) and 1<=len(calls)<=4096 and len(primitives)==len(calls),
                 'Authored draw-call/primitive coverage differs')
        bindings=[]
        for ordinal,(primitive,call) in enumerate(zip(primitives,calls)):
            _require(type(call.get('primitive')) is int and call['primitive']==ordinal and primitive.get('mode',4)==4,
                     'Authored draw-call ordering/topology differs')
            count=_integer(call.get('indices'),3,3000000)
            _require(count%3==0,'Authored model indices are not triangles')
            accessors=document.get('accessors',[])
            accessor=accessors[_integer(primitive.get('indices'),0,len(accessors)-1)]
            kind=accessor.get('componentType');types={5121:'u1',5123:'<u2',5125:'<u4'}
            _require(kind in types and accessor.get('type')=='SCALAR' and accessor.get('count')==count
                     and not any(accessor.get(k) for k in ('normalized','extensions','sparse')),
                     'Unqualified authored model index accessor')
            views=document.get('bufferViews',[]);view=views[_integer(accessor.get('bufferView'),0,len(views)-1)]
            _require(view.get('buffer',0)==0 and not view.get('extensions'),'Unqualified authored index buffer view')
            item=np.dtype(types[kind]).itemsize;stride=_integer(view.get('byteStride',item),item,252)
            start=_integer(view.get('byteOffset',0),0,len(binary));size=_integer(view.get('byteLength'),0,len(binary)-start)
            local=_integer(accessor.get('byteOffset',0),0,size)
            _require(stride%item==0 and local%item==0 and local+(count-1)*stride+item<=size,
                     'Authored model indices exceed their buffer view')
            values=np.ndarray((count,),dtype=types[kind],buffer=binary,offset=start+local,strides=(stride,))
            position=accessors[_integer(primitive.get('attributes',{}).get('POSITION'),0,len(accessors)-1)]
            _require(position.get('type')=='VEC3' and int(values.max())<_integer(position.get('count'),1,1000000),
                     'Authored model index exceeds vertex buffer')
            actual=hashlib.sha256(values.astype('<u4').tobytes()).hexdigest();expected=call.get('indices_sha256')
            _require(isinstance(expected,str) and re.fullmatch('[0-9a-f]{64}',expected) and actual==expected,
                     'Authored draw-call index stream differs')
            material=safe_resource_path(call.get('material'))
            _require(material.endswith('.vmat'),'Exact authored VMAT identity required')
            tint=call.get('tint')
            _require(isinstance(tint,list) and len(tint)==4
                     and all(type(v) in(int,float) and math.isfinite(v) for v in tint),'Invalid authored primitive tint')
            bindings.append(dict(primitive=ordinal,material=material,tint=list(tint),indices_sha256=actual))
        result.append(dict(source_mesh_index=source_index,gltf_node_index=node_index,gltf_mesh_index=mesh_index,
                           mesh_group_mask=str(uint64(row.get('mesh_group_mask'))),primitives=bindings))
    _require(seen==set(range(len(meshes))) and sum('mesh'in n for n in nodes)==len(rows),
             'Authored model has unmatched exported geometry')
    return dict(policy=POLICY,resource=resource,source_sha256=source_sha256,
                geometry_sha256=geometry_sha256,meshes=result,
                scope='Authored primitive/material identity only; native variant choice and rendering remain separate')


def authored_material_slot_document(path, bindings):
    """Annotate geometry-only GLB JSON with stable original VMAT/tint slots.

    These are import placeholders, not converted shaders. A consumer must replace
    every slot with its verified authored material and apply the retained tint.
    Binary geometry and the original file remain unchanged.
    """
    from .asset_converter import read_glb,sha
    path=Path(path)
    _require(bindings.get('policy')==POLICY and 20<=path.stat().st_size<=64*1024*1024
             and sha(path)==bindings.get('geometry_sha256'),'Authored slot geometry identity differs')
    document=deepcopy(read_glb(path));meshes=document.get('meshes',[]);nodes=document.get('nodes',[])
    _require(not document.get('materials'),'Authored slots require geometry-only input')
    rows=bindings.get('meshes');seen=set();slots={};materials=[]
    _require(isinstance(rows,list) and 1<=len(rows)==len(meshes)<=256,'Authored slot mesh coverage differs')
    for row in rows:
        index=_integer(row.get('gltf_mesh_index'),0,len(meshes)-1)
        node=nodes[_integer(row.get('gltf_node_index'),0,len(nodes)-1)]
        _require(index not in seen and node.get('mesh')==index,'Authored slot mesh/node identity differs');seen.add(index)
        extras=node.setdefault('extras',{})
        source=_integer(row.get('source_mesh_index'),0,4095)
        _require(isinstance(extras,dict) and extras.get('agr_source_mesh_index',source)==source,
                 'Authored slot source mesh metadata differs')
        extras['agr_source_mesh_index']=source
        calls=row.get('primitives');primitives=meshes[index].get('primitives',[])
        _require(isinstance(calls,list) and len(calls)==len(primitives) and 1<=len(calls)<=4096,
                 'Authored slot primitive coverage differs')
        for ordinal,(primitive,call) in enumerate(zip(primitives,calls)):
            _require(type(call.get('primitive'))is int and call['primitive']==ordinal and 'material'not in primitive,
                     'Authored slot primitive identity differs')
            material=safe_resource_path(call.get('material'));tint=call.get('tint')
            _require(material.endswith('.vmat') and isinstance(tint,list) and len(tint)==4
                     and all(type(v)in(int,float)and math.isfinite(v)for v in tint),'Invalid authored slot material/tint')
            key=(material,tuple(tint))
            if key not in slots:
                slots[key]=len(materials)
                materials.append(dict(name=f'AGR authored source slot {len(materials)}',
                    extras=dict(agr_authored_vmat=material,agr_authored_tint=list(tint))))
            primitive['material']=slots[key]
    document['materials']=materials
    return document


def authored_material_slot_bytes(path, bindings):
    """Serialize slot annotations while preserving the entire binary chunk."""
    document=authored_material_slot_document(path,bindings)
    raw=Path(path).read_bytes()
    _require(hashlib.sha256(raw).hexdigest()==bindings['geometry_sha256'],
             'Authored slot geometry changed during annotation')
    old_size=struct.unpack_from('<I',raw,12)[0]
    binary_chunk=raw[20+old_size:]
    text=json.dumps(document,separators=(',',':'),allow_nan=False).encode('utf-8')
    text+=b' '*(-len(text)%4)
    return (struct.pack('<III',0x46546c67,2,20+len(text)+len(binary_chunk))
            +struct.pack('<II',len(text),0x4e4f534a)+text+binary_chunk)
