"""Narrow alpha adaptation for exported Citadel overlay materials.

Blender surface alpha approximates Source's deferred overlay compositing;
independent normal/roughness overlay weights are not reconstructed here.
"""
from pathlib import PurePosixPath
import math


def alpha_ranges(vectors):
    ranges = []
    for name in ('g_vColorTranslucencyInputRange', 'g_vColorTranslucencyOutputRange'):
        raw = vectors.get(name, [0, 1, 0, 0])
        try:
            values = tuple(float(raw[i]) for i in range(2))
        except (TypeError, ValueError, IndexError, KeyError) as error:
            raise ValueError('Malformed overlay alpha range: ' + name) from error
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
            raise ValueError('Unsupported overlay alpha range: ' + name)
        ranges.append(values)
    if abs(ranges[0][1] - ranges[0][0]) < 1e-8:
        raise ValueError('Degenerate overlay alpha input range')
    return ranges


def repair_overlay_alpha(material):
    metadata = material.get('vmat', {})
    if metadata.get('ShaderName') != 'citadel_overlay.vfx':
        return None
    if material.get('agr_overlay_alpha') in ('color_texture_v1', 'color_texture_remap_v2'):
        return None
    vectors = metadata.get('VectorParams', {})
    # Compiled citadel_overlay PS SHA256 01d36d... supplies (0,1) defaults
    # and saturate((a-in.x)/(in.y-in.x))*(out.y-out.x)+out.x.
    input_range, output_range = alpha_ranges(vectors)
    for name in ('g_vAlbedoScrollSpeed', 'g_vAlbedoTexcoordUVOffset'):
        if any(vectors.get(name, [0, 0, 0, 0])):
            raise ValueError(f'Unsupported overlay UV motion/offset: {material.name}')
    source = metadata.get('TextureParams', {}).get('g_tColor')
    if not source or material.node_tree is None:
        raise ValueError(f'Missing overlay color texture: {material.name}')
    tree = material.node_tree
    textures = [n for n in tree.nodes if n.type == 'TEX_IMAGE' and n.image
                and n.image.name == PurePosixPath(source).name]
    shaders = [n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED']
    if len(textures) != 1 or len(shaders) != 1 or shaders[0].inputs['Alpha'].is_linked:
        raise ValueError(f'Unsupported imported overlay graph: {material.name}')
    alpha = textures[0].outputs['Alpha']
    if input_range != (0, 1) or output_range != (0, 1):
        remap = tree.nodes.new('ShaderNodeMapRange')
        remap.label = 'AGR overlay color alpha remap'
        remap.interpolation_type = 'LINEAR'
        remap.clamp = True
        for name, value in zip(('From Min', 'From Max', 'To Min', 'To Max'), (*input_range, *output_range)):
            remap.inputs[name].default_value = value
        tree.links.new(alpha, remap.inputs['Value'])
        alpha = remap.outputs['Result']
    tree.links.new(alpha, shaders[0].inputs['Alpha'])
    material.surface_render_method = 'DITHERED'
    material['agr_overlay_alpha'] = 'color_texture_remap_v2'
    return {'material': material.name, 'source_texture': source,
            'input_range': list(input_range), 'output_range': list(output_range),
            'limits': 'Color texture alpha only; Source instance-tint alpha is not recovered. Deferred normal/roughness compositing remains approximate.'}
