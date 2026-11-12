"""Textured rope materials following the pinned VRF spritecard shader.

Explicit supplied lighting is not recovered Source scene lighting. Simulation,
camera sampling and the remaining depth/per-particle shader inputs are separate.
"""
import math
import numpy as np

from .installed_effects import scalar, UnsupportedEffect
from .blender_simulated_effects import build_texture_uv_nodes
from .particle_texture_channels import texture_channel_mode
from .particle_texture_layers import enabled_texture_layers,build_layer_stack,gradient_pixels


def rope_color_scale(renderer):
    """Literal vector or byte-color input with native float32 color normalization."""
    value = renderer.get('m_vecColorScale')
    if value is None:
        return (1., 1., 1.)
    if not isinstance(value, dict):
        raise UnsupportedEffect('Rope color scale requires an explicit vector provider')
    kind = value.get('m_nType', 'PVEC_TYPE_LITERAL')
    if kind == 'PVEC_TYPE_LITERAL_COLOR':
        rgb = value.get('m_LiteralColor')
        if (not isinstance(rgb, (list, tuple)) or len(rgb) not in (3, 4)
                or any(type(v) is not int or not 0 <= v <= 255 for v in rgb)):
            raise UnsupportedEffect('Invalid rope literal byte color')
        return tuple(float(np.float32(v)*np.float32(1/255)) for v in rgb[:3])
    if kind != 'PVEC_TYPE_LITERAL':
        raise UnsupportedEffect('Rope color-scale provider '+str(kind))
    rgb = value.get('m_vLiteralValue')
    if (not isinstance(rgb, (list, tuple)) or len(rgb) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in rgb)):
        raise UnsupportedEffect('Invalid rope literal vector')
    with np.errstate(over='ignore'):
        values = np.asarray(rgb, dtype=np.float32)
    if not np.isfinite(values).all():
        raise UnsupportedEffect('Rope color scale exceeds float32 range')
    return tuple(float(v) for v in values)


def prepare_rope_shader_colors(mesh, renderer):
    """Decode gamma per vertex, before raster interpolation, as the vertex shader does."""
    source = mesh.color_attributes.get('AGR Rope Color')
    if source is None or source.domain != 'CORNER':
        raise UnsupportedEffect('Rope vertex colors are missing')
    tint = rope_color_scale(renderer)
    target = mesh.color_attributes.get('AGR Rope Shader Color')
    if target is None:
        target = mesh.color_attributes.new(name='AGR Rope Shader Color', type='FLOAT_COLOR', domain='CORNER')
    if target.domain != 'CORNER' or target.data_type != 'FLOAT_COLOR':
        raise UnsupportedEffect('Incompatible rope shader color attribute')
    gamma = renderer.get('m_bGammaCorrectVertexColors', True)
    ratios=mesh.attributes.get('AGR Rope Depth Ratio')
    weighted_rgb=weighted_alpha=None
    if ratios is not None:
        if ratios.domain!='POINT'or ratios.data_type!='FLOAT'or len(ratios.data)!=len(mesh.vertices):
            raise UnsupportedEffect('Invalid rope depth interpolation attribute')
        for value in ratios.data:
            if not math.isfinite(value.value)or value.value<=0:raise UnsupportedEffect('Invalid rope depth interpolation weight')
        weighted_rgb=mesh.attributes.get('AGR Rope Depth RGB')or mesh.attributes.new(name='AGR Rope Depth RGB',type='FLOAT_VECTOR',domain='CORNER')
        weighted_alpha=mesh.attributes.get('AGR Rope Depth Alpha')or mesh.attributes.new(name='AGR Rope Depth Alpha',type='FLOAT',domain='CORNER')
        if weighted_rgb.domain!='CORNER'or weighted_rgb.data_type!='FLOAT_VECTOR'or weighted_alpha.domain!='CORNER'or weighted_alpha.data_type!='FLOAT':
            raise UnsupportedEffect('Invalid weighted rope shader attributes')
    for index,(src, dst) in enumerate(zip(source.data, target.data)):
        rgb = list(src.color[:3])
        if gamma:
            rgb = [c/12.92 if c <= .04045 else (c/1.055+.0521327)**2.4 for c in rgb]
        dst.color = (*(c*t for c, t in zip(rgb, tint)), src.color[3])
        if ratios is not None:
            factor=ratios.data[mesh.loops[index].vertex_index].value
            weighted_rgb.data[index].vector=[c*t*factor for c,t in zip(rgb,tint)]
            weighted_alpha.data[index].value=src.color[3]*factor


