"""Collection-level rope inputs; random values use an explicit substitute seed."""
import copy
import hashlib
import json
import math
import numpy as np
from .installed_effects import UnsupportedEffect
from .particle_render_controls import render_scalar

FIELDS=('m_flRadiusScale','m_flAlphaScale','m_flTextureVWorldSize','m_flTextureVScrollRate',
        'm_flTextureVOffset','m_flSelfIllumAmount','m_flDiffuseAmount','m_flOverbrightFactor',
        'm_flAddSelfAmount','m_flSourceAlphaValueToMapToZero','m_flSourceAlphaValueToMapToOne',
        'm_flDesaturation','m_flFeatheringMinDist','m_flFeatheringMaxDist','m_flDepthBias',
        'm_flStartFadeSize','m_flEndFadeSize')
RANDOM_FIELDS={'m_flTextureVWorldSize','m_flTextureVScrollRate','m_flTextureVOffset'}


def resolve_rope_inputs(renderer,context,identity,seed):
    result=copy.deepcopy(renderer);limitations=[]
    if result.get('m_bEnableFadingAndClamping'):
        limitations.append('Rope distance fade and size clamp use the sampled camera and reference viewer equations; current native renderer equivalence is not qualified.')
    color=result.get('m_vecColorScale')
    if isinstance(color,dict) and color.get('m_nType')=='PVEC_TYPE_CP_VALUE':
        cp=color.get('m_nControlPoint',0);scale=color.get('m_vCPValueScale',[1,1,1])
        if type(cp) is not int or not 0<=cp<=63 or context is None or cp not in context['points']:
            raise UnsupportedEffect('Missing qualified rope color control-point snapshot')
        values=[context['points'][cp],scale]
        if any(not isinstance(v,(list,tuple)) or len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in values):
            raise UnsupportedEffect('Invalid rope color control-point input')
        with np.errstate(over='ignore'):
            value=np.asarray(values[0],dtype=np.float32)*np.asarray(scale,dtype=np.float32)
        if not np.isfinite(value).all():raise UnsupportedEffect('Non-finite rope color scale')
        result['m_vecColorScale']=dict(m_nType='PVEC_TYPE_LITERAL',m_vLiteralValue=value.tolist())
    for field in FIELDS:
        if field not in result:continue
        value=result[field]
        if isinstance(value,dict) and value.get('m_nType')=='PF_TYPE_RANDOM_UNIFORM':
            if (field not in RANDOM_FIELDS or type(seed) is not int or not 0<=seed<=0xffffffff
                    or not isinstance(identity,str) or not identity
                    or value.get('m_nRandomMode','PF_RANDOM_MODE_CONSTANT')!='PF_RANDOM_MODE_CONSTANT'
                    or value.get('m_nRandomSeed',-1)!=-1 or value.get('m_bHasRandomSignFlip',False)
                    or value.get('m_nMapType','PF_MAP_TYPE_DIRECT') not in ('PF_MAP_TYPE_DIRECT','PF_MAP_TYPE_MULT')):
                raise UnsupportedEffect('Unqualified random rope input '+field)
            low,high=value.get('m_flRandomMin',0),value.get('m_flRandomMax',0)
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (low,high)) or low>high:
                raise UnsupportedEffect('Invalid random rope interval '+field)
            key=json.dumps([seed,identity,field],separators=(',',':')).encode()
            amount=int.from_bytes(hashlib.sha256(key).digest()[:8],'little')/2**64
            sampled=float(np.float32(low+amount*(high-low)))
            # Float32 rounding can reach an endpoint; the value remains bounded.
            mapped=dict(value,m_nType='PF_TYPE_LITERAL',m_flLiteralValue=sampled)
            result[field]=render_scalar(mapped,context=context)
            limitations.append('Rope texture randomness uses a deterministic substitute seed; the recorded engine seed is unavailable.')
        else:result[field]=render_scalar(value,context=context)
    return result,list(dict.fromkeys(limitations))
