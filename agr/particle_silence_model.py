"""Pinned Silence startup cylinder inputs; bounded reference PBR response.

This recognizes the installed renderer and material, not arbitrary VFX bytecode.
The five dynamic programs are attribute reads, a zero float2 and OFFSET*.5.
Their decoded source is checked separately with the pinned VRF decompiler.
Native material VM, illumination, refraction and exact clock phase remain outside
this subset. No runtime/cache state is introduced here.
"""
import hashlib
import json
import math
from pathlib import Path
from .installed_effects import UnsupportedEffect

MODEL='models/particle/generic_cylinder_fx_model.vmdl'
MODEL_SHA='1a3427585a56f7aedc16b85c41bd407cc934fdc3683e5c6dca678e6b9a22dca1'
MATERIAL='materials/models/particle/silenced_debuff_start_sphere.vmat'
MATERIAL_SHA='f026b7c4005242d8c43a4fa21b0f18525c96eaa23c3c5c72f9091c199425d321'
PARSED_SHA='b586cd409c2c04e8fcd6aebbb0a7662a398456d370db7c336311959bb987ca2b'
RENDERER_SHA='61b2c07e41a9fcd7f9a98ce933af282b75c8db65969818240e207ec24bd57135'
AUX_POLICY='agr_model_aux_scalar_fields_v1'
POLICY='agr_silence_start_reference_pbr_v1'
STATIC_POLICY='agr_particle_model_static_v1'

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def is_silence_renderer(renderer):
    try:return digest(renderer)==RENDERER_SHA
    except (ValueError,TypeError):return False

def silence_material_profile(descriptor,renderer):
    authored=descriptor.get('material',{})
    try:parsed=digest(authored)
    except (TypeError,ValueError):parsed=None
    if (not is_silence_renderer(renderer) or descriptor.get('resource')!=MODEL
            or descriptor.get('source_sha256')!=MODEL_SHA or descriptor.get('material_resource')!=MATERIAL
            or descriptor.get('material_sha256')!=MATERIAL_SHA or parsed!=PARSED_SHA):
        raise UnsupportedEffect('Unqualified Silence cylinder renderer/model/material identity')
    return ({p['m_name']:p['m_pValue']for p in authored['m_textureParams']},
            {p['m_name']:p['m_flValue']for p in authored['m_floatParams']},
            {p['m_name']:p['m_value']for p in authored['m_vectorParams']})

def material_inputs(particle,simulation):
    color=particle.get('color');glow=particle.get('scratch_float0')
    offset=particle.get('scratch_float2');alpha_test=particle.get('scratch_float1')
    if (simulation.get('model_material_fields_policy')!='agr_model_material_fields_v1'
            or simulation.get('model_aux_scalar_fields_policy')!=AUX_POLICY
            or not isinstance(color,(list,tuple))or len(color)!=3
            or any(type(v)not in(int,float)or not math.isfinite(v)or not 0<=v<=1e6 for v in color)
            or any(type(v)not in(int,float)or not math.isfinite(v)or abs(v)>1e6 for v in (glow,offset,alpha_test))
            or glow<0):
        raise UnsupportedEffect('Missing qualified Silence color/strength/offset/alpha-test fields; regenerate simulation')
    # ALPHATEST is bound by the renderer but absent from m_renderAttributesUsed.
    # OFFSET controls translucent UV velocity; it is not an illumination UV offset.
    return dict(color=tuple(color),glow=float(glow),scroll=(float(offset),float(offset)*.5),alpha_test=float(alpha_test))


def require(value,message):
    if not value:raise UnsupportedEffect('Silence startup: '+message)


def validate_descriptor(renderer,descriptor):
    """Only explicit static-command output, never failed-morph fallback."""
    require(is_silence_renderer(renderer) and isinstance(descriptor,dict), 'renderer/descriptor differs')
    require(type(descriptor.get('schema_version'))is int and descriptor['schema_version']==1
        and descriptor.get('policy')==STATIC_POLICY and descriptor.get('resource')==MODEL
        and descriptor.get('source_sha256')==MODEL_SHA and type(descriptor.get('bones'))is int
        and descriptor['bones']==1 and descriptor.get('animations')==[] and descriptor.get('mesh_groups')==[]
        and type(descriptor.get('body_groups'))is int and descriptor['body_groups']==0
        and descriptor.get('material_exported')is False, 'explicit static source/selector identity differs')
    silence_material_profile(descriptor,renderer)
    require(isinstance(descriptor.get('directory'),str) and descriptor.get('geometry_file')=='model.glb',
        'geometry path missing')
    path=Path(descriptor['directory'])/'model.glb'
    require(path.is_file() and 20<=path.stat().st_size<=64*1024**2, 'geometry unavailable or exceeds bounds')
    from .asset_converter import sha
    require(sha(path)==descriptor.get('geometry_sha256'), 'geometry digest differs')
    from .particle_model_geometry import read_model_geometry
    geometry=read_model_geometry(path)
    require(geometry.get('static')is True and not geometry['morphs'], 'geometry contains animation')
    return descriptor


def validate_samples(renderer,descriptor,samples,simulation,unit_scale=.0254):
    validate_descriptor(renderer,descriptor)
    require(type(unit_scale)in(int,float) and math.isfinite(unit_scale) and unit_scale>0, 'invalid scene units')
    require(isinstance(samples,list) and 0<len(samples)<=100000, 'complete particle samples unavailable')
    from .particle_model_math import model_matrix
    matrices=[];previous=None
    for tick,_,particle in samples:
        require(type(tick)is int and (previous is None or tick==previous+1), 'sample ticks are not consecutive integers')
        previous=tick
        material_inputs(particle,simulation)
        require(all(type(particle.get(k))in(int,float) and math.isfinite(particle[k])
                    and 0<=particle[k]<=1 for k in ('alpha','alpha_alternate')), 'invalid unit opacity samples')
        matrix=model_matrix(particle,renderer);matrix[:3,3]*=unit_scale;matrices.append(matrix)
    from .particle_model_geometry import read_model_geometry
    return read_model_geometry(Path(descriptor['directory'])/'model.glb'),matrices
