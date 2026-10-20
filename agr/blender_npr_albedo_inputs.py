"""Bind qualified raw converted color to an explicit-input albedo stage.

Caller owns the material copy and independently qualifies image payload, material
identity, render UV and runtime controls. This validates graph shape, not that
an image name proves installed asset identity.
"""
import numpy as np
from agr.npr_material_identity import compiled_resource
from agr.blender_npr_albedo import create_albedo_group


def raw_albedo_input(material):
    """Resolve the typed glTF raw texture and optional vertex-color multiply.

    Unknown factors/layers remain errors. Static converted mesh vertex colors
    are separate from the explicit white runtime instance-color assumption.
    """
    bsdfs=[n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED']
    if len(bsdfs)!=1 or len(bsdfs[0].inputs['Base Color'].links)!=1:
        raise ValueError('Expected one converted albedo input')
    link=bsdfs[0].inputs['Base Color'].links[0];source=link.from_node
    if source.type=='TEX_IMAGE' and link.from_socket.name=='Color':return source,None
    if (source.type!='MIX' or source.data_type!='RGBA' or source.blend_type!='MULTIPLY'
            or source.inputs[0].is_linked or source.inputs[0].default_value!=1
            or source.clamp_result):
        raise ValueError('Unqualified converted albedo factors/layers')
    inputs=[s for s in source.inputs if s.enabled and s.name in ('A','B')]
    if len(inputs)!=2 or any(len(s.links)!=1 for s in inputs):
        raise ValueError('Expected exact texture/vertex albedo product')
    by_type={s.links[0].from_node.type:s.links[0] for s in inputs}
    if (set(by_type)!={'TEX_IMAGE','VERTEX_COLOR'}
            or any(link.from_socket.name!='Color' for link in by_type.values())):
        raise ValueError('Expected exact raw texture and static vertex colors')
    return by_type['TEX_IMAGE'].from_node,by_type['VERTEX_COLOR'].from_socket


def bind_albedo(material, target, *, expected_image, color_resource, uv_name,
                instance_color, tint_mask, correction_matrix, tint_matrix,
                vertex_strength, apply_tint_to_vertex_colors,
                mask_vertex_tint, mask_color_tint, mask_texture=None):
    if target.id_data != material.node_tree or 'Albedo' not in target.inputs:
        raise ValueError('Expected caller-owned NPR target')
    if not isinstance(uv_name,str) or not uv_name:
        raise ValueError('Explicit qualified render UV required')
    actual=material.get('vmat',{}).get('TextureParams',{}).get('g_tColor')
    if compiled_resource(actual,'.vtex') != compiled_resource(color_resource,'.vtex'):
        raise ValueError('Source color resource mismatch')
    instance=np.asarray(instance_color,dtype=float)
    if instance.shape!=(3,) or not np.isfinite(instance).all() or np.any(instance<0):
        raise ValueError('Explicit finite linear instance color required')
    if np.ndim(tint_mask) or not np.isfinite(tint_mask) or not 0<=tint_mask<=1:
        raise ValueError('Qualified constant tint mask required')
    if mask_texture is not None:
        from .blender_npr_masks import validate_mask_texture
        validate_mask_texture(mask_texture)
    nodes,links=material.node_tree.nodes,material.node_tree.links
    if nodes.get('AGR NPR source albedo') is not None:
        raise ValueError('Albedo stage already bound')
    bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED']
    if len(bsdfs)!=1 or len(bsdfs[0].inputs['Base Color'].links)!=1:
        raise ValueError('Expected single converted color input')
    texture,vertex=raw_albedo_input(material)
    if texture.image!=expected_image or expected_image is None:
        raise ValueError('Expected qualified raw color image, without converted factors')
    if expected_image.colorspace_settings.name!='sRGB' or expected_image.alpha_mode!='CHANNEL_PACKED' or texture.extension!='REPEAT' or texture.interpolation!='Linear':
        raise ValueError('Unsupported raw color sampling state')
    if texture.inputs['Vector'].is_linked:
        uv=texture.inputs['Vector'].links[0].from_node
        if uv.type!='UVMAP' or uv.uv_map!=uv_name:
            raise ValueError('Unsupported converted UV transform')
    tree=create_albedo_group(correction_matrix=correction_matrix,tint_matrix=tint_matrix,
        vertex_strength=vertex_strength,apply_tint_to_vertex_colors=apply_tint_to_vertex_colors,
        mask_vertex_tint=mask_vertex_tint,mask_color_tint=mask_color_tint)
    node=nodes.new('ShaderNodeGroup');node.node_tree=tree;node.name='AGR NPR source albedo'
    if not texture.inputs['Vector'].is_linked:
        uv=nodes.new('ShaderNodeUVMap');uv.uv_map=uv_name;links.new(uv.outputs['UV'],texture.inputs['Vector'])
    links.new(texture.outputs['Color'],node.inputs['Sampled Color'])
    node.inputs['Instance Color'].default_value=instance
    if vertex is not None:
        links.new(vertex,node.inputs['Instance Color'])
    if mask_texture is None:
        node.inputs['Tint Mask'].default_value=tint_mask
    else:
        from .blender_npr_masks import bind_mask_texture
        red,_=bind_mask_texture(material,mask_texture,uv_name=uv_name)
        links.new(red,node.inputs['Tint Mask'])
    links.new(node.outputs['Albedo'],target.inputs['Albedo'])
    return dict(color_resource=compiled_resource(color_resource,'.vtex'),uv_layer=uv_name,
                instance_color=instance.tolist(),
                converted_vertex_colors=vertex is not None,
                tint_mask=(dict(sha256=mask_texture['sha256'],channel='R',size=mask_texture['size'])
                           if mask_texture is not None else float(tint_mask)),
                scope='Caller-qualified raw image and explicit matrices; runtime provenance external')
