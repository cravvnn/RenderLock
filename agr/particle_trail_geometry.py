"""Stateless expansion of explicit native trail vertex controls.

The installed S_DRAW_AS_TRAIL vertex variant evaluates a four-control Catmull
segment, including radius, and crosses its tangent with the camera direction.
Inputs here are already uploaded controls and byte-quantized endpoint colors;
this module does not infer them from replay particles or admit a renderer.
No particle identities, temporal state, cache, allocation or publication lives
here. See ParticleTrailGeometry_COVERAGE.md for independent native evidence.
"""
import math
import numpy as np
from .installed_effects import UnsupportedEffect
from .particle_rope_geometry import catmull_rom, catmull_tangent

F = np.float32
# Native shared XY vertices0,1,254,255 and fixed index range0..6. Keep the
# uploaded triangle ordering, including its opposite winding. Callers must
# separately qualify two-sided material handling, not silently repair faces.
TRAIL_CORNERS = ((-1., 0.), (1., 0.), (-1., 1.), (1., 1.))
TRAIL_FACES = ((0, 1, 2), (1, 2, 3))


def require(value, message):
    if not value:
        raise UnsupportedEffect('Trail shader geometry: '+message)


def _array(value, shape):
    try:
        with np.errstate(over='ignore', invalid='ignore'):
            result = np.asarray(value, dtype=np.float32)
    except (ValueError, TypeError, OverflowError) as exc:
        raise UnsupportedEffect('Trail shader geometry: invalid numeric input') from exc
    require(result.shape == shape and np.isfinite(result).all(), 'nonfinite/invalid input shape')
    return result


def endpoint_shader_colors(colors, gamma):
    """Tail then head, matching native packed COLOR0/COLOR1; gamma precedes mix."""
    require(type(gamma) is bool, 'gamma flag unavailable')
    require(isinstance(colors, (list, tuple)) and len(colors) == 2
            and all(isinstance(row, (list, tuple)) and len(row) == 4
                    and all(type(v) is int and 0 <= v <= 255 for v in row) for row in colors),
            'endpoint byte colors unavailable')
    result = np.asarray(colors, dtype=np.float32)*F(1/255)
    if gamma:
        rgb = result[:, :3]
        result[:, :3] = np.where(rgb <= F(.04045), rgb*F(.07739938050508499),
                                np.power(rgb*F(.9478673338890076)+F(.052132703363895416), F(2.4)))
    return result


def sample_trail(controls, colors, camera_position, t, side, *, gamma=True):
    """One explicit native shader vertex, before optional fades/depth/materials.

    controls is four XYZW nodes (W=radius), colors is tail/head RGBA bytes.
    t=(input corner Y*.5)+.5; side is corner X. Degenerate normalization is
    refused instead of introducing a fallback plane absent from the shader.
    Caller must separately qualify vertex topology, shader uniforms and inputs.
    """
    nodes = _array(controls, (4, 4)); eye = _array(camera_position, (3,))
    require(type(t) in (int, float) and math.isfinite(t) and 0 <= t <= 1
            and type(side) in (int, float) and math.isfinite(side) and -1 <= side <= 1,
            'vertex parameters out of range')
    palette = endpoint_shader_colors(colors, gamma)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        t = F(t); center = catmull_rom(*nodes, t)
        tangent = catmull_tangent(*(node[:3] for node in nodes), t)
        across = np.cross(tangent, eye-center[:3])
        length2 = np.dot(across, across)
        require(np.isfinite(center).all() and np.isfinite(tangent).all()
                and np.isfinite(length2) and length2 > 0 and center[3] >= 0,
                'undefined normalization, radius or float32 overflow')
        width = across/F(np.sqrt(length2))
        position = center[:3]+width*center[3]*F(side)
        color = palette[0]+t*(palette[1]-palette[0])
    require(np.isfinite(position).all() and np.isfinite(color).all(), 'nonfinite expanded vertex')
    return dict(position=[float(v) for v in position],
                center=[float(v) for v in center[:3]], radius=float(center[3]),
                color=[float(v) for v in color])


def build_quad(controls, colors, camera_position, *, gamma=True):
    """Expand the fixed native CPU draw's four explicit control vertices.

    UV/card coordinates are returned before authored texture placement/atlas.
    This does not admit the alternate GPU-driven draw or infer instance inputs.
    Source texture V is top-down; Blender conversion happens after placement.
    """
    points = [sample_trail(controls, colors, camera_position, t, side, gamma=gamma)
              for side, t in TRAIL_CORNERS]
    return dict(vertices=[p['position'] for p in points],
                colors=[p['color'] for p in points],
                source_card_uv=[((side+1)*.5, 1-t) for side,t in TRAIL_CORNERS],
                faces=list(TRAIL_FACES))


def shader_card_uv(crop_flip_bytes):
    """Actual TEXCOORD8 UNORM8 crop/flip, before texture placement/atlas.

    Registered native layouts bind packed bytes+0x78 as R8G8B8A8_UNORM.
    X/Y shrink the sampled texture region, Z/W flip its coordinate basis.
    This does not change geometry or implement optional longitudinal clampV.
    Missing values refuse; a constructor default is never supplied here.
    """
    require(isinstance(crop_flip_bytes, (list, tuple)) and len(crop_flip_bytes) == 4
            and all(type(v) is int and 0 <= v <= 255 for v in crop_flip_bytes),
            'crop/flip byte payload unavailable')
    crop_u, crop_v, flip_u, flip_v = np.asarray(crop_flip_bytes, dtype=np.float32)*F(1/255)
    result = []
    for side, t in TRAIL_CORNERS:
        x = F(F(side)*F(F(flip_u*F(2))-F(1)))
        y = F(F(2*t-1)*F(F(flip_v*F(2))-F(1)))
        u = F(F(F(x*F(.5))*F(F(1)-crop_u))+F(.5))
        v = F(F(F(y*F(-.5))*F(F(1)-crop_v))+F(.5))
        result.append((float(u), float(v)))
    return result


def build_uploaded_quad(controls, colors, camera_position, crop_flip_bytes, *, gamma):
    """One explicit uploaded instance; caller qualifies disabled fades/bias.

    Unlike the unit-card geometry helper, this requires the actual native UV
    bytes and gamma flag. Input/frame/profile admission remains caller-owned.
    """
    uv = shader_card_uv(crop_flip_bytes)
    result = build_quad(controls, colors, camera_position, gamma=gamma)
    result['source_card_uv'] = uv
    return result
