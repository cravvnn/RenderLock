"""Capture evaluated model-order bone transforms for qualified particle inputs.

This supplies Blender scene poses, not a complete Source entity provider.
Entity offsets, hitboxes, particle sampling and locking remain separate.
"""
import math
import numpy as np
from pathlib import Path


def capture_root_model_points(data, entity, objects, scene, instance, manifest_dir):
    """Complete root CP0/model history for an already qualified effect profile."""
    from mathutils import Matrix
    from .simulated_effect_package import model_constraints_identity
    rig, root = objects['rig'], objects['root']
    model = entity.get('model_constraints')
    if rig is None or not model or not entity.get('pose_file'):
        raise ValueError('Model particles require authored model constraints and baked pose history')
    if root.get('agr_entity_id') != entity['id']:
        raise ValueError('Particle root entity identity mismatch')
    # The replay root carries recorded body angles. Fresh installed bindings
    # declare the client's render-facing basis separately from the GLB import
    # and pose-bake adapters. Do not infer it from an animated root bone or
    # silently impose the installed basis on an authored prepared scene.
    root_yaw = entity.get('render_root_yaw_degrees', 0)
    if (type(root_yaw) not in (int, float)
            or (type(root_yaw) is float and not math.isfinite(root_yaw))):
        raise ValueError('Particle render-root yaw must be a finite number')
    basis = Matrix.Rotation(math.radians(root_yaw % 360), 4, 'Z')
    pose_path = Path(entity['pose_file'])
    if not pose_path.is_absolute():
        pose_path = Path(manifest_dir) / pose_path
    with np.load(pose_path, allow_pickle=False) as poses:
        pose_ticks = set(poses['ticks'].tolist())
    root_ticks = {s['tick'] for s in entity['samples']
                  if s.get('active', True) and s.get('present', True)}
    captured = capture_model_bones(rig, model, scene, entity_id=entity['id'],
        start_tick=instance['created_tick'], end_tick=data['end_tick'],
        scene_start_tick=data['start_tick'], tick_rate=data['tick_rate'],
        available_ticks=root_ticks & pose_ticks)
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    points = []
    try:
        for tick, bones in zip(captured['ticks'], captured['transforms']):
            frame = 1 + (int(tick)-data['start_tick'])*(scene.render.fps/scene.render.fps_base)/data['tick_rate']
            scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
            matrix = root.matrix_world @ basis
            linear = np.asarray(matrix, dtype=float)[:3, :3]
            if (not np.isfinite(np.asarray(matrix)).all()
                    or np.max(abs(linear.T @ linear-np.eye(3))) > 1e-5
                    or abs(np.linalg.det(linear)-1) > 1e-5):
                raise ValueError('Particle root requires a finite rigid world transform')
            pos, rot, _ = matrix.decompose()
            points.append(dict(tick=int(tick), position=[x/.0254 for x in pos],
                rotation=[rot.x, rot.y, rot.z, rot.w], model_bones=bones.tolist()))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
    provenance = {key: captured[key] for key in (
        'entity_id', 'resource', 'resource_sha256', 'rig_fingerprint', 'scope')}
    provenance.update(bone_count=len(captured['names']),
        model_constraints_sha256=model_constraints_identity(model),
        start_tick=instance['created_tick'], end_tick=data['end_tick'],
        provider='selected_hero_root_model', control_point=0,
        render_root_yaw_degrees=root_yaw,
        wearable_search='bypassed_by_unnamed_attachment')
    return points, provenance


def capture_model_bones(rig, model, scene, *, entity_id, start_tick, end_tick,
                        scene_start_tick, tick_rate, available_ticks, unit_scale=.0254):
    """Return Source-unit world transforms without changing the caller's frame.

    Requires the model identity/hierarchy used by the authored-constraint bake.
    Generated cloth or other nonuniform transforms fail instead of being coerced.
    """
    from .model_constraints import ModelConstraints
    from .blender_particle_skinning import snapshot_rig_fingerprint
    if (rig.type != 'ARMATURE' or rig.get('agr_entity_id') != entity_id
            or rig.data.pose_position != 'POSE'):
        raise ValueError('Particle model requires the exact animated entity rig')
    if (model.get('schema_version') != 1 or not model.get('resource')
            or not isinstance(model.get('resource_sha256'), str)
            or len(model['resource_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in model['resource_sha256'])):
        raise ValueError('Particle model identity is incomplete')
    solver = ModelConstraints(model['names'],model['parents'],model['definitions'],
                              attachments=model.get('attachments'),
                              binding_policy=model.get('binding_policy','strict'))
    if set(solver.names) != set(rig.data.bones.keys()):
        raise ValueError('Particle model bone identity mismatch')
    for name,parent in zip(solver.names,solver.parents):
        bone=rig.data.bones[name]
        if (bone.parent.name if bone.parent else None) != (solver.names[parent] if parent >= 0 else None):
            raise ValueError('Particle model hierarchy mismatch')
    if (any(type(v) is not int for v in (start_tick,end_tick,scene_start_tick))
            or start_tick < 0 or end_tick < start_tick or end_tick-start_tick > 100000
            or not math.isfinite(tick_rate) or tick_rate <= 0
            or not math.isfinite(unit_scale) or unit_scale <= 0):
        raise ValueError('Invalid particle model capture range or units')
    old_frame,old_subframe=scene.frame_current,scene.frame_subframe
    ticks=np.arange(start_tick,end_tick+1,dtype=np.int64)
    available=set(available_ticks)
    if any(int(tick) not in available for tick in ticks):
        raise ValueError('Particle model capture lacks complete pose/root history')
    if len(ticks)*len(solver.names)>2_000_000:
        raise ValueError('Particle model capture exceeds bounded pose allocation')
    values=np.empty((len(ticks),len(solver.names),8),dtype=np.float32)
    fps=scene.render.fps/scene.render.fps_base
    if not math.isfinite(fps) or fps<=0:
        raise ValueError('Invalid particle model scene frame rate')
    try:
        for row,tick in enumerate(ticks):
            frame=1+(int(tick)-scene_start_tick)*fps/tick_rate
            scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
            for index,name in enumerate(solver.names):
                matrix=rig.matrix_world@rig.pose.bones[name].matrix
                position,rotation,scale=matrix.decompose()
                if min(scale)<=0 or max(scale)-min(scale)>1e-5:
                    raise ValueError('Particle model requires positive uniform bone scale: '+name)
                linear=np.asarray(matrix,dtype=float)[:3,:3]
                if np.max(abs(linear.T@linear-np.eye(3)*(sum(scale)/3)**2))>1e-5:
                    raise ValueError('Particle model cannot capture sheared bone: '+name)
                values[row,index]=[*(position/unit_scale),sum(scale)/3,
                                   rotation.x,rotation.y,rotation.z,rotation.w]
        if not np.isfinite(values).all():
            raise ValueError('Nonfinite particle model transforms')
    finally:
        scene.frame_set(old_frame,subframe=old_subframe)
    return {'ticks':ticks,'transforms':values,'names':list(solver.names),
            'entity_id':entity_id,'resource':model['resource'],
            'resource_sha256':model['resource_sha256'],
            'rig_fingerprint':snapshot_rig_fingerprint(rig),
            'scope':'Evaluated Blender model-order world poses; Source-unit XYZ, uniform scale, XYZW. '
                    'No assertion of complete native procedural/cloth pose parity.'}
