"""Capture model-rest to animated-world transforms for rigid snapshot bindings.

This is a checked Blender input bridge, not an implementation of the engine's
snapshot operators. It emits source-unit, column-vector 3x4 affine matrices.
"""
import math
from contextlib import contextmanager


def snapshot_rig_fingerprint(rig):
    """Identify the selected armature's rest hierarchy, not its animated motion."""
    import hashlib
    import json
    if rig.type != 'ARMATURE' or not rig.get('agr_entity_id'):
        raise ValueError('Snapshot rig requires an AGR entity binding')
    bones = [{'name': bone.name, 'parent': bone.parent.name if bone.parent else None,
              'rest': [list(row) for row in bone.matrix_local]} for bone in rig.data.bones]
    value = {'entity_id': rig['agr_entity_id'], 'bones': bones}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def rigid_snapshot_matrix(rig,bone_name,unit_scale=.0254):
    """Imported Blender-rest transform for the earlier experimental interface.

    Source SNAP coordinates require source_rigid_snapshot_matrix with explicit
    authored inverse rest; the two bases are not interchangeable.
    """
    from mathutils import Matrix
    if rig.type!='ARMATURE' or bone_name not in rig.data.bones or bone_name not in rig.pose.bones:
        raise ValueError('Snapshot skinning bone is absent: '+bone_name)
    if rig.data.pose_position!='POSE':
        raise ValueError('Snapshot skinning requires the animated pose, not rest display mode')
    if not math.isfinite(unit_scale) or unit_scale<=0:
        raise ValueError('Invalid snapshot unit scale')
    rest=rig.data.bones[bone_name].matrix_local
    if abs(rest.determinant())<1e-10:raise ValueError('Singular snapshot rest transform')
    matrix=rig.matrix_world@rig.pose.bones[bone_name].matrix@rest.inverted()
    # These snapshots require a rigid transform. Do not silently apply a rigid
    # normal transform to scaled or sheared skinning matrices.
    linear=matrix.to_3x3();gram=linear.transposed()@linear
    if (not all(math.isfinite(x) for row in matrix for x in row)
            or max(abs(gram[i][j]-(i==j)) for i in range(3) for j in range(3))>2e-4
            or abs(linear.determinant()-1)>2e-4):
        raise ValueError('Snapshot transform is not a finite rigid transform')
    scale=Matrix.Diagonal((unit_scale,unit_scale,unit_scale,1))
    result=scale.inverted()@matrix@scale
    return [list(result[row]) for row in range(3)]


def capture_snapshot_bones(rig,bone_names,scene,*,start_tick,end_tick,tick_rate,unit_scale=.0254):
    """Capture requested bones only, restoring the caller's frame on all exits."""
    if (type(start_tick) is not int or type(end_tick) is not int or end_tick<start_tick
            or end_tick-start_tick>100000 or not math.isfinite(tick_rate) or tick_rate<=0):
        raise ValueError('Invalid snapshot capture range')
    if not bone_names or len(bone_names)!=len(set(bone_names)):
        raise ValueError('Snapshot bone names must be nonempty and unique')
    old_frame,old_subframe=scene.frame_current,scene.frame_subframe
    frames=[]
    try:
        for tick in range(start_tick,end_tick+1):
            frame=1+(tick-start_tick)*(scene.render.fps/scene.render.fps_base)/tick_rate
            scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
            frames.append({'tick':tick,'bones':{name:rigid_snapshot_matrix(rig,name,unit_scale) for name in bone_names}})
    finally:
        scene.frame_set(old_frame,subframe=old_subframe)
    return {'schema_version':1,'matrix_convention':'column_vector_3x4_model_rest_to_world_source_units',
            'unit_scale':unit_scale,'start_tick':start_tick,'end_tick':end_tick,'tick_rate':tick_rate,
            'bone_names':list(bone_names),'frames':frames,
            'scope':'Evaluated Blender rigid bone transforms; caller must bind replay/rig identity before simulator use.'}


