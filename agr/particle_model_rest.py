"""Source model-order inverse rest matrices from explicit authored parent rows.

Pure input validation and affine math. Runtime CP/model selection, compiled
metadata acquisition and animation poses are independent obligations. There is
no imported-armature/NM rest fallback or quaternion renormalization.
"""
import re
import numpy as np
from .particle_snapshot_matrices import _matrices

POLICY='agr_authored_model_rest_v1'


def model_inverse_rest(inventory,*,resource,resource_sha256,names):
    """Return model-order Source 4x4 inverse rest matrices, with exact identity."""
    if (not isinstance(inventory,dict)or type(inventory.get('schema_version'))is not int or inventory['schema_version']!=1
            or inventory.get('policy')!=POLICY or not isinstance(resource,str)
            or not resource.endswith('.vmdl_c')or resource.startswith('/')
            or '..'in resource or ':'in resource or '\\'in resource
            or not isinstance(resource_sha256,str)or re.fullmatch('[0-9a-f]{64}',resource_sha256)is None
            or inventory.get('model')!=resource.removesuffix('_c')
            or inventory.get('source_sha256')!=resource_sha256):
        raise ValueError('Authored rest model/source identity mismatch')
    if (not isinstance(names,list)or not 0<len(names)<=4096
            or any(not isinstance(n,str)or not n for n in names)
            or len(set(names))!=len(names)or inventory.get('names')!=names):
        raise ValueError('Authored rest requires the exact model bone order')
    parents=inventory.get('parents')
    if (not isinstance(parents,list)or len(parents)!=len(names)
            or any(type(p)is not int or not -1<=p<len(names)for p in parents)):
        raise ValueError('Invalid authored rest parent indices')
    local=_matrices(inventory.get('parent_transforms'))
    if len(local)!=len(names):raise ValueError('Missing authored parent rest rows')
    world=[None]*len(names)
    for start in range(len(names)):
        path=[];seen=set();index=start
        while index>=0 and world[index]is None:
            if index in seen:raise ValueError('Cyclic authored rest hierarchy')
            seen.add(index);path.append(index);index=parents[index]
        for bone in reversed(path):
            parent=parents[bone]
            with np.errstate(over='ignore',invalid='ignore'):
                world[bone]=local[bone]if parent<0 else world[parent]@local[bone]
            if not np.isfinite(world[bone]).all():
                raise ValueError('Authored rest hierarchy overflow')
    try:
        result=np.linalg.inv(np.asarray(world,dtype=np.float64))
    except np.linalg.LinAlgError as error:
        raise ValueError('Singular authored rest hierarchy')from error
    if not np.isfinite(result).all():raise ValueError('Authored inverse rest overflow')
    return result
