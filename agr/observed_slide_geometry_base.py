"""Stateless camera-facing quads and native spritecard UV inputs.

Only separately hash-pinned captured slide/ambient profiles are admitted by the package
validators. This module has no particle identities, clocks, caches or handlers.
Native shader geometry/UV equations are used; actual GPU filtering, constant
buffer upload, clipping floor and Source lighting remain separate qualification.
"""
import math
import numpy as np
from .installed_effects import scalar, UnsupportedEffect
from .blender_simulated_effects import texture_control_value
from .particle_texture_animation import validate_sheet, sheet_frame
from .particle_texture_layers import enabled_texture_layers, ROTATION_INPUTS
from .particle_depth_bias import projective_depth_bias

F = np.float32
CORNERS = ((-1., -1.), (1., -1.), (-1., 1.), (1., 1.))
FACES = ((0, 1, 2), (1, 3, 2))


def require(value, message):
    if not value:
        raise UnsupportedEffect('Observed sprite geometry: '+message)


def literal(renderer, name, default):
    return scalar(renderer.get(name), default=default)


def random_offsets(value, index):
    """Installed VS ordinary layer1, with the measured native shader float."""
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1,
            'native random float missing')
    require(index == 1, 'ordinary random offsets only qualified for layer1')
    return tuple(float(v-F(np.floor(v))) for v in (
        F(F(value)*F(51.483928680419921875)), F(F(value)*F(38.105518341064453125))))


def texture_corners(controls, time, point, index, *, sheet=False):
    """Source card XY -> top-left texture UV; convert to Blender only after atlas.

    Source's upward card Y becomes downward image V. Keeping the explicit basis
    avoids reversing native collection-time scroll or flat random offsets.
    """
    mode = controls.get('m_nPerParticleScale', 'SPRITECARD_TEXTURE_PP_SCALE_NONE')
    require(mode in ('SPRITECARD_TEXTURE_PP_SCALE_NONE', 'SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1', *ROTATION_INPUTS),
            'unqualified per-particle texture scale')
    factor = (point['rotation'][ROTATION_INPUTS[mode]] if mode in ROTATION_INPUTS
              else point['shader_extra_data1'] if mode.endswith('DATA1') else 1.)
    scale = [F(F(texture_control_value(controls.get('m_flFinalTextureScale'+a), time, 1))*F(factor))
             for a in ('U', 'V')]
    require(all(math.isfinite(v) and abs(v) > 1e-12 for v in scale), 'zero/nonfinite texture scale')
    rnd = random_offsets(point['native_random'], index) if controls.get('m_bRandomizeOffsets') and not sheet else (0., 0.)
    origin = []
    for axis, random in zip(('U', 'V'), rnd):
        value = F(F(F(texture_control_value(controls.get('m_flFinalTextureOffset'+axis), time, 0))+F(random))+F(.5))
        origin.append(F(value-F(np.floor(value))))
    angle = F(texture_control_value(controls.get('m_flFinalTextureUVRotation'), time, 0))
    c, s = F(np.cos(angle)), F(np.sin(angle))
    result = []
    for x, y in CORNERS:
        u, v = F(x*.5), F(-y*.5)
        u, v = F(F(F(u*c)-F(v*s))/scale[0]+origin[0]), F(F(F(u*s)+F(v*c))/scale[1]+origin[1])
        if controls.get('m_bClampUVs'):
            u, v = F(min(1, max(0, u))), F(min(1, max(0, v)))
        result.append((float(u), float(v)))
    return result


def atlas_corners(uv, frame=None):
    low, high = (frame['min'], frame['max']) if frame else ((0., 0.), (1., 1.))
    return [(float(F(F(low[0])+F(F(u)*F(high[0]-low[0])))),
             float(F(1)-F(F(low[1])+F(F(v)*F(high[1]-low[1]))))) for u, v in uv]


def layer_uvs(point, renderer, layer, index, time, descriptor):
    sheet = descriptor.get('source_sheet') if descriptor else None
    uv = texture_corners(layer.get('m_TextureControls', {}), time, point, index, sheet=sheet is not None)
    if sheet:
        validate_sheet(sheet)
        a, b, blend = sheet_frame(sheet, point['sequence'], point, renderer)
        return atlas_corners(uv, a), atlas_corners(uv, b), blend
    require(not descriptor or not descriptor.get('is_sprite_sheet'), 'original atlas missing')
    current = atlas_corners(uv)
    return current, current, 0.




