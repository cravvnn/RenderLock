"""Shared material for separately validated captured sprite components."""
from pathlib import Path
from types import SimpleNamespace
from .observed_slide_profile import require
from .observed_slide_geometry_base import literal

def build_material(renderer, assets, image_cache, index):
    import bpy
    from .blender_images import load_packed_image
    from .blender_simulated_effects import static_texture_sequence
    from .blender_rope_material import rope_color_scale
    from .particle_texture_layers import build_layer_stack, gradient_pixels, LayerNodes, mix
    mat = bpy.data.materials.new('RenderLock native sprite | '+str(index)); mat.use_nodes = True
    if hasattr(mat, 'surface_render_method'): mat.surface_render_method = 'BLENDED'
    nodes, links = mat.node_tree.nodes, mat.node_tree.links; nodes.clear()
    graph = LayerNodes(nodes, links); op = graph.op
    def attr(name, output='Fac'):
        n = nodes.new('ShaderNodeAttribute'); n.attribute_name = name
        return n.outputs[output]
    ratio = attr('AGR Sprite Depth Ratio')
    denominator = graph.vector((ratio, ratio, ratio))
    def divided(value):
        n = nodes.new('ShaderNodeVectorMath'); n.operation = 'DIVIDE'
        links.new(value, n.inputs[0]); links.new(denominator, n.inputs[1]); return n.outputs['Vector']
    def uv(name):
        n = nodes.new('ShaderNodeUVMap'); n.uv_map = name
        return divided(n.outputs['UV'])
    def image(resource, raw=False):
        key = resource, raw
        if key not in image_cache:
            d = assets['textures'][resource]
            if d.get('source_sheet'):
                d = d['source_sheet']; path = Path(d['directory'])/d['image']; expected = d['image_sha256']
            else:
                seq = static_texture_sequence(d, 0, resource)
                require(len(seq['frames']) == 1, 'flat sprite texture unexpectedly animated')
                frame = seq['frames'][0]; path = Path(d['directory'])/frame['image']; expected = frame['sha256']
            value = load_packed_image(str(path), check_existing=False, expected_sha256=expected)
            if raw: value.colorspace_settings.name = 'Non-Color'
            image_cache[key] = value
        return image_cache[key]
    def sample(layer, layer_index, raw=False):
        current = uv(f'AGR Sprite UV {layer_index}')
        first = nodes.new('ShaderNodeTexImage')
        if layer.get('m_bReplaceTextureWithGradient'):
            key = 'gradient', str(layer.get('m_Gradient', {}))
            if key not in image_cache:
                value = bpy.data.images.new('RenderLock sprite gradient', width=256, height=1, float_buffer=True)
                value.colorspace_settings.name = 'Non-Color'; value.alpha_mode = 'STRAIGHT'
                value.pixels.foreach_set(gradient_pixels(layer.get('m_Gradient', {}))); value.pack(); image_cache[key] = value
            first.image = image_cache[key]; first.extension = 'EXTEND'
        else:
            first.image = image(layer['m_hTexture'], raw); first.extension = 'REPEAT'
        links.new(current, first.inputs['Vector'])
        descriptor = assets['textures'].get(layer.get('m_hTexture'), {})
        if descriptor.get('source_sheet'):
            next_uv = uv(f'AGR Sprite Next UV {layer_index}')
            second = nodes.new('ShaderNodeTexImage'); second.image = first.image; second.extension = first.extension
            links.new(next_uv, second.inputs['Vector'])
            fraction = attr(f'AGR Sprite Blend {layer_index}')
            rgb = nodes.new('ShaderNodeMixRGB'); links.new(fraction, rgb.inputs[0])
            links.new(first.outputs['Color'], rgb.inputs[1]); links.new(second.outputs['Color'], rgb.inputs[2])
            alpha = mix(first.outputs['Alpha'], second.outputs['Alpha'], fraction, op)
            return SimpleNamespace(outputs={'Color': rgb.outputs[0], 'Alpha': alpha}, animated=True,
                                   distortion_source=first), current
        return first, current
    def parameter(value, default): return literal({'value': value}, 'value', default)
    def particle_input(mode):
        require(mode == 'SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1', 'unqualified sprite texture input')
        return attr('AGR Sprite Extra1')
    color, alpha = build_layer_stack(nodes, links, renderer['m_vecTexturesInput'], sample, parameter,
        particle_input=particle_input, uv_y_sign=-1)
    source_color = divided(attr('AGR Sprite Depth RGB', 'Vector'))
    source_alpha = op('DIVIDE', attr('AGR Sprite Depth Alpha'), ratio)
    mult = nodes.new('ShaderNodeVectorMath'); mult.operation = 'MULTIPLY'
    links.new(source_color, mult.inputs[0]); links.new(color, mult.inputs[1]); color = mult.outputs['Vector']
    alpha = op('MULTIPLY', alpha, source_alpha)
    # Retain the existing explicit spritecard material study's neutral scene
    # light. Actual Source probes/light/fog/map interactions remain unresolved.
    factor = literal(renderer, 'm_flOverbrightFactor', 1)*(literal(renderer, 'm_flSelfIllumAmount', 0)+literal(renderer, 'm_flDiffuseAmount', 1))
    tint = rope_color_scale(renderer)
    def vector(operation, value, constant):
        n = nodes.new('ShaderNodeVectorMath'); n.operation = operation
        links.new(value, n.inputs[0]); n.inputs[1].default_value = constant; return n.outputs['Vector']
    color = vector('MULTIPLY', color, tuple(factor*t for t in tint))
    if renderer.get('m_bSaturateColorPreAlphaBlend', True):
        color = vector('MAXIMUM', color, (0., 0., 0.)); color = vector('MINIMUM', color, (1., 1., 1.))
    color = vector('MULTIPLY', color, (1+literal(renderer, 'm_flAddSelfAmount', 0),)*3)
    from .blender_particle_blend import build_output_shader
    blend = renderer.get('m_nOutputBlendMode', 'PARTICLE_OUTPUT_BLEND_MODE_ALPHA')
    shader = build_output_shader(nodes, links, color, alpha, blend)
    output = nodes.new('ShaderNodeOutputMaterial'); links.new(shader, output.inputs['Surface'])
    mat['agr_observed_sprite_material'] = 'native_manual_atlas_inputs_v1'
    mat['agr_texture_uv_basis'] = 'Source top-left converted after atlas; distortion Y reflected'
    mat['agr_lighting_scope'] = 'Neutral explicit scene-light material study; Source lighting/fog not reconstructed'
    return mat
