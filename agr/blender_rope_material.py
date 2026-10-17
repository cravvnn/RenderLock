"""Textured rope study materials following the pinned VRF spritecard shader.

Explicit supplied lighting is a study input, not recovered Source scene lighting.
This stage remains separate from normal replay export until simulation and the
remaining depth/per-particle shader inputs are supported.
"""
import math

from .installed_effects import scalar, UnsupportedEffect
from .blender_simulated_effects import build_texture_uv_nodes


def prepare_rope_shader_colors(mesh, renderer):
    """Decode gamma per vertex, before raster interpolation, as the vertex shader does."""
    source = mesh.color_attributes.get('AGR Rope Color')
    if source is None or source.domain != 'CORNER':
        raise UnsupportedEffect('Rope vertex colors are missing')
    if renderer.get('m_vecColorScale'):
        raise UnsupportedEffect('Rope color-scale provider is not implemented')
    target = mesh.color_attributes.get('AGR Rope Shader Color')
    if target is None:
        target = mesh.color_attributes.new(name='AGR Rope Shader Color', type='FLOAT_COLOR', domain='CORNER')
    if target.domain != 'CORNER' or target.data_type != 'FLOAT_COLOR':
        raise UnsupportedEffect('Incompatible rope shader color attribute')
    gamma = renderer.get('m_bGammaCorrectVertexColors', True)
    for src, dst in zip(source.data, target.data):
        rgb = list(src.color[:3])
        if gamma:
            rgb = [c/12.92 if c <= .04045 else (c/1.055+.0521327)**2.4 for c in rgb]
        dst.color = (*rgb, src.color[3])


