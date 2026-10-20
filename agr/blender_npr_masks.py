"""Bind authenticated Source red tint/green rim channels on the albedo UV.

The qualified pbr pixel shader samples both masks and color at the same UV.
Blender uses linear repeat sampling; Source anisotropic/minification parity is
not claimed. Callers still authenticate the material/package relationship.
"""
from pathlib import Path
from .asset_converter import sha
from .source_npr_assets import png_dimensions

NODE_NAME='AGR NPR tint rim mask'


def validate_mask_texture(texture):
    if not isinstance(texture,dict):raise ValueError('Authenticated NPR mask texture required')
    path=Path(texture['path'])
    if sha(path)!=texture['sha256'] or png_dimensions(path)!=texture['size']:
        raise ValueError('NPR mask texture changed')
    return path


def bind_mask_texture(material,texture,*,uv_name=None):
    """Return R/G sockets, preserving the complete image and explicit UV."""
    import bpy
    path=validate_mask_texture(texture)
    nodes,links=material.node_tree.nodes,material.node_tree.links
    existing=nodes.get(NODE_NAME)
    if existing is not None:
        split=nodes.get(NODE_NAME+' channels')
        if (existing.type!='TEX_IMAGE' or existing.image is None or split is None
                or existing.get('source_sha256')!=texture['sha256']
                or existing.get('source_resource')!=texture['resource']
                or existing.interpolation!='Linear' or existing.extension!='REPEAT'
                or existing.image.colorspace_settings.name!='Non-Color'
                or existing.image.alpha_mode!='CHANNEL_PACKED'
                or list(existing.image.size)!=texture['size']
                or len(split.inputs['Color'].links)!=1
                or split.inputs['Color'].links[0].from_node!=existing):
            raise ValueError('Prepared NPR mask graph changed')
        return split.outputs['Red'],split.outputs['Green']
    if not isinstance(uv_name,str) or not uv_name:
        # Saved NPR material's qualified source-color texture retains the exact
        # render UV; never infer an arbitrary first layer from an object.
        bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED']
        if len(bsdfs)!=1 or len(bsdfs[0].inputs['Base Color'].links)!=1:
            raise ValueError('Explicit qualified mask UV required')
        from .blender_npr_albedo_inputs import raw_albedo_input
        source,_=raw_albedo_input(material)
        if source.type!='TEX_IMAGE' or len(source.inputs['Vector'].links)!=1:
            raise ValueError('Qualified albedo UV binding required for mask')
        uv=source.inputs['Vector'].links[0].from_node
        if uv.type!='UVMAP' or not uv.uv_map:
            raise ValueError('Unsupported mask UV transform')
        uv_name=uv.uv_map
    from .blender_images import load_packed_image
    image=load_packed_image(path,expected_sha256=texture['sha256'])
    image.colorspace_settings.name='Non-Color';image.alpha_mode='CHANNEL_PACKED'
    tex=nodes.new('ShaderNodeTexImage');tex.name=NODE_NAME;tex.image=image
    tex.interpolation='Linear';tex.extension='REPEAT'
    tex['source_sha256']=texture['sha256'];tex['source_resource']=texture['resource']
    uv=nodes.new('ShaderNodeUVMap');uv.uv_map=uv_name
    split=nodes.new('ShaderNodeSeparateColor');split.mode='RGB';split.name=NODE_NAME+' channels'
    links.new(uv.outputs['UV'],tex.inputs['Vector']);links.new(tex.outputs['Color'],split.inputs['Color'])
    return split.outputs['Red'],split.outputs['Green']
