"""Capture model-rest to animated-world transforms for rigid snapshot bindings.

This is a checked Blender input bridge, not an implementation of the engine's
snapshot operators. It emits source-unit, column-vector 3x4 affine matrices.
"""
import math


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
