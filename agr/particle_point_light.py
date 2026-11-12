"""Evaluated native omni-light data and an explicit Blender adaptation.

The descriptor is already evaluated by the game. This module does not infer
renderer defaults, particle age or a material clock. Shader/BRDF/photometric
equivalence to Source is not established by this conversion.
"""
import math
import struct

SCOPE = ('Evaluated native point-light position/RGB/range/quadratic falloff. '
         'Blender adaptation: Cycles falloff graph or EEVEE approximate cutoff; Source photometry, BRDF, fog, '
         'submitted-frame/common hero clock and pixel parity remain unqualified.')


def require(value, message):
    if not value:
        raise ValueError('Native point light: '+message)


def decode_descriptor(raw):
    require(type(raw) is bytes and len(raw) == 0x120, 'descriptor length differs')
    require(struct.unpack_from('<I', raw)[0] == 1, 'non-omni descriptor')
    color = list(struct.unpack_from('<3f', raw, 4))
    position = list(struct.unpack_from('<3f', raw, 0x98))
    radius = struct.unpack_from('<f', raw, 0x1c)[0]
    attenuation = list(struct.unpack_from('<3f', raw, 0x24))
    offset = struct.unpack_from('<f', raw, 0xcc)[0]
    require(all(math.isfinite(v) and abs(v) <= 1e12 for v in color+position+[radius, offset]),
            'nonfinite/out-of-bound descriptor')
    require(all(v >= 0 for v in color) and radius > 0 and 0 <= offset <= 1,
            'unsupported color/range/falloff')
    require(attenuation == [0., 0., 1.] and struct.unpack_from('<I', raw, 0xd0)[0] == 12,
            'only observed quadratic profile supported')
    require(raw[0x3d] in (0, 1), 'invalid shadow flag')
    return dict(color=color, position=position, range=radius, falloff_offset=offset,
                casts_shadows=bool(raw[0x3d]))


def source_attenuation(distance_source_units, descriptor):
    require(type(distance_source_units) in (int, float) and math.isfinite(distance_source_units)
            and distance_source_units >= 0, 'invalid distance')
    if distance_source_units > descriptor['range']:
        return 0.
    return min(1., max(0., 1/max(1., distance_source_units**2)-descriptor['falloff_offset']))


def blender_parameters(raw, unit_scale):
    require(type(unit_scale) in (int, float) and math.isfinite(unit_scale) and 0 < unit_scale <= 1,
            'explicit scene unit scale required')
    d = decode_descriptor(raw)
    peak = max(d['color'])
    return dict(location=[v*unit_scale for v in d['position']],
                color=[v/peak for v in d['color']] if peak else [0., 0., 0.],
                energy=4*math.pi*peak*unit_scale**2,
                range=d['range']*unit_scale, falloff_offset=d['falloff_offset'],
                unit_scale=unit_scale, casts_shadows=d['casts_shadows'], scope=SCOPE)
