"""Authored FE control preparation, before particle and rotation reconstruction.

This is the unit-instance initialization route of the current native operation.
Its timing within the offline integrator remains a separate caller contract.
"""
import numpy as np

from .fe_node_rotations import _rotate
from .fe_soft_offsets import apply_soft_offsets


def control_transform_rows(matrices, unit_scale, *, discard_animation_scale=False):
    """Convert metre matrices to unit Source controls at a specified phase.

    The native animation gather copies xyz/quaternion and forces control W=1.
    Its raw uniform bone scale is discarded only when explicitly requested.
    The physics instance itself remains unit scale at the Blender entrypoint.
    """
    matrices=np.asarray(matrices,dtype=float)
    if (matrices.ndim!=3 or matrices.shape[1:]!=(4,4) or not 1<=len(matrices)<=4096
            or not np.isfinite(matrices).all() or not np.isfinite(unit_scale) or unit_scale<=0
            or np.max(abs(matrices[:,3]-[0,0,0,1]))>2e-5):
        raise ValueError('Finite bounded FE control matrices and positive units required')
    result=np.zeros((len(matrices),8));result[:,3]=1;result[:,7]=1
    result[:,:3]=matrices[:,:3,3]/unit_scale
    for i,matrix in enumerate(matrices):
        rotation=matrix[:3,:3]
        if discard_animation_scale:
            scales=np.linalg.norm(rotation,axis=0);scale=float(scales.mean())
            if scale<=1e-10 or np.max(abs(scales/scale-1))>2e-5:
                raise ValueError('FE animation gather requires recoverable positive uniform control scale')
            rotation=rotation/scale
        if (np.max(abs(rotation.T@rotation-np.eye(3)))>2e-5
                or abs(np.linalg.det(rotation)-1)>2e-5):
            raise ValueError('FE control preparation is qualified only for unit instance/control scale')
        xx,xy,xz=rotation[0];yx,yy,yz=rotation[1];zx,zy,zz=rotation[2]
        symmetric=np.array([[xx-yy-zz,xy+yx,xz+zx,zy-yz],
            [xy+yx,yy-xx-zz,yz+zy,xz-zx],[xz+zx,yz+zy,zz-xx-yy,yx-xy],
            [zy-yz,xz-zx,yx-xy,xx+yy+zz]])
        result[i,4:]=np.linalg.eigh(symmetric)[1][:,-1]
    return result


def control_transform_matrices(rows, unit_scale):
    """Convert validated Source transform rows to metre control matrices."""
    from .vendor.pose_math import rotate
    rows=apply_soft_offsets(rows,[])
    if not np.isfinite(unit_scale) or unit_scale<=0:raise ValueError('Positive FE control units required')
    result=np.broadcast_to(np.eye(4),(len(rows),4,4)).copy()
    result[:,:3,3]=rows[:,:3]*unit_scale
    result[:,:3,:3]=np.stack([rotate(rows[:,4:],axis) for axis in np.eye(3)],axis=-1)*rows[:,3,None,None]
    return result


def prepare_goal_transforms(physics, matrices, unit_scale):
    """Prepare one frame of exact raw animation controls before reconstruction."""
    return control_transform_matrices(prepare_control_offsets(
        control_transform_rows(matrices,unit_scale,discard_animation_scale=True),physics),unit_scale)


def validate_preparation_layout(physics):
    """Require the native repeat-pass closure domain used by the baker.

    Every hard child is written once after its hard parent. Every soft child
    has a hard write, so each preparation pass starts its blend from the same
    authored hard frame. The input-list construction retains all other roots.
    """
    fe=physics['m_pFeModel']
    prepare_control_offsets(fe['m_InitPose'],physics)
    hard=fe.get('m_CtrlOffsets',[])
    children=[row['nCtrlChild'] for row in hard]
    if len(set(children))!=len(children):
        raise ValueError('FE preparation requires unique hard control children')
    written=set();all_children=set(children)
    for row in hard:
        if row['nCtrlParent'] in all_children and row['nCtrlParent'] not in written:
            raise ValueError('FE hard control preparation must follow parent dependency order')
        written.add(row['nCtrlChild'])
    if any(row['nCtrlChild'] not in written for row in fe.get('m_CtrlSoftOffsets',[])):
        raise ValueError('FE soft control preparation requires an authored hard baseline for each child')
    return sorted(set(range(len(fe['m_InitPose'])))-written)


def prepare_control_offsets(current, physics, *, offset_scale=1.0):
    """Apply ordered hard control offsets, then ordered soft control offsets.

    Transforms use Source xyz/scale/xyzw rows. Hard offsets copy the evolving
    parent's rotation and reset the child scale to one. Soft offsets preserve
    child rotation/scale. Morph reference poses and Os offsets are unqualified.
    """
    fe=physics['m_pFeModel']
    if fe.get('m_DynKinLinks') or fe.get('m_MorphLayers') or fe.get('m_MorphSetData'):
        raise ValueError('FE control preparation requires empty DynKin and morph declarations')
    if fe.get('m_CtrlOsOffsets'):
        raise ValueError('FE Os control preparation is not qualified')
    hard=fe.get('m_CtrlOffsets',[]);soft=fe.get('m_CtrlSoftOffsets',[])
    if not isinstance(hard,list) or len(hard)>100000:
        raise ValueError('Bounded FE hard control declarations required')
    # The shared soft helper validates the input representation even if the
    # model has no soft rows. Validate every hard row before changing any row.
    result=apply_soft_offsets(current,[],offset_scale=offset_scale)
    for row in hard:
        for key in ('nCtrlParent','nCtrlChild'):
            if type(row.get(key)) is not int or not 0<=row[key]<len(result):
                raise ValueError('FE hard control outside transform array')
        offset=np.asarray(row.get('vOffset'),dtype=float)
        if offset.shape!=(3,) or not np.isfinite(offset).all():
            raise ValueError('Finite authored FE hard control offset required')
    for row in hard:
        parent,child=row['nCtrlParent'],row['nCtrlChild']
        frame=result[parent].copy()
        result[child,:3]=frame[:3]+frame[3]*_rotate(frame[4:],np.asarray(row['vOffset'])*offset_scale)
        result[child,3]=1.
        result[child,4:]=frame[4:]
    return apply_soft_offsets(result,soft,offset_scale=offset_scale)