def source_rigid_snapshot_matrix(rig, bone_name, source_inverse_rest, unit_scale=.0254):
    """Map Source-authored model-rest positions to the evaluated world pose.

    The caller supplies the exact model-order authored inverse-rest row in
    xyz/scale/quaternion-xyzw form, and owns model/variant/rig identity. Reading
    the imported armature rest matrix here would introduce an unrelated basis.
    Runtime provider selection and previous-frame scheduling remain separate.
    """
    from .particle_snapshot_matrices import snapshot_skinning_matrices
    if (rig.type != 'ARMATURE' or bone_name not in rig.data.bones
            or bone_name not in rig.pose.bones or rig.data.pose_position != 'POSE'):
        raise ValueError('Source snapshot requires an available animated rig bone')
    if not math.isfinite(unit_scale) or unit_scale <= 0:
        raise ValueError('Invalid snapshot unit scale')
    matrix = rig.matrix_world @ rig.pose.bones[bone_name].matrix
    if not all(math.isfinite(x) for row in matrix for x in row):
        raise ValueError('Source snapshot pose is not finite')
    position, rotation, scale = matrix.decompose()
    average = sum(scale)/3
    gram = matrix.to_3x3().transposed() @ matrix.to_3x3()
    if (min(scale) <= 0 or max(scale)-min(scale) > 1e-5
            or max(abs(gram[i][j]-(average**2 if i == j else 0))
                   for i in range(3) for j in range(3)) > 1e-5):
        raise ValueError('Source snapshot pose is scaled nonuniformly or sheared')
    pose = [*(v/unit_scale for v in position), average,
            rotation.x, rotation.y, rotation.z, rotation.w]
    result, _ = snapshot_skinning_matrices([source_inverse_rest], [pose],
        [0, 65535, 65535, 65535], [1, 0, 0])
    linear = result[:, :3]
    import numpy as np
    if (np.max(abs(linear.T@linear-np.eye(3))) > 2e-4
            or abs(np.linalg.det(linear)-1) > 2e-4):
        raise ValueError('Source snapshot result is not a proper rigid transform')
    return result.tolist()


@contextmanager
def evaluated_snapshot_rig(rig, root, scene):
    """Borrow only RenderLock's preview-hidden rig, restoring frame/visibility.

    Hidden ancestors or an independently disabled armature are not substitutes
    for missing evaluation. The exporter marks its own cosmetic rig hiding.
    """
    if rig is None or root is None or rig == root or rig.type != 'ARMATURE':
        raise ValueError('Snapshot requires the owning animated rig and replay root')
    cursor = rig
    seen = set()
    bound = False
    while cursor is not None:
        if cursor in seen or len(seen) >= 64:
            raise ValueError('Cyclic or unbounded snapshot rig ancestry')
        seen.add(cursor)
        if cursor.hide_viewport and (cursor != rig or cursor.get('agr_preview_hidden') is not True):
            raise ValueError('Snapshot requires evaluated rig ancestry; only exporter preview hiding can be borrowed')
        bound |= cursor == root
        cursor = cursor.parent
    if not bound:
        raise ValueError('Snapshot rig is not parented to its owning replay root')
    hidden = rig.hide_viewport
    frame, subframe = scene.frame_current, scene.frame_subframe
    try:
        if hidden:
            rig.hide_viewport = False
        yield
    finally:
        try:
            scene.frame_set(frame, subframe=subframe)
        finally:
            rig.hide_viewport = hidden


def capture_source_snapshot_provider(data,entity,objects,scene,instance,manifest_dir,inventory):
    with evaluated_snapshot_rig(objects.get('rig'), objects.get('root'), scene):
        return _capture_source_snapshot_provider(data,entity,objects,scene,instance,manifest_dir,inventory)