def build_rope_material(renderer, images, *, scene_light, age=0., name='AGR textured rope study',
                        mesh=None,depth_compensated=False,render_context=None,native_inputs=None):
    """Return (material, limitations), consuming already loaded static texture images.

    Lighting is ambient+direct RGB in the shader's linear space. The caller must
    retain limitations in its report; this function does not claim engine parity.
    """
    import bpy
    from .particle_rope_shader_inputs import validate_inputs
    qualified=native_inputs is not None
    if qualified:validate_inputs(native_inputs)
    if renderer.get('m_bDrawAsOpaque') or renderer.get('m_bOnlyRenderInEffectsWaterPass'):
        raise UnsupportedEffect('Opaque/water-pass rope output is not implemented')
    if len(scene_light) != 3 or any(not math.isfinite(x) or x < 0 for x in scene_light):
        raise UnsupportedEffect('Rope study lighting must be finite nonnegative RGB')
    blend = renderer.get('m_nOutputBlendMode', 'PARTICLE_OUTPUT_BLEND_MODE_ALPHA')
    if blend not in ('PARTICLE_OUTPUT_BLEND_MODE_ALPHA', 'PARTICLE_OUTPUT_BLEND_MODE_ADD')and not(
            qualified and blend=='PARTICLE_OUTPUT_BLEND_MODE_BLEND_ADD'):
        raise UnsupportedEffect('Unsupported rope output blend')
    for field in ('m_flDesaturation', 'm_flFeatheringMinDist', 'm_flFeatheringMaxDist'):
        if scalar(renderer.get(field), default=0) != 0:
            raise UnsupportedEffect('Unsupported rope shader control: '+field)
    if (renderer.get('m_bOutline') or renderer.get('m_nHSVShiftControlPoint', -1) != -1
            or renderer.get('m_nHsvShiftControlPoint', -1) != -1):
        raise UnsupportedEffect('Rope outline/HSV shader input is not implemented')
    if scalar(renderer.get('m_flDepthBias'), default=0) != 0 and not(qualified and depth_compensated):
        raise UnsupportedEffect('Rope depth-bias shader input is not implemented')
    layers = renderer.get('m_vecTexturesInput', [])
    if not layers:
        raise UnsupportedEffect('Rope material requires explicit texture layers')
    for _,layer in enabled_texture_layers(layers):
        texture_channel_mode(layer)
        if not qualified and scalar(layer.get('m_flTextureBlend'), default=1) != 1:
            raise UnsupportedEffect('Rope partial texture blending is not qualified')
        if not qualified and (layer.get('m_nTextureType', 'SPRITECARD_TEXTURE_COLOR') != 'SPRITECARD_TEXTURE_COLOR' or layer.get('m_nTextureBlendMode', 'SPRITECARD_TEXTURE_BLEND_MULTIPLY') != 'SPRITECARD_TEXTURE_BLEND_MULTIPLY'):
            raise UnsupportedEffect('Rope material currently requires multiplied color layers')
        if layer.get('m_TextureControls', {}).get('m_bRandomizeOffsets')and not qualified:
            raise UnsupportedEffect('Rope random texture offsets require qualified segment inputs')
        controls=layer.get('m_TextureControls',{})
        if any(controls.get(k,'SPRITECARD_TEXTURE_PP_SCALE_NONE')!='SPRITECARD_TEXTURE_PP_SCALE_NONE'
            for k in ('m_nPerParticleDistortion','m_nPerParticleScale')):
            raise UnsupportedEffect('Per-particle rope texture controls require separately captured shader extras')
        if layer.get('m_TextureControls', {}).get('m_bClampUVs'):
            raise UnsupportedEffect('Rope clamped UVs require per-vertex texture placement')
        if not layer.get('m_bReplaceTextureWithGradient') and layer.get('m_hTexture') not in images:
            raise UnsupportedEffect('Missing rope texture image: '+str(layer.get('m_hTexture')))
    values = {key: scalar(renderer.get(key), default=default) for key, default in (
        ('m_flSelfIllumAmount', 0), ('m_flDiffuseAmount', 1), ('m_flOverbrightFactor', 1),
        ('m_flAddSelfAmount', 0), ('m_flSourceAlphaValueToMapToZero', 0),
        ('m_flSourceAlphaValueToMapToOne', 1))}
    limitations = ['Lighting supplied explicitly; Source scene lighting and fog are not reconstructed.']
    for key in ('m_nPerParticleAlphaReference', 'm_nPerParticleAlphaRef'):
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
    vertex_alpha=vertex.outputs['Alpha'];coordinate=uv.outputs['UV']
    if depth_compensated:
        if not qualified or mesh is None or any(mesh.attributes.get(name)is None for name in(
            'AGR Rope Depth Ratio','AGR Rope Depth RGB','AGR Rope Depth Alpha')):
            raise UnsupportedEffect('Depth compensation requires qualified complete rope attributes')
        weight=nodes.new('ShaderNodeAttribute');weight.attribute_name='AGR Rope Depth Ratio'
        denominator=nodes.new('ShaderNodeCombineXYZ')
        for socket in denominator.inputs:links.new(weight.outputs['Fac'],socket)
        rgb=nodes.new('ShaderNodeAttribute');rgb.attribute_name='AGR Rope Depth RGB'
        weighted_alpha=nodes.new('ShaderNodeAttribute');weighted_alpha.attribute_name='AGR Rope Depth Alpha'
        color=vector_node('DIVIDE',rgb.outputs['Vector'],denominator.outputs[0])
        vertex_alpha=math_node('DIVIDE',weighted_alpha.outputs['Fac'],weight.outputs['Fac'])
        coordinate=vector_node('DIVIDE',coordinate,denominator.outputs[0])
        mat['agr_depth_bias_interpolation']='original_clip_w_compensation_v1'
    if qualified:
        mat['agr_rope_native_shader_policy']=native_inputs['policy'];mat['agr_rope_uv_random_seed']=0.
        limitations.append('Rope UV inputs follow the qualified zero-seed native segment profile; GPU filtering and Source framebuffer/lighting equivalence remain approximate.')
    def sample_layer(layer,index,raw=False):
        tex = nodes.new('ShaderNodeTexImage')
        if layer.get('m_bReplaceTextureWithGradient'):
            image=bpy.data.images.new('AGR rope gradient',width=256,height=1,float_buffer=True)
            image.colorspace_settings.name='Non-Color';image.alpha_mode='STRAIGHT'
            image.pixels.foreach_set(gradient_pixels(layer.get('m_Gradient',{})));image.pack()
            tex.image=image;tex.extension='EXTEND'
        else:
            tex.image=images[layer['m_hTexture']];tex.image.pack();tex.extension='REPEAT'
        if raw:
            tex.image=tex.image.copy();tex.image.colorspace_settings.name='Non-Color';tex.image.pack()
        controls=layer.get('m_TextureControls',{})
        if qualified:
            from .particle_render_controls import render_scalar
            controls={key:(render_scalar(value,age=age,default=1 if 'Scale'in key else 0,context=render_context)
                if key.startswith('m_flFinalTexture')else value)for key,value in controls.items()}
        mapping = build_texture_uv_nodes(nodes, links, coordinate, controls, [(1, age, (0., 0.))])
        links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
        return tex,mapping.outputs['Vector']
    from .particle_render_controls import render_scalar
    layer_color,alpha=build_layer_stack(nodes,links,layers,sample_layer,
        lambda value,default:render_scalar(value,age=age,default=default,context=render_context))
    color=vector_node('MULTIPLY',color,layer_color)
    low, high = (values['m_flSourceAlphaValueToMapTo'+word] for word in ('Zero', 'One'))
    if low < high:
        remap = nodes.new('ShaderNodeMapRange');remap.interpolation_type = 'SMOOTHSTEP';remap.clamp = True
        remap.inputs['From Min'].default_value = low;remap.inputs['From Max'].default_value = high
        links.new(alpha, remap.inputs['Value']);alpha = remap.outputs['Result']
    alpha = math_node('MULTIPLY', alpha, vertex_alpha)
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
    elif blend=='PARTICLE_OUTPUT_BLEND_MODE_BLEND_ADD':
        # Native parameter -1 keeps emitted RGB independent of alpha while
        # alpha still attenuates the destination. Avoid dividing by alpha: its
        # zero endpoint intentionally retains the additive contribution.
        links.new(math_node('SUBTRACT',1,alpha),transparent.inputs['Color'])
        shader=nodes.new('ShaderNodeAddShader')
        links.new(transparent.outputs[0],shader.inputs[0]);links.new(emission.outputs[0],shader.inputs[1])
        mat['agr_rope_output_blend']='premultiplied_blend_add_approximation_v1'
    else:
        shader = nodes.new('ShaderNodeMixShader');links.new(alpha, shader.inputs[0])
        links.new(transparent.outputs[0], shader.inputs[1]);links.new(emission.outputs[0], shader.inputs[2])
    output = nodes.new('ShaderNodeOutputMaterial');links.new(shader.outputs[0], output.inputs['Surface'])
    return mat, limitations
