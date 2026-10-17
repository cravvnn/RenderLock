"""Bake a decompiled FE proxy onto its matching generated-bone garment.

General interface; bounded studies cover Dynamo and Geist's installed skirt.
Rest-space Surface Deform replaces generated FE influences while ordinary body
vertices retain armature deformation. Does not alter the replay's rig action.
"""
from __future__ import annotations


def bake_fe_proxy(rig, definition, frames, fps, collection=None, *, unit_scale=.0254):
    import bpy,math
    from .installed_cloth import UnsupportedCloth
    frames=list(frames)
    if definition.get('schema')!='agr_decompiled_fe_proxy_v1':raise UnsupportedCloth('Unknown FE proxy schema')
    if not frames or any(type(f)!=int for f in frames) or any(b-a!=1 for a,b in zip(frames,frames[1:])):raise UnsupportedCloth('FE bake requires consecutive integer frames')
    if not math.isfinite(unit_scale) or unit_scale<=0 or not math.isfinite(fps) or fps<=0:raise UnsupportedCloth('Invalid FE bake timing/scale')
    if abs(bpy.context.scene.render.fps/bpy.context.scene.render.fps_base-fps)>1e-6:raise UnsupportedCloth('FE bake FPS does not match scene')
    suffix=definition['mesh_suffix']
    candidates=[o for o in bpy.data.objects if o.type=='MESH' and
                                                    (o.name.endswith(suffix) or (o.name.rpartition('.')[2].isdigit() and o.name.rpartition('.')[0].endswith(suffix))) and
                                                    any(m.type=='ARMATURE' and m.object==rig for m in o.modifiers)]
    if len(candidates)!=1:raise UnsupportedCloth('FE garment must resolve to one rig-bound mesh')
    coat=candidates[0]
    if any(m.type=='SURFACE_DEFORM' for m in coat.modifiers):raise UnsupportedCloth('Garment already has surface deformation; export afresh to rebake')
    if len(coat.data.vertices)<len(definition['render']['vertices']):raise UnsupportedCloth('FE garment is smaller than its decompiled painted surface')
    proxy=definition['proxy'];count=len(proxy['vertices']);slots=proxy['joint_count']
    if len(proxy['blend_weights'])!=count*slots or len(proxy['blend_indices'])!=count*slots or len(proxy['paint']['cloth_enable$0'])!=count:raise UnsupportedCloth('Malformed proxy skin/paint arrays')
    if collection is None:collection=bpy.context.scene.collection
    groups=('objects','collections','meshes','materials','actions')
    before={key:set(getattr(bpy.data,key)) for key in groups}
    modifiers=set(coat.modifiers);vertex_groups=set(coat.vertex_groups)
    masks=[(m,m.vertex_group,m.invert_vertex_group) for m in coat.modifiers if m.type=='ARMATURE']
    pose=rig.data.pose_position
    try:
        return _bake(rig,coat,definition,frames,collection,unit_scale)
    except Exception:
        for m in list(coat.modifiers):
            if m not in modifiers:coat.modifiers.remove(m)
        for group in list(coat.vertex_groups):
            if group not in vertex_groups:coat.vertex_groups.remove(group)
        for m,name,invert in masks:m.vertex_group=name;m.invert_vertex_group=invert
        bpy.data.batch_remove([item for key in groups for item in set(getattr(bpy.data,key))-before[key]])
        raise
    finally:
        rig.data.pose_position=pose


