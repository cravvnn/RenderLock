"""Static slide mesh/material projection, without capture or renderer admission.

The caller supplies a complete affine transform and source-bound material frame.
This leaf owns no clock, lifetime, resource allocation or cross-frame identity.
PBR lighting and texture filtering are separate Blender approximations.
"""
import math
import struct
import numpy as np
from .particle_material_expressions import (
    MODULE_SHA256, SLIDE_MATERIAL_SHA256, SLIDE_PARSED_SHA256,
)
from .particle_slide_parameters import canonical, require, raw, SPEC, POLICY
from .particle_slide_material import (
    PIXEL_SHADER_SHA256, VERTEX_SHADER_SHA256, _f32, slide_texture_uv,
)
from .particle_model_geometry import transform_static_model_frame
from .npr_color_matrices import color_tint

FRAME_POLICY = 'agr_slide_source_bound_material_frame_v1'
UV_NAMES = ('albedo', 'translucency', 'alt_translucency', 'self_illum')
SCENE_MODULE_SHA256 = '22a37a9ec40f8960a2f3749ce9687f80e2ff35a932ea8dce03bf2db8b77acdb4'


def _frame(frame):
    require(type(frame) is dict and type(frame.get('schema_version')) is int
            and frame['schema_version'] == 1 and frame.get('policy') == FRAME_POLICY,
            'source-bound slide frame required')
    canonical(frame)
    time = struct.unpack('<f', raw(frame.get('game_time_raw'), 4, 'source GameTime'))[0]
    require(type(frame.get('game_time')) in (int, float) and math.isfinite(time)
            and frame['game_time'] == time, 'frame GameTime/raw scalar differs')
    values = frame.get('material_values')
    require(type(values) is dict and type(values.get('schema_version')) is int
            and values['schema_version'] == 1 and values.get('policy') == POLICY
            and values.get('material_sha256') == SLIDE_MATERIAL_SHA256
            and values.get('parsed_sha256') == SLIDE_PARSED_SHA256
            and values.get('expression_module_sha256') == MODULE_SHA256
            and type(values.get('binding_index')) is int and values['binding_index'] == 1,
            'resolved slide binding/material identity differs')
    values = values.get('values')
    require(type(values) is dict and set(values) == {v[0] for v in SPEC.values()},
            'complete slide material values required')
    for name, lanes, lo, hi in SPEC.values():
        value = values[name]
        require(type(value) in (list, tuple) and len(value) == lanes,
                'slide material value shape differs')
        for i, v in enumerate(value):
            require(lo[i] <= _f32(v) <= hi[i], 'slide material clamp differs')
    return values, time


def _derived_uv(scale, rotation, offset):
    """Pinned VCS expression order; host trig is not native/GPU bit parity."""
    angle = _f32(_f32(_f32(rotation) * _f32(math.pi)) / _f32(180))
    c, s = _f32(math.cos(angle)), _f32(math.sin(angle))
    x, y = map(_f32, scale[:2])
    matrix = (_f32(x*c), _f32(-x*s), _f32(y*s), _f32(y*c))
    translated = tuple(_f32(_f32(_f32(_f32(-.5)*a)*b)+_f32(o))
                       for a, b, o in zip((x, y), (_f32(c-s), _f32(s+c)), offset))
    return matrix, tuple(_f32(v + _f32(.5)) for v in translated)