def _build_geometry(row, renderer, textures, camera):
    """Shared stateless math; callers must admit their complete renderer profile."""
    right, up, eye = (np.asarray(camera[k], dtype=float) for k in ('right', 'up', 'position'))
    require(all(v.shape == (3,) and np.isfinite(v).all() for v in (right, up, eye))
            and abs(np.linalg.norm(right)-1) < 1e-5 and abs(np.linalg.norm(up)-1) < 1e-5
            and abs(np.dot(right, up)) < 1e-5, 'camera basis invalid')
    scale = literal(renderer, 'm_flRadiusScale', 1); alpha_scale = literal(renderer, 'm_flAlphaScale', 1)
    roll_scale = literal(renderer, 'm_flRollScale', 1)
    require(scale >= 0 and 0 <= alpha_scale <= 1, 'radius/alpha scale outside profile')
    # Native Sprite constructor, unlike the rope profile defaults:0/5000/1e8/2e8.
    lo = literal(renderer, 'm_flMinSize', 0); hi = literal(renderer, 'm_flMaxSize', 5000)
    start = literal(renderer, 'm_flStartFadeSize', 1e8); end = literal(renderer, 'm_flEndFadeSize', 2e8)
    require(0 <= lo <= hi and 0 <= start < end, 'size/fade bounds invalid')
    enabled = enabled_texture_layers(renderer.get('m_vecTexturesInput', []))
    vertices = []; colors = []; faces = []; extra = []; layers = {i: dict(current=[], next=[], blend=[]) for i, _ in enabled}
    for p in row['points']:
        position = np.asarray(p['position'], dtype=float); radius = F(F(p['radius'])*F(scale))
        distance = F(np.linalg.norm(position-eye)); first, last = F(F(start)*distance), F(F(end)*distance)
        fade = 1. if radius <= first else max(0., 1.-float(F(F(radius-first)/F(last-first)))) if last > first else 0.
        radius = min(max(float(radius), lo*float(distance)), hi*float(distance))
        raw = p['color_bytes']; alpha_byte = int(np.rint(F(F(raw[3])*F(alpha_scale))))
        alpha = float(F(F(alpha_byte)*F(1/255)))*fade
        rgb = [float(F(F(c)*F(1/255))) for c in raw[:3]]
        if renderer.get('m_bGammaCorrectVertexColors', True):
            rgb = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in rgb]
        color = [c*fade for c in rgb]+[alpha]
        yaw, pitch, roll = p['rotation']; roll *= roll_scale
        c, s = math.cos(roll), math.sin(roll)
        rolled_right = right*c+up*s; rolled_up = up*c-right*s
        c, s = math.cos(yaw), math.sin(yaw)
        axis = rolled_up/np.linalg.norm(rolled_up)
        rolled_right = rolled_right*c+np.cross(axis, rolled_right)*s+axis*np.dot(axis, rolled_right)*(1-c)
        # The native vertex shader collapses sub-UNORM alpha geometry. Keep
        # its array slot as a degenerate quad, never invent an empty collection.
        radius = radius if alpha >= 1/255 else 0.
        offset = len(vertices)
        vertices.extend(position+(rolled_right*x+rolled_up*y)*radius for x, y in CORNERS)
        colors.extend([color]*4); extra.extend([p['shader_extra_data1']]*4)
        faces.extend(tuple(offset+j for j in triangle) for triangle in FACES)
        for index, layer in enabled:
            a, b, blend = layer_uvs(p, renderer, layer, index, row['collection_time'], textures.get(layer.get('m_hTexture')))
            layers[index]['current'].extend(a); layers[index]['next'].extend(b); layers[index]['blend'].extend([blend]*4)
    original = np.asarray(vertices, dtype=float).reshape(-1, 3)
    bias = literal(renderer, 'm_flDepthBias', 0)
    positions, ratios = projective_depth_bias(original, bias, camera.get('projection')) if bias else (original.copy(), np.ones(len(original)))
    require(np.isfinite(positions).all() and all(math.isfinite(v) for c in colors for v in c), 'nonfinite mesh inputs')
    return dict(vertices=positions, source_vertices=original, ratios=ratios, colors=colors, faces=faces,
                extra=extra, layers=layers, native_points=len(row['points']), depth_bias=bias)