def _bake(rig, coat, definition, frames, collection, unit_scale):
    import bpy,json,math,numpy as np
    from mathutils import Matrix,Vector,kdtree
    from .cloth_proxy_settings import proxy_pin_weights, proxy_clock_settings
    source=definition['render'];proxydata=definition['proxy']
    goal_mode=definition.get('blender_goal_pins','static_only')
    pin_weights=proxy_pin_weights(proxydata,goal_mode)
    clock_mode=definition.get('blender_solver_clock','legacy_frame')
    if clock_mode not in ('legacy_frame','authored_clock_v1'):
        raise ValueError('Unsupported proxy solver clock: '+str(clock_mode))
    clock=None
    if clock_mode=='authored_clock_v1':
        scene=bpy.context.scene
        if tuple(bpy.app.version)!=(5,2,1):
            raise ValueError('Authored proxy clock requires a native validation for this Blender version')
        if scene.render.frame_map_old!=scene.render.frame_map_new:
            raise ValueError('Authored proxy clock does not support scene time remapping')
        clock=proxy_clock_settings(proxydata,unit_scale,scene.gravity,scene.render.fps/scene.render.fps_base,scene.use_gravity)
    points=np.array([v.co[:] for v in coat.data.vertices]);tree=kdtree.KDTree(len(points))
    for i,p in enumerate(points):tree.insert(p,i)
    tree.balance()
    fits=[]
    for angle in (0,90,-90,180):
        C=Matrix.Rotation(math.radians(angle),4,'Z');values=[C@Vector(v)*unit_scale for v in source['vertices']]
        distances=[tree.find(v)[2] for v in values];fits.append((max(distances),angle))
    error,angle=min(fits);print('Source/mesh fit',fits,flush=True);assert error<.005,('Cloth rest surface mismatch',fits)
    C=Matrix.Rotation(math.radians(angle),4,'Z')
    vertices=[C@Vector(v)*unit_scale for v in proxydata['vertices']]
    faces=[f for f in proxydata['faces'] if sum((vertices[f[i]]-vertices[f[0]]).cross(vertices[f[i+1]]-vertices[f[0]]).length for i in range(1,len(f)-1))>1e-10]
    mesh=bpy.data.meshes.new('AGR reconstructed cloth proxy');mesh.from_pydata(vertices,[],faces);mesh.update()
    proxy=bpy.data.objects.new(mesh.name,mesh);collection.objects.link(proxy);proxy.parent=rig
    for name in proxydata['joint_names']:
        if name in rig.data.bones:proxy.vertex_groups.new(name=name)
    for i in range(len(vertices)):
        for slot in range(proxydata['joint_count']):
            k=i*proxydata['joint_count']+slot;weight=proxydata['blend_weights'][k]
            if weight>1e-8:proxy.vertex_groups[proxydata['joint_names'][proxydata['blend_indices'][k]]].add([i],weight,'REPLACE')
    pin=proxy.vertex_groups.new(name='AGR reconstructed static cloth pins')
    for i,weight in enumerate(pin_weights):
        if weight>0:pin.add([i],weight,'REPLACE')
    arm=proxy.modifiers.new('AGR proxy recorded pose','ARMATURE');arm.object=rig
    scene=bpy.context.scene;scene.frame_set(frames[0]);bpy.context.view_layer.update()
    # Preserve authored paint for inspection; FE influences select the deformation mask below.
    paint_tree=kdtree.KDTree(len(source['vertices']))
    for i,v in enumerate(source['vertices']):paint_tree.insert(C@Vector(v)*unit_scale,i)
    paint_tree.balance();group=coat.vertex_groups.new(name='AGR authored cloth enable')
    fe_groups={g.index for g in coat.vertex_groups if g.name.startswith('$cloth_')}
    mapped_vertices=0
    for v in coat.data.vertices:
        _,i,distance=paint_tree.find(v.co)
        if distance>=.005:
            assert not any(g.group in fe_groups and g.weight>1e-6 for g in v.groups),('FE vertex outside decompiled painted surface',v.index,distance)
            continue
        mapped_vertices+=1
        weight=source['cloth_enable$0'][i]
        if weight>0:group.add([v.index],weight,'REPLACE')
    surface=coat.modifiers.new('AGR reconstructed proxy deformation','SURFACE_DEFORM');surface.target=proxy
    fe_mask=coat.vertex_groups.new(name='AGR generated FE surface')
    count=0
    for v in coat.data.vertices:
        if any(g.group in fe_groups and g.weight>1e-6 for g in v.groups):fe_mask.add([v.index],1,'REPLACE');count+=1
    assert count>0,'No generated FE skin region'
    surface.vertex_group=fe_mask.name
    for modifier in coat.modifiers:
        if modifier.type=='ARMATURE':modifier.vertex_group=fe_mask.name;modifier.invert_vertex_group=True
    print('FE surface region',count,'of',len(coat.data.vertices),flush=True)
    rig.data.pose_position='REST';bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active=coat;coat.select_set(True)
    with bpy.context.temp_override(object=coat,active_object=coat):bpy.ops.object.surfacedeform_bind(modifier=surface.name)
    print('Surface binding',surface.is_bound,'proxy faces',len(faces),flush=True)
    assert surface.is_bound,'Surface deform binding failed'
    rig.data.pose_position='POSE';bpy.context.view_layer.update()
    cloth=proxy.modifiers.new('AGR experimental proxy cloth','CLOTH');cloth.settings.quality=24;cloth.settings.mass=.015;cloth.settings.vertex_group_mass=pin.name;cloth.settings.pin_stiffness=50
    cloth.settings.tension_stiffness=100;cloth.settings.compression_stiffness=100;cloth.settings.shear_stiffness=100;cloth.settings.bending_stiffness=.7
    if clock is not None:
        cloth.settings.quality=clock['quality'];cloth.settings.time_scale=clock['time_scale']
        cloth.settings.effector_weights.gravity=clock['gravity_multiplier']
        if not math.isclose(cloth.settings.effector_weights.gravity,clock['gravity_multiplier'],rel_tol=1e-6,abs_tol=1e-8):
            raise ValueError('Authored gravity exceeds Blender effector-weight range')
    collision_collection=bpy.data.collections.new('AGR reconstructed cloth colliders');collection.children.link(collision_collection)
    from agr.blender_surface_cloth import _capsule_mesh
    physics=definition['physics']['m_pFeModel']
    rows=[dict(node=r['nNode'],ends=r['vSphere']) for r in physics['m_TaperedCapsuleRigids']]
    rows += [dict(node=r['nNode'],ends=[r['vSphere'],r['vSphere']]) for r in physics['m_SphereRigids']]
    for i,row in enumerate(rows):
        name=physics['m_CtrlName'][row['node']]
        assert name in rig.pose.bones,name
        verts,polys=_capsule_mesh(row,unit_scale)
        mesh=bpy.data.meshes.new('AGR authored capsule');mesh.from_pydata(verts,[],polys);mesh.update()
        collider=bpy.data.objects.new('AGR reconstructed authored collider '+str(i),mesh);collision_collection.objects.link(collider)
        collider.hide_render=True;collider.display_type='WIRE'
        constraint=collider.constraints.new('COPY_TRANSFORMS');constraint.target=rig;constraint.subtarget=name
        collider.modifiers.new('AGR authored cloth collision','COLLISION');collider.collision.thickness_outer=.001;collider.collision.thickness_inner=.001
        print('Collider',name,flush=True)
    cloth.collision_settings.use_collision=True;cloth.collision_settings.distance_min=.002;cloth.collision_settings.collision_quality=4;cloth.collision_settings.collection=collision_collection;cloth.collision_settings.use_self_collision=False
    cloth.point_cache.frame_start=frames[0];cloth.point_cache.frame_end=frames[-1]
    proxy.hide_render=True
    samples=[]
    for f in frames:
        scene.frame_set(f);deps=bpy.context.evaluated_depsgraph_get();evaluated=proxy.evaluated_get(deps);arr=np.array([v.co[:] for v in evaluated.data.vertices]);assert np.isfinite(arr).all()
        samples.append(arr)
        if f%30==0:print('Cloth frame',f,flush=True)
    
    proxy.modifiers.clear();proxy.shape_key_add(name='Basis')
    for frame,arr in zip(frames,samples):
        key=proxy.shape_key_add(name=f'Frame {frame:06d}')
        for vertex,point in zip(key.data,arr):vertex.co=point
        key.value=0;key.keyframe_insert('value',frame=frame-1);key.value=1;key.keyframe_insert('value',frame=frame);key.value=0;key.keyframe_insert('value',frame=frame+1)
    scene.frame_set(frames[0])
    proxy['agr_fe_proxy_bake']=True
    rig['agr_fe_proxy_cloth_scope']='Approximate reconstructed proxy bake; regenerate after pose, range or FPS edits.'
    return {'status':'baked_partial','schema':definition['schema'],'simulation_vertex_count':len(vertices),
            'coverage_scope':definition.get('scope','Matched FE-weighted garment region only'),
            'affected_render_vertex_count':count,'proxy_faces':len(faces),'rest_fit_error_m':error,
            'matched_painted_surface_vertices':mapped_vertices,'combined_render_vertices':len(coat.data.vertices),
            'blender_goal_pins':goal_mode,
            'blender_solver_clock':clock or {'mode':'legacy_frame'},
            'unmapped_proxy_paint_channels':sorted(set(proxydata['paint'])-({'cloth_enable$0','cloth_goal_strength_v2$0'} if goal_mode=='authored_strength' else {'cloth_enable$0'})-({'cloth_gravity$0'} if clock else set())),
                                    'basis_yaw':angle,'cache_embedded':True,'cache_frames':[frames[0],frames[-1]],
                                    'sample_count':len(frames),'body_action_edited':False,'objects':[coat.name],
                                    'proxy':proxy.name,'surface_bound':surface.is_bound,
                                    'limitations':['Blender cloth approximation, not recorded Source cloth motion',
                                                                                                ('Authored goal strength approximated as Blender pin weights; Source goal forces and damping are not reproduced' if goal_mode=='authored_strength' else 'Static pins use cloth_enable paint; authored goal forces and damping remain unsupported'),
                                                                                                ('Uniform authored gravity and binary solver clock calibrated; Source spring and collision response remain approximations' if clock else 'Legacy frame-valued solver clock: acceleration varies with scene FPS; authored gravity is not mapped'),
                                                                                                'Authored FE capsules/spheres approximated by collision meshes',
                                                                                                'No self-collision, map collision or pre-shot cloth history',
                                                                                                'Only the matched FE-weighted garment region is simulated',
                                                                                                'Regenerate the export after changing pose, range or FPS']}
