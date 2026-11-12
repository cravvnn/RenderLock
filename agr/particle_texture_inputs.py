"""Stateless texture-control substitutes under the explicit simulation seed policy.

No stored RNG, lifetime, cache or retry state: an immutable collection/renderer/
layer identity derives the same bounded value at every sampled tick. This is
not recovery of the game's missing random seed or its random stream.
"""
import hashlib
import json
import math
import numpy as np
from .installed_effects import UnsupportedEffect

FIELDS={'m_flFinalTextureScaleU','m_flFinalTextureScaleV','m_flFinalTextureOffsetU',
        'm_flFinalTextureOffsetV','m_flFinalTextureUVRotation','m_flDistortion'}


def resolve_texture_random_inputs(controls,identity,seed,seed_policy):
    """Resolve constant unseeded uniform controls; retain their declared mapping."""
    if not isinstance(controls,dict):raise UnsupportedEffect('Invalid texture controls')
    result=dict(controls);notes=[]
    for field,value in controls.items():
        if not isinstance(value,dict) or value.get('m_nType')!='PF_TYPE_RANDOM_UNIFORM':continue
        low,high=value.get('m_flRandomMin'),value.get('m_flRandomMax')
        if (field not in FIELDS or any(type(v) not in (int,float) or not math.isfinite(v) for v in (low,high))
                or low>high or value.get('m_bHasRandomSignFlip',False)
                or value.get('m_nRandomMode','PF_RANDOM_MODE_CONSTANT')!='PF_RANDOM_MODE_CONSTANT'
                or value.get('m_nMapType','PF_MAP_TYPE_DIRECT') not in ('PF_MAP_TYPE_DIRECT','PF_MAP_TYPE_MULT')):
            raise UnsupportedEffect('Unsupported random texture control '+field)
        if low==high:continue # Already exact, handled by texture_control_value.
        if value.get('m_nRandomSeed',-1)!=-1:
            raise UnsupportedEffect('Explicit texture random seeds require a separate stream implementation')
        if (seed_policy!='agr_substitute_sha256_v1' or type(seed) is not int or not 0<=seed<=0xffffffff
                or not isinstance(identity,str) or not identity):
            raise UnsupportedEffect('Random texture input requires the declared substitute seed policy')
        key=json.dumps([seed,identity,field],separators=(',',':')).encode()
        amount=int.from_bytes(hashlib.sha256(key).digest()[:8],'little')/2**64
        with np.errstate(over='ignore',invalid='ignore'):
            sampled=float(np.float32(low+amount*(high-low)))
        if not math.isfinite(sampled):raise UnsupportedEffect('Random texture input exceeds float32 range')
        result[field]=dict(value,m_nType='PF_TYPE_LITERAL',m_flLiteralValue=sampled)
        notes.append('Texture-control randomness uses a deterministic substitute seed; the recorded engine seed is unavailable.')
    return result,list(dict.fromkeys(notes))