def prepare_slide_model_frame(geometry, matrix, frame, authored, instance_rgba,
                              unit_scale, *, vertex_shader_sha256, pixel_shader_sha256):
    """Bake full affine mesh and four UV layers for the one pinned VMAT.

instance_rgba is EXPLICIT, including alpha; absent COLOR material queries are
handled upstream from stored properties and never replaced here with white.
Source-unit basis scaling is disabled by both pinned authored axis selectors.
Both VS coordinates therefore equal mesh UV. UV V is flipped exactly once,
after the PS transform. Instance-color producer and capture coherence remain
the caller's obligations; this numeric operation does not establish either.
"""
    require(vertex_shader_sha256 == VERTEX_SHADER_SHA256
            and pixel_shader_sha256 == PIXEL_SHADER_SHA256, 'selected slide shaders differ')
    require(type(authored) is dict and canonical(authored) == SLIDE_PARSED_SHA256,
            'slide authored material differs')
    values, time = _frame(frame)
    require(type(instance_rgba) in (list, tuple) and len(instance_rgba) == 4,
            'explicit complete instance RGBA required')
    rgba = tuple(_f32(v) for v in instance_rgba)
    require(all(0 <= v <= 1 for v in rgba), 'instance RGBA outside normalized range')
    mesh = transform_static_model_frame(geometry, matrix, unit_scale)
    # The installed mesh includes repeated-index padding triangles. They have
    # zero raster area but corrupt Blender's corner-normal builder if passed as
    # polygons. Remove only these provably empty triangles, retaining counts.
    indices = mesh['indices']
    keep = np.array([len(set(map(int, face))) == 3 for face in indices], dtype=bool)
    require(bool(keep.any()), 'slide mesh has no nondegenerate index triangles')
    mesh['source_triangles'] = len(indices)
    mesh['discarded_degenerate_triangles'] = int((~keep).sum())
    mesh['indices'] = indices[keep].copy()
    vectors = {v['m_name']: v['m_value'] for v in authored['m_vectorParams']}
    floats = {v['m_name']: v['m_flValue'] for v in authored['m_floatParams']}
    # Quantize=0 and primary-UV selection are exact defaults in the pinned VCS;
    # material SHA pins all authored overrides. No unread runtime value fallback.
    spec = {
        'albedo': ('AlbedoTexcoord', vectors['g_vAlbedoScrollSpeed1'], '1'),
        'self_illum': ('SelfIllumTexcoord', vectors['g_vSelfIllumScrollSpeed1'], '1'),
        'translucency': ('TranslucentTexcoord', values['g_vTranslucentScrollSpeed0_1'], '0_1'),
        'alt_translucency': ('TranslucentTexcoord', vectors['g_vTranslucentScrollSpeed1_1'], '1_1'),
    }
    layers = {}
    for name, (prefix, scroll, suffix) in spec.items():
        transform, offset = _derived_uv(vectors['g_v'+prefix+'Scale'+suffix],
            floats['g_fl'+prefix+'Rotation'+suffix], vectors['g_v'+prefix+'Offset'+suffix][:2])
        coordinates = [slide_texture_uv(uv.tolist(), time, scroll[:2], 0, transform, offset,
                       shader_sha256=pixel_shader_sha256) for uv in geometry['uv']]
        layers[name] = np.asarray([(u, 1-v) for u, v in coordinates], dtype=np.float64)
    # The VMAT derives MatrixColorTint3 from gamma-decoded artist COLOR. This
    # existing qualified row-vector matrix is distinct from instance RGB.
    tint = values['g_vColorTint1']
    require(all(0 <= v <= 1 for v in tint), 'slide tint outside qualified gamma range')
    linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in tint]
    return dict(**mesh, uv_layers=layers, instance_rgba=rgba,
        tint_matrix=color_tint(linear, 1), self_illum_scale=values['g_flSelfIllumScale1'][0],
        self_illum_tint=tuple(values['g_vSelfIllumTint1']), opacity_scale=floats['g_flOpacityScale1'],
        game_time=time, input_frame_sha256=canonical(frame),
        scope='Affine geometry and selected-shader numeric inputs; host derived trig/gamma, Blender PBR/filtering and instance-input ownership remain separate.')


def prepare_slide_model_frame_from_cmdl(geometry, matrix, frame, authored, colors,
        unit_scale, *, slot, expected_wrapper, scene_base,
        vertex_shader_sha256, pixel_shader_sha256):
    """Bind observed evaluated CMDL output to this exact current model slot.

This avoids recreating field/blend/desaturation/white-RGB decisions. The native
SetColor float4 is CPU output: caller retains same-capture association, loaded
producer and full-cohort proof. Blender uses an explicit host sRGB approximation
of RGB and the unquantized observed alpha; final GPU packing is not established.
No raw field1/lifetime, guessed alpha, Age or missing-color fallback is allowed.
"""
    require(type(colors) is dict and colors.get('schema') == 'private_cmdl_evaluated_color_v1'
            and colors.get('scene_module_sha256') == SCENE_MODULE_SHA256,
            'qualified evaluated model color required')
    rows = colors.get('rows')
    require(type(rows) is list and 1 <= len(rows) <= 3 and type(slot) is int
            and 0 <= slot < len(rows) and colors.get('initial_read_bytes') == len(rows)*24,
            'complete evaluated model slots required')
    require(type(scene_base) is int and 0 < scene_base < 2**47 and scene_base%8 == 0
            and type(expected_wrapper) is int and 0 < expected_wrapper < 2**47
            and expected_wrapper%8 == 0, 'explicit current model/module addresses required')
    require(all(type(row) is dict and type(row.get('slot')) is int and row['slot'] == index
            for index, row in enumerate(rows)), 'evaluated color slot order differs')
    row = rows[slot]
    require(row.get('wrapper_pointer') == hex(expected_wrapper)
            and row.get('state') == 'captured_CMDL_SetColor_float4_not_GPU'
            and row.get('producer') == dict(vtable='0x625bc8',slot='0x70',setter='0x17f460',offset='0x110')
            and struct.unpack('<Q',raw(row.get('header_raw'),8,'CMDL type'))[0] == scene_base+0x625bc8,
            'evaluated color producer/current instance differs')
    source = raw(row.get('color_raw'),16,'CMDL evaluated color')
    rgba = struct.unpack('<4f',source)
    require(all(math.isfinite(v) and 0 <= v <= 1 for v in rgba)
            and type(row.get('color')) is list and len(row['color']) == 4
            and all(type(v) in (int,float) for v in row['color'])
            and tuple(row['color']) == rgba, 'evaluated model color/raw floats differ')
    # The selected VS decodes sRGB packed RGB; the normalized CPU float4 is
    # kept separately. We do not fabricate evidence for a GPU packing step.
    linear = tuple(v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgba[:3])
    prepared = prepare_slide_model_frame(geometry,matrix,frame,authored,(*linear,rgba[3]),unit_scale,
        vertex_shader_sha256=vertex_shader_sha256,pixel_shader_sha256=pixel_shader_sha256)
    prepared['instance_color_source'] = dict(policy='observed_CMDL_float4_host_srgb_approximation_v1',
        raw_hex=source.hex(),slot=slot,wrapper=hex(expected_wrapper),scene_module_sha256=SCENE_MODULE_SHA256,
        unquantized_alpha=rgba[3],scope='Observed CPU output; host RGB conversion and unquantized alpha are a Blender approximation, not final GPU packing.')
    return prepared
