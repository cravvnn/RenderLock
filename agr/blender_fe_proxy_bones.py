"""Bake an authored back-solved proxy onto its exact imported bone outputs."""
from __future__ import annotations


def bake_fe_proxy_bones(rig, definition, frames, fps, collection=None, *, unit_scale=.0254):
    import math
    import bpy
    import numpy as np
    from mathutils import Matrix, Quaternion, Vector
    from .fe_proxy_bones import proxy_bone_outputs, uniform_proxy_colliders, weighted_rigid_delta, skin_proxy_goals, project_proxy_rods
    from .cloth_proxy_settings import proxy_pin_weights
    from .blender_fe_proxy import _capsule_mesh
    from .blender_scene import _curve

    frames = list(frames); scene = bpy.context.scene
    if (definition.get('schema')!='agr_decompiled_fe_bone_proxy_v1'
            or definition.get('bone_basis_yaw_degrees')!=-90.
            or not frames or any(type(f) is not int for f in frames)
            or any(b-a!=1 for a,b in zip(frames,frames[1:]))
            or not math.isfinite(unit_scale) or unit_scale<=0
            or not math.isfinite(fps) or fps<=0
            or abs(scene.render.fps/scene.render.fps_base-fps)>1e-6):
        raise ValueError('Invalid back-solved proxy identity, timing or coordinates')
    if rig.type!='ARMATURE' or not rig.animation_data or not rig.animation_data.action:
        raise ValueError('Back-solved proxy requires its recorded animated rig')
    if rig.get('agr_fe_bone_proxy_bake'):
        raise ValueError('Back-solved proxy already baked; export afresh')
    data=definition['proxy'];fe=definition['physics']['m_pFeModel']
    fits,reverse=proxy_bone_outputs(data,definition['physics'])
    colliders=uniform_proxy_colliders(data,definition['physics'])
    nodes={node:i for i,node in enumerate(data['fe_node_indices']) if node>=0}
    outputs=[row['nNode'] for row in fits]+[row['nBoneCtrl'] for row in reverse]
    names=fe['m_CtrlName'];explicit={names[i] for i in outputs}
    carriers={names[row['nNode']] for _,row in colliders}
    required=explicit|carriers
    if not required<=set(rig.data.bones.keys()):
        raise ValueError('Back-solved proxy lacks exact imported output/carrier bones: '+', '.join(sorted(required-set(rig.data.bones.keys()))))
    affected=set(explicit)
    for name in explicit:affected.update(child.name for child in rig.data.bones[name].children_recursive)
    source_action=rig.animation_data.action
    source_slot=rig.animation_data.action_slot if hasattr(rig.animation_data,'action_slot') else None
    before={key:set(getattr(bpy.data,key)) for key in ('objects','collections','meshes','actions')}
    original_modes={name:rig.pose.bones[name].rotation_mode for name in explicit}
    action=None
    if collection is None:collection=scene.collection
    C=Matrix.Rotation(-math.pi/2,4,'Z')
    def source_matrix(row):
        return Matrix.LocRotScale(Vector(row[:3])*unit_scale,Quaternion((row[7],*row[4:7])),Vector((row[3],)*3))
    rest=[C@source_matrix(row) for row in fe['m_InitPose']]
    try:
        vertices=[C@Vector(v)*unit_scale for v in data['vertices']]
        binds=data.get('joint_rest_transforms')
        if not isinstance(binds,list) or len(binds)!=len(data['joint_names']):
            raise ValueError('Back-solved proxy lacks exact decompiled joint bind frames')
        source_binds={}
        for name,row in zip(data['joint_names'],binds):
            array=np.asarray(row,dtype=float)
            if array.shape!=(4,4) or not np.isfinite(array).all() or abs(np.linalg.det(array[:3,:3]))<1e-8:
                raise ValueError('Invalid back-solved proxy joint bind frame')
            array=array.copy();array[:3,3]*=unit_scale;source_binds[name]=C@Matrix(array.tolist())
        mesh=bpy.data.meshes.new('AGR back-solved cloth proxy');mesh.from_pydata(vertices,[],data['faces']);mesh.update()
        proxy=bpy.data.objects.new(mesh.name,mesh);collection.objects.link(proxy);proxy.parent=rig;proxy.hide_render=True
        for name in data['joint_names']:
            if name in rig.data.bones:proxy.vertex_groups.new(name=name)
        count=len(vertices);slots=data['joint_count']
        if len(data['blend_indices'])!=count*slots or len(data['blend_weights'])!=count*slots:
            raise ValueError('Back-solved proxy skin dimensions differ')
        for i in range(count):
            for slot in range(slots):
                k=i*slots+slot;weight=data['blend_weights'][k];index=data['blend_indices'][k]
                if not math.isfinite(weight) or weight<0 or type(index) is not int or not 0<=index<len(data['joint_names']):
                    raise ValueError('Back-solved proxy has invalid skin weights/indices')
                if weight>1e-8:
                    name=data['joint_names'][index]
                    if name not in proxy.vertex_groups:
                        raise ValueError('Back-solved proxy skin bone is absent: '+name)
                    proxy.vertex_groups[name].add([i],weight,'REPLACE')
        pin=proxy.vertex_groups.new(name='AGR authored proxy goal pins')
        for i,weight in enumerate(proxy_pin_weights(data,definition.get('blender_goal_pins','authored_strength'))):
            if weight>0:pin.add([i],weight,'REPLACE')
        poses=[];worlds=[];goals=[]
        for frame in frames:
            scene.frame_set(frame);bpy.context.view_layer.update()
            poses.append({bone.name:bone.matrix.copy() for bone in rig.pose.bones});worlds.append(rig.matrix_world.copy())
            goals.append(skin_proxy_goals(data,poses[-1],unit_scale))
        # Cloth pins follow exact DMX-inverse-bind skinning. The independently
        # imported rig can relocate weapon bones, so its rest inverse is invalid
        # for these source-space proxy vertices.
        for vertex,point in zip(proxy.data.vertices,goals[0]):vertex.co=point
        proxy.shape_key_add(name='Basis')
        for frame,points in zip(frames,goals):
            key=proxy.shape_key_add(name=f'Goal {frame:06d}')
            for vertex,point in zip(key.data,points):vertex.co=point
            for tick,value in ((frame-1,0),(frame,1),(frame+1,0)):
                key.value=value;key.keyframe_insert('value',frame=tick)
        collision_collection=bpy.data.collections.new('AGR selected proxy colliders');collection.children.link(collision_collection)
        for number,(kind,row) in enumerate(colliders):
            local=Matrix.Identity(4)
            if kind=='m_BoxRigids':
                half=np.asarray(row['vSize'],dtype=float)*unit_scale
                if half.shape!=(3,) or not np.isfinite(half).all() or np.any(half<0):
                    raise ValueError('Invalid back-solved proxy collision box')
                verts=[(x*half[0],y*half[1],z*half[2]) for x,y,z in ((-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),(1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1))]
                faces=[(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)]
                local=source_matrix(row['tmFrame2'])
            else:
                ends=row['vSphere'] if kind=='m_TaperedCapsuleRigids' else [row['vSphere'],row['vSphere']]
                verts,faces=_capsule_mesh({'ends':ends},unit_scale)
            shape=bpy.data.meshes.new('AGR selected authored rigid');shape.from_pydata(verts,[],faces);shape.update()
            obj=bpy.data.objects.new('AGR selected proxy rigid '+str(number),shape);collision_collection.objects.link(obj)
            obj.hide_render=True;obj.display_type='WIRE';obj.rotation_mode='QUATERNION'
            node=row['nNode'];name=names[node]
            for index,frame in enumerate(frames):
                if name not in source_binds:raise ValueError('Cloth collider lacks its exact decompiled bind frame: '+name)
                obj.matrix_world=worlds[index]@poses[index][name]@source_binds[name].inverted()@rest[node]@local
                for prop in ('location','rotation_quaternion','scale'):obj.keyframe_insert(prop,frame=frame)
            obj.modifiers.new('AGR authored selected collision','COLLISION');obj.collision.thickness_outer=.001;obj.collision.thickness_inner=.001
        scene.frame_set(frames[0]);bpy.context.view_layer.update()
        cloth=proxy.modifiers.new('AGR approximate back-solved cloth','CLOTH')
        cloth.settings.quality=24;cloth.settings.mass=.015;cloth.settings.vertex_group_mass=pin.name;cloth.settings.pin_stiffness=50
        cloth.settings.tension_stiffness=100;cloth.settings.compression_stiffness=100;cloth.settings.shear_stiffness=100;cloth.settings.bending_stiffness=.7
        cloth.collision_settings.use_collision=True;cloth.collision_settings.distance_min=.002;cloth.collision_settings.collision_quality=4
        cloth.collision_settings.collection=collision_collection;cloth.collision_settings.use_self_collision=False
        cloth.point_cache.frame_start=frames[0];cloth.point_cache.frame_end=frames[-1]
        samples=[];maximum_rod_residual=0.
        for index,frame in enumerate(frames):
            scene.frame_set(frame);bpy.context.view_layer.update()
            evaluated=proxy.evaluated_get(bpy.context.evaluated_depsgraph_get())
            points=np.asarray([v.co[:] for v in evaluated.data.vertices])
            if points.shape!=(count,3) or not np.isfinite(points).all():raise ValueError('Back-solved proxy simulation produced invalid vertices')
            points,residual=project_proxy_rods(data,definition['physics'],points,goals[index],unit_scale)
            maximum_rod_residual=max(maximum_rod_residual,residual)
            if residual>.005:raise ValueError('Back-solved proxy authored rods did not converge within 5 mm: '+str(residual))
            samples.append(points)
        ordered=sorted(explicit,key=lambda name:len(rig.data.bones[name].parent_recursive))
        values={name:[] for name in ordered};maximum_displacement=0.
        for index,frame in enumerate(frames):
            target={}
            for fit in fits:
                selected=[nodes[row['nNode']] for row in fit['weights']]
                weights=[row['flWeight'] for row in fit['weights']]
                delta=weighted_rigid_delta(np.asarray(vertices)[selected],samples[index][selected],weights)
                name=names[fit['nNode']];target[name]=Matrix(delta.tolist())@source_binds[name]
            for row in reverse:
                name=names[row['nBoneCtrl']];position=nodes[row['nTargetNode']]
                node=row['nBoneCtrl']
                control=poses[index][name]@source_binds[name].inverted()@rest[node]
                control.translation=Vector(samples[index][position])-control.to_3x3()@(Vector(row['vOffset'])*unit_scale)
                target[name]=control@rest[node].inverted()@source_binds[name]
            def parent_target(bone):
                if bone is None:return None
                if bone.name in target:return target[bone.name]
                original=poses[index][bone.name]
                if bone.parent and bone.parent.name in affected:
                    return parent_target(bone.parent)@poses[index][bone.parent.name].inverted()@original
                return original
            for name in ordered:
                bone=rig.data.bones[name]
                kwargs=dict(parent_matrix=parent_target(bone.parent),parent_matrix_local=bone.parent.matrix_local) if bone.parent else {}
                basis=bone.convert_local_to_pose(target[name],bone.matrix_local,invert=True,**kwargs)
                location,rotation,scale=basis.decompose();values[name].append((*location,*rotation,*scale))
            maximum_displacement=max(maximum_displacement,float(np.max(np.linalg.norm(samples[index]-goals[index],axis=1))))
        # Embed the actual authored proxy samples, independent of future rig edits.
        proxy.modifiers.clear();proxy.shape_key_clear();proxy.shape_key_add(name='Basis')
        for frame,points in zip(frames,samples):
            key=proxy.shape_key_add(name=f'Frame {frame:06d}')
            for vertex,point in zip(key.data,points):vertex.co=point
            for tick,value in ((frame-1,0),(frame,1),(frame+1,0)):
                key.value=value;key.keyframe_insert('value',frame=tick)
        action=source_action.copy();action.name=source_action.name+' | Back-solved cloth'
        if hasattr(action,'slots'):
            bags=[bag for layer in action.layers for strip in layer.strips for bag in strip.channelbags]
            if len(action.slots)!=1 or len(bags)!=1:raise ValueError('Back-solved cloth requires one recorded action channel bag')
            channels=bags[0].fcurves
        else:channels=action.fcurves
        selected={rig.pose.bones[name].path_from_id(prop) for name in explicit for prop in
                  ('location','rotation_quaternion','rotation_euler','rotation_axis_angle','scale')}
        for curve in list(channels):
            if curve.data_path in selected:channels.remove(curve)
        for name in ordered:
            bone=rig.pose.bones[name];bone.rotation_mode='QUATERNION';array=np.asarray(values[name])
            for i in range(1,len(array)):
                if array[i-1,3:7]@array[i,3:7]<0:array[i,3:7]*=-1
            for prop,offset,length in (('location',0,3),('rotation_quaternion',3,4),('scale',7,3)):
                for component in range(length):_curve(channels,rig,bone.path_from_id(prop),component,frames,array[:,offset+component])
        rig.animation_data.action=action
        if hasattr(action,'slots'):rig.animation_data.action_slot=action.slots[0]
        unchanged=set(rig.pose.bones.keys())-affected;maximum_body_error=0.
        for i,frame in enumerate(frames):
            scene.frame_set(frame);bpy.context.view_layer.update()
            for name in unchanged:maximum_body_error=max(maximum_body_error,float(np.max(abs(np.asarray(rig.pose.bones[name].matrix)-np.asarray(poses[i][name])))))
        if maximum_body_error>1e-5:raise ValueError('Back-solved proxy changed an unrelated body bone: '+str(maximum_body_error))
        source_action.use_fake_user=True;rig['agr_fe_bone_proxy_bake']=True
        affected_vertices=0;meshes=[]
        for obj in bpy.data.objects:
            if obj.type!='MESH' or not any(mod.type=='ARMATURE' and mod.object==rig for mod in obj.modifiers):continue
            meshes.append(obj.name);groups={group.index for group in obj.vertex_groups if group.name in affected}
            affected_vertices+=sum(any(item.group in groups and item.weight>1e-6 for item in vertex.groups) for vertex in obj.data.vertices)
        scene.frame_set(frames[0])
        return dict(status='baked_partial',schema=definition['schema'],approximation_only=True,
            simulation_vertex_count=count,affected_render_vertex_count=affected_vertices,
            objects=meshes,proxy=proxy.name,cache_embedded=True,sample_count=len(frames),cache_frames=[frames[0],frames[-1]],
            adjusted_cloth_bones=sorted(explicit),affected_descendant_bones=sorted(affected-explicit),
            original_body_action_preserved=source_action.name,body_action_edited=True,
            maximum_unadjusted_body_matrix_difference=maximum_body_error,unadjusted_body_matrix_checks=len(unchanged)*len(frames),
            maximum_proxy_displacement_m=maximum_displacement,authored_collision_shape_count=len(colliders),
            maximum_authored_rod_residual_m=maximum_rod_residual,
            unsupported_parts=[],limitations=[
                'Authored proxy mesh uses Blender cloth approximation, not recorded Source simulation',
                'Compiled fit weights use an absolute weighted rigid fit from authored rest; Source fitting response is not reproduced',
                'Endpoint reverse offsets retain recorded orientation and exact target displacement',
                'Authored goal strength is approximated by Blender pins; Source force and damping response is not reproduced',
                'Legacy frame-valued solver clock; acceleration varies with scene FPS and authored gravity is not mapped',
                'Source collision masks/selections retained; rigid shapes approximated by collision meshes',
                'Compiled rods projected after Blender cloth; collision response is not iterated with the final rod projection',
                'No self collision, map collision, named effect activation or pre-shot simulation history'])
    except Exception:
        rig.animation_data.action=source_action
        if source_slot is not None:rig.animation_data.action_slot=source_slot
        for name,mode in original_modes.items():rig.pose.bones[name].rotation_mode=mode
        bpy.data.batch_remove([item for key,items in before.items() for item in set(getattr(bpy.data,key))-items])
        raise
