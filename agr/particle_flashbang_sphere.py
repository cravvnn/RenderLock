"""Pinned static flashbang sphere; bounded translucent PBR approximation.

Only COLORTINT is read by this compiled material. The other declared renderer
variables do not justify inventing uncaptured particle fields. Static geometry,
material identity and every transform/color sample are checked independently.
This module is pure per-call validation/math, with no new cache or lifecycle.
"""
import math
from pathlib import Path
import numpy as np
from .particle_value_identity import digest
from .installed_effects import UnsupportedEffect
from .particle_model_geometry import read_model_geometry
from .particle_model_math import model_matrix
from .asset_converter import sha

POLICY='agr_particle_model_static_v1'
MODEL='models/particle/aligned_sphere_fx_model.vmdl'
MODEL_SHA='70126b329e8edd326355ee752b2e0b674547fefbf4a834637d6f41123ad0428f'
MATERIAL='materials/particle/abilities/priest/priest_flashbang_explosion_sphere.vmat'
MATERIAL_SHA='0e1258b454184b571c41efc3e634f0ef190c79f9c30bbfcb3d525d6d2db34113'
PARSED_SHA='117e588de2a91b05a322e8a81fa8d8e25b29ba06ab4598bdbb8efdd4378aa04b'
RENDERER_SHA='af75bb704884a6379ac0d634f150d7092403512835e49fb74f00ecf847429975'


def require(ok,message):
    if not ok:raise UnsupportedEffect('Flashbang sphere: '+message)


def is_renderer(renderer):
    try:return digest(renderer)==RENDERER_SHA
    except (TypeError,ValueError):return False


def material_profile(material):
    require(isinstance(material,dict) and digest(material)==PARSED_SHA,
        'compiled material changed; requalification required')
    return ({p['m_name']:p['m_pValue']for p in material['m_textureParams']},
        {p['m_name']:p['m_flValue']for p in material['m_floatParams']},
        {p['m_name']:p['m_value']for p in material['m_vectorParams']})


def validate_descriptor(renderer,descriptor):
    require(is_renderer(renderer) and isinstance(descriptor,dict), 'renderer/descriptor differs')
    require(type(descriptor.get('schema_version'))is int and descriptor['schema_version']==1
        and descriptor.get('policy')==POLICY and descriptor.get('resource')==MODEL
        and descriptor.get('source_sha256')==MODEL_SHA
        and descriptor.get('material_resource')==MATERIAL and descriptor.get('material_sha256')==MATERIAL_SHA
        and type(descriptor.get('bones'))is int and descriptor['bones']==1
        and descriptor.get('animations')==[] and descriptor.get('mesh_groups')==[]
        and type(descriptor.get('body_groups'))is int and descriptor['body_groups']==0
        and descriptor.get('material_exported')is False,'static source/selector/material identity differs')
    require(isinstance(descriptor.get('directory'),str) and descriptor.get('geometry_file')=='model.glb',
        'geometry path missing')
    path=Path(descriptor['directory'])/'model.glb'
    require(path.is_file() and sha(path)==descriptor.get('geometry_sha256'),'geometry digest differs')
    geometry=read_model_geometry(path)
    require(geometry.get('static')is True and not geometry['morphs'],'geometry contains animation')
    material_profile(descriptor.get('material'))
    return descriptor


def validate_samples(renderer,descriptor,samples,unit_scale=.0254):
    validate_descriptor(renderer,descriptor)
    require(type(unit_scale)in(int,float) and math.isfinite(unit_scale) and unit_scale>0,
        'scene units must be finite and positive')
    require(isinstance(samples,list) and samples,'sampled particle state is missing')
    matrices=[]
    for tick,_,p in samples:
        color=p.get('color');alpha=p.get('alpha');alternate=p.get('alpha_alternate')
        require(type(tick)is int and isinstance(color,(tuple,list)) and len(color)==3
            and all(type(v)in(int,float) and math.isfinite(v) and 0<=v<=1e6 for v in color)
            and all(type(v)in(int,float) and math.isfinite(v) and 0<=v<=1 for v in (alpha,alternate)),
            'finite color and unit opacity samples are required')
        matrix=model_matrix(p,renderer);matrix[:3,3]*=unit_scale;matrices.append(matrix)
    return read_model_geometry(Path(descriptor['directory'])/'model.glb'),matrices


def source_uv(uv,*,scale=(1,1),rotation=0,offset=(0,0)):
    """Source static texture transform; input/output use top-left Source UVs."""
    require(all(type(v)in(int,float) and math.isfinite(v)for v in (*uv,*scale,rotation,*offset)),
        'nonfinite static texture transform')
    angle=math.radians(rotation);c,s=math.cos(angle),math.sin(angle)
    return np.asarray(scale)*(np.array([[c,-s],[s,c]])@(np.asarray(uv)-.5))+.5+np.asarray(offset)


def facing_fade(dot,power):
    require(all(type(v)in(int,float) and math.isfinite(v)for v in (dot,power)), 'nonfinite facing input')
    return max(.001,1-abs(dot) if power<0 else abs(dot))**abs(power)