def _capture_source_snapshot_provider(data,entity,objects,scene,instance,manifest_dir,inventory):
    """Capture exact model-order Source rest -> evaluated world for Crossbow.

    Selection/creation admission is deliberately not inferred from these poses.
    The normal exporter must independently qualify its recorded CP bindings.
    """
    from pathlib import Path
    import numpy as np
    from .blender_particle_model import capture_model_bones
    from .particle_model_rest import model_inverse_rest
    from .particle_snapshot_matrices import _matrices
    from .particle_snapshot_provider import binding_header,bind_provider,rigid_matrix
    model=entity.get('model_constraints')or{}
    header=binding_header(entity['id'],model)
    inverse=model_inverse_rest(inventory,resource=model['resource'],resource_sha256=model['resource_sha256'],names=model['names'])
    if inventory['parents']!=model.get('parents'):
        raise ValueError('Snapshot authored rest and rig parent orders differ')
    rig,root=objects.get('rig'),objects.get('root')
    if rig is None or root is None or root==rig or root.get('agr_entity_id')!=entity['id']:
        raise ValueError('Snapshot requires the owning animated rig and replay root')
    cursor=rig;depth=0;bound=False
    while cursor is not None:
        depth+=1
        if depth>64 or cursor.hide_viewport:
            raise ValueError('Snapshot requires visible evaluated rig ancestry')
        if cursor==root:bound=True
        cursor=cursor.parent
    if not bound:raise ValueError('Snapshot rig is not parented to its owning replay root')
    path=Path(entity.get('pose_file',''))
    if not path.is_absolute():path=Path(manifest_dir)/path
    with np.load(path,allow_pickle=False)as poses:
        available=set(poses['ticks'].tolist())-set(entity.get('pose_unavailable_ticks',[]))
    root_ticks={r['tick']for r in entity['samples']if r.get('active',True)and r.get('present',True)}
    capture=capture_model_bones(rig,model,scene,entity_id=entity['id'],
        start_tick=instance['created_tick'],end_tick=data['end_tick'],scene_start_tick=data['start_tick'],
        tick_rate=data['tick_rate'],available_ticks=available&root_ticks,required_names=['crossbow'])
    index=model['names'].index('crossbow');frames=[]
    for tick,rows in zip(capture['ticks'],capture['transforms']):
        # Only this bone is available; do not serialize unselected NaN rows or
        # apply the unrelated Blender/GLB rest matrix to Source SNAP positions.
        matrix=(_matrices([rows[index]])[0]@inverse[index])[:3]
        entry=dict(control_point=0,bone='crossbow',binding_id=header['binding_id'],matrix=rigid_matrix(matrix.reshape(-1).tolist()))
        frames.append(dict(tick=int(tick),snapshot_model_bones=[entry]))
    return bind_provider(data,instance,model,inventory,capture['rig_fingerprint'],frames)


def validate_source_snapshot_rig(provider,data,scene,manifest_dir):
    """Re-evaluate saved package's selected bone; reject edited pose/root actions."""
    from .particle_snapshot_provider import validate_provider
    originals=[i for i in data.get('particle_instances',[])if i.get('id')==provider.get('instance_id')]
    if len(originals)!=1:raise ValueError('Snapshot recorded instance is missing or ambiguous')
    instance=originals[0];validate_provider(provider,data,instance)
    entities=[e for e in data['entities']if e.get('id')==provider['entity_id']]
    rigs=[o for o in scene.objects if o.type=='ARMATURE'and o.get('agr_entity_id')==provider['entity_id']]
    if len(entities)!=1 or len(rigs)!=1 or snapshot_rig_fingerprint(rigs[0])!=provider['rig_fingerprint']:
        raise ValueError('Prepared snapshot does not match its exact evaluated scene rig')
    rig=rigs[0];cursor=rig.parent;root=None;depth=0
    while cursor is not None:
        depth+=1
        if depth>64:raise ValueError('Cyclic snapshot rig ancestry')
        if cursor.get('agr_entity_id')==provider['entity_id']:root=cursor
        cursor=cursor.parent
    actual=capture_source_snapshot_provider(data,entities[0],dict(rig=rig,root=root),scene,instance,manifest_dir,provider['inventory'])
    if actual!=provider:raise ValueError('Prepared snapshot bone or root motion changed; regenerate particles')
