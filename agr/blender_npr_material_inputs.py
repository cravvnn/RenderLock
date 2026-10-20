"""Bind converted opaque hero inputs on a caller-owned material copy."""
from pathlib import Path
import hashlib


def validate_material_inputs(material, transmissive, *, uv_name):
    """Read-only converted-input validation, usable before material allocation."""
    if not isinstance(uv_name,str) or not uv_name:raise ValueError('Explicit mesh UV layer required')
    if not material.use_nodes or material.node_tree is None:raise ValueError('Material nodes required')
    nodes=material.node_tree.nodes
    bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED']
    ao=[n.inputs['Occlusion'] for n in nodes if n.type=='GROUP' and n.node_tree.name=='glTF Material Output' and 'Occlusion' in n.inputs]
    if len(bsdfs)!=1 or len(ao)!=1 or not ao[0].is_linked:raise ValueError('Unqualified converted material/AO graph')
    bsdf=bsdfs[0]
    from .blender_material_alpha import validate_alpha
    validate_alpha(material,bsdf)
    path=Path(transmissive['path'])
    from .asset_converter import sha
    if sha(path)!=transmissive['sha256']:raise ValueError('Transmissive texture changed')
    return {'Albedo':bsdf.inputs['Base Color'],'Metallic':bsdf.inputs['Metallic'],'Roughness':bsdf.inputs['Roughness'],'Material AO':ao[0]}


def bind_material_inputs(material,target,transmissive,*,uv_name):
    """Caller qualifies material identity, mesh UV layer and Source semantics.

    Preserves converted albedo/metal/rough/AO expressions. Does not qualify their
    equivalence to Source tint/status/selfillum expressions or infer a UV layer.
    """
    import bpy
    if target.id_data!=material.node_tree:raise ValueError('Target must belong to material')
    nodes,links=material.node_tree.nodes,material.node_tree.links
    mapping=validate_material_inputs(material,transmissive,uv_name=uv_name)
    for name in (*mapping,'Transmissive'):
        if name not in target.inputs:raise ValueError('Missing NPR material input: '+name)
    path=Path(transmissive['path'])
    from .blender_images import load_packed_image
    image=load_packed_image(path,expected_sha256=transmissive['sha256'])
    image.colorspace_settings.name='sRGB';image.alpha_mode='CHANNEL_PACKED'
    for name,socket in mapping.items():
        if socket.is_linked:links.new(socket.links[0].from_socket,target.inputs[name])
        else:target.inputs[name].default_value=socket.default_value[:3] if name=='Albedo' else socket.default_value
    uv=nodes.new('ShaderNodeUVMap');uv.uv_map=uv_name
    texture=nodes.new('ShaderNodeTexImage');texture.name='AGR NPR transmissive';texture.image=image;texture.interpolation='Linear';texture.extension='REPEAT'
    links.new(uv.outputs['UV'],texture.inputs['Vector']);links.new(texture.outputs['Color'],target.inputs['Transmissive'])
    return {'uv_layer':uv_name,'transmissive_sha256':transmissive['sha256'],'linked_inputs':{name:target.inputs[name].is_linked for name in (*mapping,'Transmissive')}}
