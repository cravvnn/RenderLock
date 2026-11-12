"""Measured per-renderer slide sprite inputs; no simulation or lifetime state.

The complete-reference/profile validator owns source admission. This adapter
keeps native collection-random scales as measured values and original texture
indices, and uses native manual frame input. Fixed/FIT animation is admitted
only for single-frame sheets, where no animation-time conversion is needed.
"""
import copy
import math
import struct
import numpy as np
from .observed_slide_geometry_base import _build_geometry, require, literal
from .particle_texture_layers import enabled_texture_layers

MODES = ('ANIMATION_TYPE_FIXED_RATE', 'ANIMATION_TYPE_FIT_LIFETIME', 'ANIMATION_TYPE_MANUAL_FRAMES')


def captured_renderer(renderer, inputs):
    require(renderer.get('_class') == inputs.get('kind') == 'C_OP_RenderSprites', 'captured renderer type differs')
    require(renderer.get('m_nOrientationType', 'PARTICLE_ORIENTATION_SCREEN_ALIGNED') == 'PARTICLE_ORIENTATION_SCREEN_ALIGNED',
            'captured sprite orientation differs')
    require(renderer.get('m_bBlendFramesSeq0', True) is True
            and not renderer.get('m_bMaxLuminanceBlendingSequence0'), 'captured sheet blend differs')
    try: rate, mode = struct.unpack('<fi', bytes.fromhex(inputs['animation_raw']))
    except (KeyError, ValueError, struct.error) as exc: raise ValueError('Captured sprite animation input unavailable') from exc
    require(mode in range(3) and renderer.get('m_nAnimationType', MODES[0]) == MODES[mode]
            and math.isfinite(rate) and rate == float(np.float32(literal(renderer, 'm_flAnimationRate', .1))),
            'captured animation mode/rate differs')
    effective = copy.deepcopy(renderer)
    for key, name, default in (('radius_scale', 'm_flRadiusScale', 1), ('alpha_scale', 'm_flAlphaScale', 1), ('depth_bias', 'm_flDepthBias', 0)):
        value = inputs.get(key, {})
        require(value.get('state') == 'literal' and type(value.get('value')) in (float, int)
                and math.isfinite(value['value']) and abs(value['value']) <= 1e6
                and value['value'] == float(np.float32(literal(renderer, name, default))), 'captured literal renderer value differs')
        effective[name] = value['value']
    require(inputs.get('gamma_raw') in ('00', '01')
            and (inputs['gamma_raw'] == '01') == renderer.get('m_bGammaCorrectVertexColors', True), 'captured gamma flag differs')
    layers = enabled_texture_layers(renderer.get('m_vecTexturesInput', []))
    native = inputs.get('texture_layers')
    require(isinstance(native, list) and len(native) == len(layers), 'captured texture coverage differs')
    for accepted, ((index, _), layer) in enumerate(zip(layers, native)):
        require(type(layer.get('accepted_index')) is int and layer['accepted_index'] == accepted
                and type(layer.get('source_index')) is int and layer['source_index'] == index, 'captured original texture association differs')
        controls = effective['m_vecTexturesInput'][index].setdefault('m_TextureControls', {})
        for axis in ('u', 'v'):
            value = layer.get('scale_'+axis, {})
            require(value.get('state') in ('literal', 'constant_random') and type(value.get('value')) in (float, int)
                    and math.isfinite(value['value']) and 1e-12 < abs(value['value']) <= 1e6, 'captured texture scale unavailable')
            controls['m_flFinalTextureScale'+axis.upper()] = value['value']
    return effective, mode


def build_captured_frame(row, renderer, textures, camera):
    effective, mode = captured_renderer(renderer, row['native_inputs'])
    for _, layer in enabled_texture_layers(renderer.get('m_vecTexturesInput', [])):
        sheet = textures.get(layer.get('m_hTexture'), {}).get('source_sheet')
        require(mode == 2 or not sheet or all(len(s['frames']) == 1 for s in sheet['sequences']),
                'animated fixed/FIT atlas needs separate native timing qualification')
    points = []
    for p in row['points']:
        sequence = p['sequence']
        require(isinstance(sequence, list) and len(sequence) == 2
                and type(sequence[0]) is int and 0 <= sequence[0] <= 32767, 'captured sprite sequence invalid')
        q = {k: p[k] for k in ('position', 'radius', 'rotation', 'color_bytes', 'shader_extra_data1', 'native_random')}
        q['sequence'] = sequence[0]
        if mode == 2: q['manual_frame'] = p['native_animation_input']
        points.append(q)
    return _build_geometry(dict(points=points, collection_time=row['collection_time']), effective, textures, camera)
