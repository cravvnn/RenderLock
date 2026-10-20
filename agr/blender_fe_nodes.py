"""Apply a bounded FE node/rod bake to exact imported cloth bones."""
from __future__ import annotations


def bake_fe_nodes(rig, definition, frames, fps, *, unit_scale=.0254, sample_times=None):
    import math
    import bpy
    import numpy as np
    from mathutils import Matrix, Quaternion, Vector
    from .fe_bone_motion import validate_motion_physics, simulate_node_motion, node_basis
    from .fe_node_rotations import apply_twist_constraints, apply_goal_locks, apply_parent_locks
    from .fe_control_offsets import prepare_goal_transforms
    from .blender_scene import _curve

    fe = validate_motion_physics(definition['physics'])
    frames = list(frames)
    if (not frames or any(type(frame) is not int for frame in frames)
            or any(b-a != 1 for a,b in zip(frames,frames[1:]))
            or abs(bpy.context.scene.render.fps/bpy.context.scene.render.fps_base-fps)>1e-6):
        raise ValueError('FE node bake requires consecutive frames at the scene FPS')
    if rig.type != 'ARMATURE' or rig.animation_data is None or rig.animation_data.action is None:
        raise ValueError('FE node bake requires its recorded animated rig')
    if any(abs(value-1)>1e-4 for value in rig.matrix_world.to_scale()):
        raise ValueError('FE physics-owner constraint scaling is qualified only for unit-scale instances')
    if rig.get('agr_fe_node_bake'):
        raise ValueError('Imported cloth bones already baked; export afresh')
    names = fe['m_CtrlName']; count = len(names)
    yaw = definition.get('bone_basis_yaw_degrees')
    if yaw != -90.:
        raise ValueError('Unqualified installed FE bone coordinate adapter')
    C = Matrix.Rotation(math.radians(yaw),4,'Z')
    source_binds=definition.get('source_bone_rest')
    if not isinstance(source_binds,dict) or not source_binds:
        raise ValueError('Imported FE nodes lack exact decompiled skeleton bind frames')
    source_rest={}
    for name,row in source_binds.items():
        matrix=np.asarray(row,dtype=float)
        if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:3,:3]))<1e-8:
            raise ValueError('Invalid decompiled FE skeleton bind transform')
        matrix=matrix.copy();matrix[:3,3]*=unit_scale
        source_rest[name]=C@Matrix(matrix.tolist())
    rest = []
    for row in fe['m_InitPose']:
        rest.append(C @ Matrix.LocRotScale(Vector(row[:3])*unit_scale,
            Quaternion((row[7],*row[4:7])),Vector((row[3],)*3)))
    parents = {}
    for key in ('m_CtrlOffsets','m_CtrlOsOffsets'):
        for row in fe.get(key,[]):
            child,parent = row['nCtrlChild'],row['nCtrlParent']
            if type(child) is not int or type(parent) is not int or not 0<=child<count or not 0<=parent<count:
                raise ValueError('FE offset index outside control nodes')
            if child in parents and parents[child] != parent:
                raise ValueError('Ambiguous FE virtual-control carrier')
            parents[child] = parent
    skel = fe.get('m_SkelParents',[])
    if len(skel) != count:
        raise ValueError('Missing FE control hierarchy')
    for i,parent in enumerate(skel):
        if i not in parents and type(parent) is int and 0<=parent<count and parent!=i:
            parents[i] = parent
    scene = bpy.context.scene
    source_action = rig.animation_data.action
    source_slot = rig.animation_data.action_slot if hasattr(rig.animation_data,'action_slot') else None
    poses, worlds, goals = [], [], []
    for frame in frames:
        scene.frame_set(frame); bpy.context.view_layer.update()
        pose = {bone.name:bone.matrix.copy() for bone in rig.pose.bones}
        poses.append(pose); worlds.append(rig.matrix_world.copy())
        mapped = {}; visiting = set()
        def target(i):
            if i in mapped:return mapped[i]
            if i in visiting:raise ValueError('Cycle in FE virtual-control carriers')
            visiting.add(i)
            if names[i] in rig.data.bones:
                if names[i] not in source_rest:
                    raise ValueError('FE control bone lacks its exact decompiled bind frame: '+names[i])
                matrix = rig.matrix_world @ pose[names[i]] @ source_rest[names[i]].inverted() @ rest[i]
            else:
                if i not in parents:
                    raise ValueError('FE control lacks an exact bone or authored carrier: '+names[i])
                parent = parents[i]
                matrix = target(parent) @ rest[parent].inverted() @ rest[i]
            visiting.remove(i); mapped[i] = matrix
            return matrix
        goals.append(np.array([target(i) for i in range(count)]))
    raw_goals = np.asarray(goals)
    # Hard/soft controls prepare the animation seed before both physics and
    # rotation reconstruction. They must not overwrite the final simulated
    # cloth transforms as a blanket post-pass. Keep the raw bone mapping below.
    goals=(np.asarray([prepare_goal_transforms(definition['physics'],frame,unit_scale) for frame in raw_goals])
           if any(fe.get(key) for key in ('m_CtrlOffsets','m_CtrlSoftOffsets','m_SphereRigids','m_TaperedCapsuleRigids','m_BoxRigids')) else raw_goals)
    points, solver = simulate_node_motion(definition['physics'],raw_goals,fps,unit_scale,
                                         sample_times=sample_times)
    if not solver.get('native_qualified_compiled_rod_blocks') and solver['maximum_rod_residual_m'] > .005:
        raise ValueError('FE node constraints did not converge within 5 mm: '+str(solver['maximum_rod_residual_m']))
    bases = {row['nNode']:row for row in fe.get('m_NodeBases',[])}
    reverse = {row['nBoneCtrl']:row for row in fe.get('m_ReverseOffsets',[])}
    output = {i for i,name in enumerate(names) if name in rig.data.bones and
              (fe['m_NodeInvMasses'][i]>0 or (i>=fe['m_nRotLockStaticNodes'] and i in bases))}
    output.update(row['nNodeOrient'] for row in fe.get('m_Twists',[]) if names[row['nNodeOrient']] in rig.data.bones)
    output.update(i for i in fe.get('m_LockToGoal',[]) if names[i] in rig.data.bones)
    output.update(row['nCtrlChild'] for row in fe.get('m_LockToParent',[]) if names[row['nCtrlChild']] in rig.data.bones)
    if not output:
        raise ValueError('FE control model has no exact imported cloth-bone outputs')
    for i in reverse:
        if names[i] not in rig.data.bones:
            raise ValueError('FE back-solved bone is absent from the imported rig: '+names[i])
    explicit = {names[i] for i in output}
    affected = set(explicit)
    for name in explicit:
        affected.update(child.name for child in rig.data.bones[name].children_recursive)
    ordered = sorted(output,key=lambda i:len(rig.data.bones[names[i]].parent_recursive))
    values = {i:[] for i in ordered}
    maximum_displacement = 0.
    def transform_rows(matrices):
        rows=[]
        for matrix in matrices:
            location,rotation,scale=Matrix(np.asarray(matrix).tolist()).decompose()
            if min(scale)<=0 or max(scale)-min(scale)>1e-5:
                raise ValueError('FE rotations require positive uniform control scale')
            rotation.normalize();rows.append((*location,sum(scale)/3,rotation.x,rotation.y,rotation.z,rotation.w))
        return np.asarray(rows)
    rest_rows=transform_rows([worlds[0]@matrix for matrix in rest])
    for frame_index,frame in enumerate(frames):
        simulated = points[frame_index]
        previous=transform_rows(goals[frame_index]);current=previous.copy();current[:,:3]=simulated
        for i,row in bases.items():
            rotation=node_basis(simulated,row)@node_basis(goals[frame_index,:,:3,3],row).T@goals[frame_index,i,:3,:3]
            q=Matrix(rotation.tolist()).to_quaternion();q.normalize();current[i,4:]=(q.x,q.y,q.z,q.w)
        # Current native rotation pass: bases/fits/free copies, ordered twists,
        # then reverse offsets. Other operation families remain strict errors.
        current=apply_twist_constraints(rest_rows,previous,current,simulated,fe.get('m_Twists',[]))
        for i,row in reverse.items():
            q=current[i,4:];rotation=np.asarray(Quaternion((q[3],*q[:3])).to_matrix())*current[i,3]
            current[i,:3]=simulated[row['nTargetNode']]-rotation@(np.asarray(row['vOffset'])*unit_scale)
        if fe.get('m_LockToGoal') or fe.get('m_LockToParent'):
            source_current=current.copy();source_current[:,:3]/=unit_scale
            source_previous=previous.copy();source_previous[:,:3]/=unit_scale
            source_current=apply_goal_locks(source_current,source_previous,simulated/unit_scale,fe.get('m_LockToGoal',[]))
            current=apply_parent_locks(source_current,source_previous,fe.get('m_LockToParent',[]))
            current[:,:3]*=unit_scale
        target_matrices = {}
        for i in ordered:
            goal = raw_goals[frame_index,i]
            q=current[i,4:];rotation=np.asarray(Quaternion((q[3],*q[:3])).to_matrix())*current[i,3]
            position = current[i,:3].copy()
            delta = np.eye(4);delta[:3,:3] = rotation;delta[:3,3] = position
            # Keep the exact source-rest offset between this FE control and its bone.
            bone_world = Matrix(delta.tolist()) @ Matrix(goal.tolist()).inverted() @ worlds[frame_index] @ poses[frame_index][names[i]]
            target_matrices[names[i]] = worlds[frame_index].inverted() @ bone_world
            maximum_displacement = max(maximum_displacement,float(np.linalg.norm(position-goal[:3,3])))
        def pose_target(bone):
            if bone is None:return None
            if bone.name in target_matrices:return target_matrices[bone.name]
            original = poses[frame_index][bone.name]
            if bone.parent and bone.parent.name in affected:
                return pose_target(bone.parent) @ poses[frame_index][bone.parent.name].inverted() @ original
            return original
        for i in ordered:
            bone = rig.data.bones[names[i]]
            kwargs = dict(parent_matrix=pose_target(bone.parent),parent_matrix_local=bone.parent.matrix_local) if bone.parent else {}
            basis = bone.convert_local_to_pose(target_matrices[bone.name],bone.matrix_local,invert=True,**kwargs)
            location,rotation,scale = basis.decompose()
            values[i].append((*location,*rotation,*scale))
    action = source_action.copy();action.name = source_action.name+' | Imported cloth'
    try:
        if hasattr(action,'slots'):
            bags = [bag for layer in action.layers for strip in layer.strips for bag in strip.channelbags]
            if len(action.slots)!=1 or len(bags)!=1:
                raise ValueError('FE node bake requires one recorded action slot/channel bag')
            channels = bags[0].fcurves
        else:channels = action.fcurves
        selected_paths = {rig.pose.bones[name].path_from_id(prop) for name in explicit
                          for prop in ('location','rotation_quaternion','rotation_euler','rotation_axis_angle','scale')}
        for curve in list(channels):
            if curve.data_path in selected_paths:channels.remove(curve)
        for i in ordered:
            bone = rig.pose.bones[names[i]];bone.rotation_mode='QUATERNION'
            array = np.asarray(values[i])
            for j in range(1,len(array)):
                if array[j-1,3:7] @ array[j,3:7] < 0:array[j,3:7] *= -1
            for prop,offset,length in (('location',0,3),('rotation_quaternion',3,4),('scale',7,3)):
                for component in range(length):
                    _curve(channels,rig,bone.path_from_id(prop),component,frames,array[:,offset+component])
        rig.animation_data.action = action
        if hasattr(action,'slots'):rig.animation_data.action_slot = action.slots[0]
        unchanged = sorted(set(rig.pose.bones.keys())-affected)
        maximum_body_error = 0.
        for index,frame in enumerate(frames):
            scene.frame_set(frame);bpy.context.view_layer.update()
            for name in unchanged:
                maximum_body_error = max(maximum_body_error,float(np.max(abs(np.asarray(rig.pose.bones[name].matrix)-np.asarray(poses[index][name])))))
        if maximum_body_error > 1e-5:
            raise ValueError('Imported cloth changed an unrelated body-bone matrix: '+str(maximum_body_error))
        source_action.use_fake_user = True
        action['agr_secondary_motion'] = 'Explicit FE node approximation; only named cloth outputs replaced'
        rig['agr_fe_node_bake'] = True
        scene.frame_set(frames[0])
        meshes = [obj for obj in bpy.data.objects if obj.type=='MESH' and
                  any(mod.type=='ARMATURE' and mod.object==rig for mod in obj.modifiers)]
        count_affected = 0
        for mesh in meshes:
            groups = {group.index for group in mesh.vertex_groups if group.name in affected}
            count_affected += sum(any(group.group in groups and group.weight>1e-6 for group in vertex.groups) for vertex in mesh.data.vertices)
        return dict(status='baked_partial',schema=definition['schema'],approximation_only=True,
            simulation_vertex_count=count,affected_render_vertex_count=count_affected,
            sample_count=len(frames),cache_frames=[frames[0],frames[-1]],cache_embedded=True,
            sample_times_seconds=None if sample_times is None else np.asarray(sample_times,dtype=float).tolist(),
            body_action_edited=True,original_body_action_preserved=source_action.name,
            adjusted_cloth_bones=sorted(explicit),affected_descendant_bones=sorted(affected-explicit),
            unadjusted_body_bone_count=len(unchanged),unadjusted_body_matrix_checks=len(unchanged)*len(frames),
            maximum_unadjusted_body_matrix_difference=maximum_body_error,
            maximum_node_displacement_m=maximum_displacement,solver=solver,
            native_qualified_twist_count=len(fe.get('m_Twists',[])),
            native_qualified_goal_lock_count=len(fe.get('m_LockToGoal',[])),
            native_qualified_parent_lock_count=len(fe.get('m_LockToParent',[])),
            objects=[obj.name for obj in meshes],unsupported_parts=[],
            limitations=list(definition.get('limitations',[]))+[
                'Node-basis rotations use the simulated/animated basis delta; ordered twist projection is native qualified',
                'Sphere/capsule/box contact geometry, node radii and friction are native qualified; their integration clock and collision scheduling remain approximate',
                'Authored soft rods and finite compiled passes can retain distance residuals; zero residual is not a native guarantee',
                *(['Rope run metadata is retained; its additional solver semantics are not qualified'] if fe.get('m_Ropes') else []),
                'Only the selected frame range is simulated; regenerate after animation, FPS or explicit sample-time edits'])
    except Exception:
        rig.animation_data.action = source_action
        if source_slot is not None:rig.animation_data.action_slot = source_slot
        bpy.data.actions.remove(action)
        raise
