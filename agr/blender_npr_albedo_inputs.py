"""Bind qualified raw converted color to an explicit-input albedo stage.

Caller owns the material copy and independently qualifies image payload, material
identity, render UV and runtime controls. This validates graph shape, not that
an image name proves installed asset identity.
"""
import numpy as np
from agr.npr_material_identity import compiled_resource
from agr.blender_npr_albedo import create_albedo_group


def bind_albedo(material, target, *, expected_image, color_resource, uv_name,
                instance_color, tint_mask, correction_matrix, tint_matrix,
                vertex_strength, apply_tint_to_vertex_colors,
                mask_vertex_tint, mask_color_tint):
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
    nodes,links=material.node_tree.nodes,material.node_tree.links
    if nodes.get('AGR NPR source albedo') is not None:
        raise ValueError('Albedo stage already bound')
    bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED']
    if len(bsdfs)!=1 or len(bsdfs[0].inputs['Base Color'].links)!=1:
        raise ValueError('Expected single converted color input')
    link=bsdfs[0].inputs['Base Color'].links[0];texture=link.from_node
    if texture.type!='TEX_IMAGE' or link.from_socket.name!='Color' or texture.image!=expected_image or expected_image is None:
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
    node.inputs['Tint Mask'].default_value=tint_mask
    links.new(node.outputs['Albedo'],target.inputs['Albedo'])
    return dict(color_resource=compiled_resource(color_resource,'.vtex'),uv_layer=uv_name,
                instance_color=instance.tolist(),tint_mask=float(tint_mask),
                scope='Caller-qualified raw image and explicit matrices; runtime provenance external')