def build_rope_material(renderer, images, *, scene_light, age=0., name='AGR textured rope study'):
    """Return (material, limitations), consuming already loaded static texture images.

    Lighting is ambient+direct RGB in the shader's linear space. The caller must
    retain limitations in its report; this function does not claim engine parity.
    """
    import bpy
    if len(scene_light) != 3 or any(not math.isfinite(x) or x < 0 for x in scene_light):
        raise UnsupportedEffect('Rope study lighting must be finite nonnegative RGB')
    blend = renderer.get('m_nOutputBlendMode', 'PARTICLE_OUTPUT_BLEND_MODE_ALPHA')
    if blend not in ('PARTICLE_OUTPUT_BLEND_MODE_ALPHA', 'PARTICLE_OUTPUT_BLEND_MODE_ADD'):
        raise UnsupportedEffect('Unsupported rope output blend')
    for field in ('m_flDesaturation', 'm_flFeatheringMinDist', 'm_flFeatheringMaxDist'):
        if scalar(renderer.get(field), default=0) != 0:
            raise UnsupportedEffect('Unsupported rope shader control: '+field)
    if renderer.get('m_bOutline') or renderer.get('m_nHsvShiftControlPoint', -1) != -1:
        raise UnsupportedEffect('Rope outline/HSV shader input is not implemented')
    layers = [layer for layer in renderer.get('m_vecTexturesInput', []) if layer.get('m_hTexture')]
    if not layers:
        raise UnsupportedEffect('Rope material requires explicit texture layers')
    for layer in layers:
        if layer.get('m_nTextureChannels', 'SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA') != 'SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA' or scalar(layer.get('m_flTextureBlend'), default=1) != 1:
            raise UnsupportedEffect('Rope texture channel masks/partial blending are not implemented')
        if layer.get('m_nTextureType', 'SPRITECARD_TEXTURE_COLOR') != 'SPRITECARD_TEXTURE_COLOR' or layer.get('m_nTextureBlendMode', 'SPRITECARD_TEXTURE_BLEND_MULTIPLY') != 'SPRITECARD_TEXTURE_BLEND_MULTIPLY':
            raise UnsupportedEffect('Rope material currently requires multiplied color layers')
        if layer.get('m_bReplaceTextureWithGradient') or layer.get('m_TextureControls', {}).get('m_bRandomizeOffsets'):
            raise UnsupportedEffect('Rope gradient/random texture mapping is not implemented')
        if layer['m_hTexture'] not in images:
            raise UnsupportedEffect('Missing rope texture image: '+layer['m_hTexture'])
    values = {key: scalar(renderer.get(key), default=default) for key, default in (
        ('m_flSelfIllumAmount', 0), ('m_flDiffuseAmount', 1), ('m_flOverbrightFactor', 1),
        ('m_flAddSelfAmount', 0), ('m_flSourceAlphaValueToMapToZero', 0),
        ('m_flSourceAlphaValueToMapToOne', 1))}
    limitations = ['Lighting supplied explicitly; Source scene lighting and fog are not reconstructed.']
    for key in ('m_nPerParticleAlphaReference', 'm_nPerParticleAlphaRef', 'm_flDepthBias'):
        if key in renderer:
            limitations.append(key+' is not represented by this study material.')
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
    mat['agr_rope_material_study'] = True
    mat['agr_shader_reference'] = 'VRF 42743fb93030c552f834fabb30708dad84d0cbf5 particle_spritecard'
    mat['agr_material_limitations'] = '\n'.join(limitations)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()

    def math_node(operation, left, right):
        node = nodes.new('ShaderNodeMath');node.operation = operation
        for index, value in enumerate((left, right)):
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]

    def vector_node(operation, left, right):
        node = nodes.new('ShaderNodeVectorMath');node.operation = operation
        links.new(left, node.inputs[0])
        if isinstance(right, (list, tuple)):
            node.inputs[1].default_value = right
        else:
            links.new(right, node.inputs[1])
        return node.outputs['Vector']

    vertex = nodes.new('ShaderNodeVertexColor');vertex.layer_name = 'AGR Rope Shader Color'
    uv = nodes.new('ShaderNodeTexCoord')
    color = vertex.outputs['Color']
    alpha = 1.
    for layer in layers:
        tex = nodes.new('ShaderNodeTexImage');tex.image = images[layer['m_hTexture']]
        tex.image.pack();tex.extension = 'REPEAT'
        mapping = build_texture_uv_nodes(nodes, links, uv.outputs['UV'], layer.get('m_TextureControls', {}), [(1, age, (0., 0.))])
        links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
        color = vector_node('MULTIPLY', color, tex.outputs['Color'])
        alpha = math_node('MULTIPLY', alpha, tex.outputs['Alpha'])
    low, high = (values['m_flSourceAlphaValueToMapTo'+word] for word in ('Zero', 'One'))
    if low < high:
        remap = nodes.new('ShaderNodeMapRange');remap.interpolation_type = 'SMOOTHSTEP';remap.clamp = True
        remap.inputs['From Min'].default_value = low;remap.inputs['From Max'].default_value = high
        links.new(alpha, remap.inputs['Value']);alpha = remap.outputs['Result']
    alpha = math_node('MULTIPLY', alpha, vertex.outputs['Alpha'])
    factor = [values['m_flOverbrightFactor'] * (values['m_flSelfIllumAmount'] + values['m_flDiffuseAmount']*x) for x in scene_light]
    color = vector_node('MULTIPLY', color, factor)
    if renderer.get('m_bSaturateColorPreAlphaBlend', True):
        color = vector_node('MAXIMUM', color, (0., 0., 0.))
        color = vector_node('MINIMUM', color, (1., 1., 1.))
    add_self = 1+values['m_flAddSelfAmount']
    color = vector_node('MULTIPLY', color, (add_self,)*3)
    emission = nodes.new('ShaderNodeEmission');links.new(color, emission.inputs['Color'])
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    if blend == 'PARTICLE_OUTPUT_BLEND_MODE_ADD':
        links.new(alpha, emission.inputs['Strength'])
        shader = nodes.new('ShaderNodeAddShader')
        links.new(transparent.outputs[0], shader.inputs[0]);links.new(emission.outputs[0], shader.inputs[1])
    else:
        shader = nodes.new('ShaderNodeMixShader');links.new(alpha, shader.inputs[0])
        links.new(transparent.outputs[0], shader.inputs[1]);links.new(emission.outputs[0], shader.inputs[2])
    output = nodes.new('ShaderNodeOutputMaterial');links.new(shader.outputs[0], output.inputs['Surface'])
    return mat, limitations
