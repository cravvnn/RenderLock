"""Native Blender waist cloth from installed Seven geometry, with baked caches.

Source FE names identify the bounded garment region and capsule carriers. Native
Cloth parameters and low-resolution capsule meshes are explicit approximations.
"""
from __future__ import annotations

import json
import math
import uuid
import heapq
from pathlib import Path

import numpy as np

from .installed_cloth import UnsupportedCloth

PIN_GROUP = 'AGR waist cloth pins'
INDEX_ATTRIBUTE = 'agr_cloth_source_index'


def _mesh_points(mesh):
    points = np.empty(len(mesh.vertices)*3, dtype=np.float64)
    mesh.vertices.foreach_get('co', points)
    return points.reshape((-1,3))


def _weights(obj):
    return [{obj.vertex_groups[g.group].name: float(g.weight) for g in v.groups if g.weight > 1e-8}
            for v in obj.data.vertices]


def _matching_surface(carrier, follower):
    """Exact duplicated front/back geometry can share one simulation cache."""
    if len(carrier.data.vertices) != len(follower.data.vertices):
        return None
    if np.max(abs(np.array(carrier.matrix_world)-np.array(follower.matrix_world))) > 1e-6:
        return None
    a,b = _mesh_points(carrier.data),_mesh_points(follower.data)
    distance = np.linalg.norm(b[:,None]-a[None],axis=-1)
    indices = distance.argmin(axis=1)
    if len(set(indices)) != len(indices) or distance[np.arange(len(b)),indices].max() > 1e-6:
        return None
    wa,wb = _weights(carrier),_weights(follower)
    for j,i in enumerate(indices):
        keys = set(wa[i])|set(wb[j])
        if any(abs(wa[i].get(k,0)-wb[j].get(k,0)) > 1e-5 for k in keys):
            return None
    return indices


def _follow_surface(carrier, follower, indices):
    import bpy
    attribute = follower.data.attributes.new(INDEX_ATTRIBUTE,'INT','POINT')
    attribute.data.foreach_set('value',np.asarray(indices,dtype=np.int32))
    tree=bpy.data.node_groups.new('AGR shared waist cloth positions','GeometryNodeTree')
    tree.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    nodes,links=tree.nodes,tree.links
    source=nodes.new('NodeGroupInput');output=nodes.new('NodeGroupOutput')
    info=nodes.new('GeometryNodeObjectInfo');info.transform_space='ORIGINAL';info.inputs['Object'].default_value=carrier
    info.inputs['As Instance'].default_value=False
    position=nodes.new('GeometryNodeInputPosition')
    sample=nodes.new('GeometryNodeSampleIndex');sample.data_type='FLOAT_VECTOR';sample.domain='POINT';sample.clamp=False
    index=nodes.new('GeometryNodeInputNamedAttribute');index.data_type='INT';index.inputs['Name'].default_value=INDEX_ATTRIBUTE
    setpos=nodes.new('GeometryNodeSetPosition')
    links.new(source.outputs['Geometry'],setpos.inputs['Geometry'])
    links.new(info.outputs['Geometry'],sample.inputs['Geometry'])
    links.new(position.outputs['Position'],sample.inputs['Value'])
    links.new(index.outputs['Attribute'],sample.inputs['Index'])
    links.new(sample.outputs['Value'],setpos.inputs['Position'])
    links.new(setpos.outputs['Geometry'],output.inputs['Geometry'])
    modifier=follower.modifiers.new('AGR shared cloth backfaces','NODES');modifier.node_group=tree
    follower['agr_cloth_carrier']=carrier.name
    return tree


def _capsule_mesh(row, scale):
    """Rounded low-resolution tapered proxy, in its FE carrier's local frame."""
    a,b = (np.array(end,dtype=float)*scale for end in row['ends'])
    axis=b[:3]-a[:3];length=np.linalg.norm(axis)
    axis=axis/length if length>1e-10 else np.array([0.,0.,1.])
    side=np.cross(axis,np.eye(3)[np.argmin(abs(axis))]);side/=np.linalg.norm(side)
    up=np.cross(axis,side);rings=[]
    for center,radius,angles in ((a[:3],a[3],np.linspace(math.pi,math.pi/2,5)),
                                  (b[:3],b[3],np.linspace(math.pi/2,0,5))):
        for angle in angles:
            rings.append([center+axis*(radius*math.cos(angle))+radius*math.sin(angle)*
                          (side*math.cos(j*math.tau/12)+up*math.sin(j*math.tau/12)) for j in range(12)])
    faces=[(i*12+j,i*12+(j+1)%12,(i+1)*12+(j+1)%12,(i+1)*12+j)
           for i in range(len(rings)-1) for j in range(12)]
    return [p for ring in rings for p in ring],faces


def _split_copy(obj, face_indices, *, garment, collection):
    import bpy,bmesh
    copy=obj.copy();copy.data=obj.data.copy()
    copy.name=('AGR waist cloth | ' if garment else 'AGR cloth body | ')+obj.name.rsplit('.',1)[-1]
    collection.objects.link(copy)
    bm=bmesh.new()
    try:
        bm.from_mesh(copy.data);bm.faces.ensure_lookup_table()
        doomed=[f for f in bm.faces if (f.index not in face_indices if garment else f.index in face_indices)]
        bmesh.ops.delete(bm,geom=doomed,context='FACES')
        bmesh.ops.delete(bm,geom=[v for v in bm.verts if not v.link_faces],context='VERTS')
        if garment:
            face_count=len(bm.faces)
            bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00005)
            bmesh.ops.dissolve_degenerate(bm,edges=list(bm.edges),dist=.00001)
            if len(bm.faces)!=face_count:
                raise UnsupportedCloth('Waist geometry has degenerate faces; faithful separation is unsupported')
        bm.to_mesh(copy.data)
    finally:
        bm.free()
    copy['agr_cloth_original_object']=obj.name
    copy['agr_surface_cloth_part']='garment' if garment else 'body'
    return copy


def _pin_boundary(obj, dynamic_names, cut_positions):
    import bmesh
    groups={g.index for g in obj.vertex_groups if g.name in dynamic_names}
    pin=obj.vertex_groups.new(name=PIN_GROUP)
    for vertex in obj.data.vertices:
        weight=sum(g.weight for g in vertex.groups if g.group in groups)
        pin.add([vertex.index],max(0.,1.-weight),'REPLACE')
    bm=bmesh.new()
    try:
        bm.from_mesh(obj.data);layer=bm.verts.layers.deform.verify()
        for vertex in bm.verts:
            # Only the newly cut body interface is fixed. Original open hems
            # remain free unless their source skin weights pin them.
            key=tuple(round(float(x)/1e-6) for x in vertex.co)
            if key in cut_positions:
                vertex[layer][pin.index]=1.
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    return sum(any(g.group==pin.index and g.weight>=1.0 for g in v.groups) for v in obj.data.vertices)


def _native_modifier(obj, collision_collection, frames):
    modifier=obj.modifiers.new('AGR native waist cloth','CLOTH')
    settings=modifier.settings
    settings.quality=16;settings.mass=1.2/len(obj.data.vertices)
    settings.tension_stiffness=100;settings.compression_stiffness=100
    settings.shear_stiffness=100;settings.bending_stiffness=.5
    settings.air_damping=3;settings.vertex_group_mass=PIN_GROUP;settings.pin_stiffness=50
    collision=modifier.collision_settings
    collision.use_collision=True;collision.distance_min=.002
    collision.collision_quality=2;collision.use_self_collision=False
    collision.collection=collision_collection
    cache=modifier.point_cache;cache.frame_start=frames[0];cache.frame_end=frames[-1];cache.use_disk_cache=False
    return modifier



def _fit_waist_capsules(rig, definition, carriers, frames, unit_scale):
    """Fit relevant body proxies below initial cloth and animated seam anchors.

    Full source FE capsules intersect this skinned surface at its initial pose.
    Their centers and carrier frames remain authored; only radii are reduced.
    The fit is an explicit native approximation, never source-engine parity.
    """
    import bpy
    names={'spine_1','pelvis','leg_upper_L','leg_upper_R'}
    rows=[(i,row) for i,row in enumerate(definition['capsules'])
          if definition['control_names'][row['node']] in names]
    if len(rows)!=4:
        raise UnsupportedCloth('Expected one waist contact capsule for each torso, pelvis and thigh carrier')
    fixed={}
    for obj in carriers:
        index=obj.vertex_groups[PIN_GROUP].index
        fixed[obj.name]=np.array([any(g.group==index and g.weight>=1. for g in v.groups)
                                 for v in obj.data.vertices])
        if not fixed[obj.name].any():
            raise UnsupportedCloth('Waist cloth has no fully animated seam anchors')
    factors=np.ones(len(rows));initial_penetration=np.zeros(len(rows));sample_count=0
    clearance=.003
    for frame in frames:
        bpy.context.scene.frame_set(frame);bpy.context.view_layer.update()
        samples=[]
        for obj in carriers:
            points=_evaluate_points(obj)
            if frame!=frames[0]:points=points[fixed[obj.name]]
            matrix=np.array(obj.matrix_world)
            samples.append(points@matrix[:3,:3].T+matrix[:3,3])
        points=np.concatenate(samples);sample_count+=len(points)
        for j,(_,row) in enumerate(rows):
            name=definition['control_names'][row['node']]
            if name not in rig.pose.bones:
                raise UnsupportedCloth('Cloth capsule carrier is missing: '+name)
            matrix=np.array(rig.matrix_world@rig.pose.bones[name].matrix)
            ends=np.asarray(row['ends'],dtype=float)*unit_scale
            a,b=ends[:,:3]@matrix[:3,:3].T+matrix[:3,3]
            axis=b-a;length_squared=float(axis@axis)
            t=np.clip((points-a)@axis/length_squared,0,1) if length_squared>1e-16 else np.zeros(len(points))
            distance=np.linalg.norm(points-(a+t[:,None]*axis),axis=1)
            radius=ends[0,3]*(1-t)+ends[1,3]*t
            factors[j]=min(factors[j],float(((distance-clearance)/radius).min()))
            if frame==frames[0]:initial_penetration[j]=max(0.,float((radius-distance).max()))
    fitted=[];report=[]
    for (index,row),factor,penetration in zip(rows,factors,initial_penetration):
        if not math.isfinite(factor) or factor<.5:
            raise UnsupportedCloth('Waist contact fit would remove more than half a source capsule radius',
                                   diagnostics={'source_capsule_index':index,'radius_factor':float(factor)})
        item=json.loads(json.dumps(row));item['ends'][0][3]*=factor;item['ends'][1][3]*=factor
        fitted.append(item)
        report.append({'source_capsule_index':index,'bone':definition['control_names'][row['node']],
                       'radius_factor':float(factor),'initial_source_penetration_m':float(penetration),
                       'clearance_m':clearance,'frame_count':len(frames),'frame_bounds':[frames[0],frames[-1]],
                       'sample_count':sample_count,'sample_scope':'All first-pose cloth vertices and all-frame fully pinned seam vertices'})
    bpy.context.scene.frame_set(frames[0]);bpy.context.view_layer.update()
    return fitted,report


def _evaluate_points(obj):
    import bpy
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
    try:
        return _mesh_points(mesh)
    finally:
        evaluated.to_mesh_clear()


def _validate_surface_cache(carriers, followers, frames):
    """Measure every cached frame; compare physical free edges to first-pose rest."""
    import bpy
    scene=bpy.context.scene;frames=list(frames)
    surfaces=list(dict.fromkeys([*carriers,*(f[0] for f in followers)]))
    first_rest={};edges_by_name={};free_by_name={};reach_by_name={};pins_by_name={};checks=[];max_follow_error=0.
    scene.frame_set(frames[0]);bpy.context.view_layer.update()
    for carrier in carriers:
        modifier=next(m for m in carrier.modifiers if m.type=='CLOTH')
        modifier.show_viewport=False;bpy.context.view_layer.update()
        first_rest[carrier.name]=_evaluate_points(carrier)
        modifier.show_viewport=True;bpy.context.view_layer.update()
        edges=np.array([e.vertices[:] for e in carrier.data.edges],dtype=int)
        pin=carrier.vertex_groups[PIN_GROUP].index
        fixed=np.array([any(g.group==pin and g.weight>=1.0 for g in v.groups) for v in carrier.data.vertices])
        edges_by_name[carrier.name]=edges
        free_by_name[carrier.name]=~fixed[edges[:,0]] & ~fixed[edges[:,1]]
        pins_by_name[carrier.name]=fixed
        rest=first_rest[carrier.name]
        lengths=np.linalg.norm(rest[edges[:,0]]-rest[edges[:,1]],axis=1)
        adjacency=[[] for _ in rest]
        for (a,b),length in zip(edges,lengths):
            adjacency[a].append((b,float(length)));adjacency[b].append((a,float(length)))
        distance=np.full(len(rest),np.inf);queue=[]
        for i in np.flatnonzero(fixed):
            distance[i]=0.;heapq.heappush(queue,(0.,int(i)))
        while queue:
            length,a=heapq.heappop(queue)
            if length!=distance[a]:continue
            for b,edge in adjacency[a]:
                candidate=length+edge
                if candidate<distance[b]:distance[b]=candidate;heapq.heappush(queue,(candidate,b))
        if not np.isfinite(distance).all():
            raise UnsupportedCloth('A waist cloth component has no fully animated attachment')
        # Two opposite swings can separate current and animated positions by
        # twice the anchor reach; the strain allowance is the same1.5 factor.
        reach_by_name[carrier.name]=np.maximum(.02,3.*distance)
    for frame in frames:
        scene.frame_set(frame);bpy.context.view_layer.update()
        samples={obj.name:_evaluate_points(obj) for obj in surfaces}
        for surface,carrier,indices in followers:
            if np.max(abs(np.array(surface.matrix_world)-np.array(carrier.matrix_world)))>1e-6:
                raise UnsupportedCloth('Waist front/back object transforms diverged')
            error=float(np.linalg.norm(samples[surface.name]-samples[carrier.name][indices],axis=1).max())
            max_follow_error=max(max_follow_error,error)
        for carrier in carriers:
            current=samples[carrier.name]
            modifier=next(m for m in carrier.modifiers if m.type=='CLOTH')
            modifier.show_viewport=False;bpy.context.view_layer.update()
            baseline=_evaluate_points(carrier);modifier.show_viewport=True;bpy.context.view_layer.update()
            edges=edges_by_name[carrier.name];free=free_by_name[carrier.name]
            rest=first_rest[carrier.name]
            rest_lengths=np.linalg.norm(rest[edges[:,0]]-rest[edges[:,1]],axis=1)
            original_lengths=np.linalg.norm(baseline[edges[:,0]]-baseline[edges[:,1]],axis=1)
            current_lengths=np.linalg.norm(current[edges[:,0]]-current[edges[:,1]],axis=1)
            meaningful=original_lengths>.0001
            ratios=current_lengths[meaningful]/original_lengths[meaningful]
            physical=free & (rest_lengths>.0001)
            physical_ratios=current_lengths[physical]/rest_lengths[physical]
            displacement=np.linalg.norm(current-baseline,axis=1)
            movement=float(displacement.max())
            reach_excess=float(np.maximum(displacement-reach_by_name[carrier.name],0).max())
            fixed=pins_by_name[carrier.name]
            pin_error=float(displacement[fixed].max())
            row={'frame':frame,'object':carrier.name,'max_displacement_m':movement,
                 'max_anchor_reach_excess_m':reach_excess,'max_pinned_vertex_error_m':pin_error,
                 'edge_stretch_vs_animated_pose_p95':float(np.percentile(ratios,95)),
                 'free_edge_stretch_vs_first_pose_p95':float(np.percentile(physical_ratios,95)) if len(physical_ratios) else 1.,
                 'max_edge_extension_vs_animated_pose_m':float((current_lengths-original_lengths).max()),
                 'max_free_edge_extension_vs_first_pose_m':float((current_lengths-rest_lengths)[physical].max()) if physical.any() else 0.}
            checks.append(row)
            if (not np.isfinite(current).all() or reach_excess>.0001 or pin_error>.0002
                    or row['free_edge_stretch_vs_first_pose_p95']>1.5
                    or row['max_free_edge_extension_vs_first_pose_m']>.015):
                raise UnsupportedCloth('Native waist cloth failed its surface stability check; original meshes retained',
                                       diagnostics={'surface_checks':checks})
    if max_follow_error>.00001:
        raise UnsupportedCloth(f'Waist front/back surfaces differ by {max_follow_error:.8f} m')
    if any(not next(m for m in carrier.modifiers if m.type=='CLOTH').point_cache.is_baked for carrier in carriers):
        raise UnsupportedCloth('Waist point cache was invalidated during validation')
    return {'surface_checks':checks,'max_shared_surface_error_m':max_follow_error,'validated_frames':len(frames)}


def bake_surface_cloth(rig, definition, frames, fps, collection=None, *, unit_scale=.0254):
    """Create and validate native waist cloth before replacing visible meshes.

    Original source objects remain intact in a hidden backup collection. Only
    the newly created AGR point caches are baked. Existing installed animation
    actions, pose bones and replay roots are never changed.
    """
    import bpy
    from .cloth_definition import SEVEN_SCHEMA
    frames=list(frames)
    if (definition.get('schema')!=SEVEN_SCHEMA or not frames
            or any(not isinstance(f,int) or isinstance(f,bool) for f in frames)
            or any(b-a!=1 for a,b in zip(frames,frames[1:]))
            or not math.isfinite(fps) or not 1<=fps<=240 or not math.isfinite(unit_scale) or unit_scale<=0):
        raise UnsupportedCloth('Native waist cloth requires the validated garment definition and consecutive frames')
    scene=bpy.context.scene
    if abs(scene.render.fps/scene.render.fps_base-fps)>1e-5:
        raise UnsupportedCloth('Scene frame rate does not match cloth bake timing')
    if rig.get('agr_surface_cloth_collection'):
        raise UnsupportedCloth('Native cloth already exists; rebake its AGR point cache after editing animation')
    dynamic_names={n for n in definition['control_names'] if n.startswith('waist_cloth_')
                   and definition['control_names'].index(n) in definition['dynamic_nodes']}
    if len(dynamic_names)!=6:
        raise UnsupportedCloth('Verified waist cloth controls changed')
    source_objects=[obj for obj in scene.objects if obj.type=='MESH' and not obj.get('agr_surface_cloth_part')
                    and any(mod.type=='ARMATURE' and mod.object==rig for mod in obj.modifiers)]
    if not source_objects:
        raise UnsupportedCloth('No skinned mesh is attached to the cloth rig')
    collection=collection or rig.users_collection[0]
    suffix=uuid.uuid4().hex[:8]
    output=bpy.data.collections.new('AGR waist cloth '+suffix);collection.children.link(output)
    output['agr_surface_cloth']=True;output['agr_rig']=rig.name
    colliders=bpy.data.collections.new('AGR cloth body capsules '+suffix);output.children.link(colliders)
    colliders.hide_render=True
    created_meshes=[];created_trees=[];sources=[];bodies=[];surfaces=[];carriers=[];followers=[]
    previous_frame=scene.frame_current;previous_active=bpy.context.view_layer.objects.active
    committed=False
    try:
        scene.frame_set(frames[0]);bpy.context.view_layer.update()
        for original in source_objects:
            groups={g.index for g in original.vertex_groups if g.name in dynamic_names}
            weights=[sum(g.weight for g in v.groups if g.group in groups) for v in original.data.vertices]
            selected={p.index for p in original.data.polygons if max(weights[v] for v in p.vertices)>.05}
            if not selected:
                continue
            if original.data.shape_keys or any(m.type!='ARMATURE' for m in original.modifiers):
                raise UnsupportedCloth('Waist surface has shape keys or additional modifiers that require a separate adapter')
            if max(abs(x-1) for x in original.matrix_world.to_scale())>.002:
                raise UnsupportedCloth('Scaled waist mesh requires a different native cloth length mapping')
            body=_split_copy(original,selected,garment=False,collection=output)
            surface=_split_copy(original,selected,garment=True,collection=output)
            created_meshes.extend((body.data,surface.data))
            if len(body.data.polygons)+len(surface.data.polygons)!=len(original.data.polygons):
                raise UnsupportedCloth('Waist split did not preserve all original faces')
            outside_vertices={v for polygon in original.data.polygons if polygon.index not in selected for v in polygon.vertices}
            cut_positions={tuple(round(float(x)/1e-6) for x in original.data.vertices[v].co) for v in outside_vertices}
            pinned=_pin_boundary(surface,dynamic_names,cut_positions)
            sources.append(original);bodies.append(body);surfaces.append(surface)
            surface['agr_waist_pinned_vertices']=pinned
            surface['agr_surface_cloth_rig']=rig.name
            surface['agr_surface_cloth']=True
            surface['agr_entity_id']=rig.get('agr_entity_id','')
        if not surfaces:
            raise UnsupportedCloth('No faces are weighted to the supported waist controls')
        # Prefer the visible outer surface as carrier; backface meshes keep their
        # topology/materials but sample those same simulated vertex positions.
        for surface in sorted(surfaces,key=lambda o:('backface' in o.name.lower(),o.name)):
            for carrier in carriers:
                indices=_matching_surface(carrier,surface)
                if indices is not None:
                    created_trees.append(_follow_surface(carrier,surface,indices))
                    followers.append((surface,carrier,indices));break
            else:
                carriers.append(surface)
        fitted_capsules,contact_fit=_fit_waist_capsules(rig,definition,carriers,frames,unit_scale)
        for i,row in enumerate(fitted_capsules):
            name=definition['control_names'][row['node']]
            if name not in rig.pose.bones:
                raise UnsupportedCloth('Cloth capsule carrier is missing: '+name)
            vertices,faces=_capsule_mesh(row,unit_scale)
            mesh=bpy.data.meshes.new('AGR cloth capsule');mesh.from_pydata(vertices,[],faces);created_meshes.append(mesh)
            obj=bpy.data.objects.new('AGR cloth capsule '+str(i),mesh);colliders.objects.link(obj)
            obj.hide_render=True;obj.display_type='WIRE';obj['agr_cloth_collision_proxy']=True
            constraint=obj.constraints.new('COPY_TRANSFORMS');constraint.target=rig;constraint.subtarget=name
            constraint.target_space=constraint.owner_space='WORLD'
            obj.modifiers.new('AGR cloth contact','COLLISION')
            obj.collision.thickness_outer=.001;obj.collision.thickness_inner=.001
        for carrier in carriers:
            modifier=_native_modifier(carrier,colliders,frames)
            carrier['agr_native_cloth_carrier']=True
            bpy.context.view_layer.objects.active=carrier;scene.frame_set(frames[0])
            with bpy.context.temp_override(object=carrier,active_object=carrier,point_cache=modifier.point_cache):
                bpy.ops.ptcache.bake(bake=True)
            if not modifier.point_cache.is_baked:
                raise UnsupportedCloth('Native waist point cache did not finish baking')
        validation=_validate_surface_cache(carriers,followers,frames)
        checks=validation['surface_checks'];max_follow_error=validation['max_shared_surface_error_m']
        for proxy in colliders.objects:
            proxy.hide_set(True)
        backup=bpy.data.collections.new('AGR cloth source backup '+suffix)
        collection.children.link(backup);backup.hide_render=True;backup.hide_viewport=True
        backup['agr_surface_cloth_backup']=True
        # Collection visibility protects backups from animated object hide flags.
        for original in sources:
            original['agr_cloth_original_collections']=json.dumps([c.name for c in original.users_collection])
            for parent in list(original.users_collection):
                parent.objects.unlink(original)
            backup.objects.link(original)
        output['agr_source_backup_collection']=backup.name
        rig['agr_surface_cloth_collection']=output.name
        rig['agr_surface_cloth_backup_collection']=backup.name
        rig['agr_cloth_definition']=json.dumps(definition,allow_nan=False)
        rig['agr_cloth_source_root']=str(Path(__file__).resolve().parents[1])
        committed=True
        report={'status':'baked_partial','scope':'Seven waist cloth only',
                'unsupported_parts':['Seven sleeves','Seven leather straps','Seven cables','other heroes'],
                'dynamic_controls':[],
                'simulation_vertex_count':sum(len(o.data.vertices) for o in carriers),
                'affected_render_vertex_count':sum(len(o.data.vertices) for o in surfaces),
                'pinned_vertex_count':sum(int(o['agr_waist_pinned_vertices']) for o in carriers),
                'point_cache_embedded':True,
                'simulated_vertices':sum(len(o.data.vertices) for o in carriers),
                'pinned_vertices':sum(int(o['agr_waist_pinned_vertices']) for o in carriers),
                'rendered_surface_vertices':sum(len(o.data.vertices) for o in surfaces),
                'physics_carriers':len(carriers),'mapped_backface_followers':len(followers),
                'objects':[o.name for o in surfaces],'body_objects':[o.name for o in bodies],
                'source_objects':[o.name for o in sources],'collection':output.name,
                'capsule_proxy_count':len(fitted_capsules),'source_capsule_count':len(definition['capsules']),
                'capsule_fit':contact_fit,
                'sample_count':len(frames),'cache_embedded':True,'cache_frames':[frames[0],frames[-1]],
                'body_action_edited':False,'max_shared_surface_error_m':max_follow_error,'surface_checks':checks,
                'native_settings':{'quality':16,'target_total_mass_kg_per_carrier':1.2,'tension':100,'compression':100,'shear':100,'bending':.5,
                                   'air_damping':3,'pin_stiffness':50,'self_collision':False,'cloth_margin_m':.002},
                'limitations':['Native Blender reconstruction, not recorded historical Source 2 cloth.',
                               'Only the waist surface is simulated; source bone channels remain unchanged.',
                               'Four torso, pelvis and thigh capsules use reduced radii fitted beneath the selected waist pose and animated seam; this is an approximate contact envelope.',
                               'Mesh-wide body collision, self-collision and map collision are absent; limited body intersections can remain outside those fitted proxies.',
                               'Cloth starts from the first selected animation pose; arbitrary earlier cloth state is unavailable.',
                               'After editing animation, rebake the AGR native cloth cache before rendering.']}
        output['agr_surface_cloth_report']=json.dumps(report,allow_nan=False)
        return report
    finally:
        if not committed:
            for obj in list(output.all_objects):
                bpy.data.objects.remove(obj,do_unlink=True)
            bpy.data.collections.remove(colliders);bpy.data.collections.remove(output)
            for mesh in created_meshes:
                if mesh.users==0:bpy.data.meshes.remove(mesh)
            for tree in created_trees:
                if tree.users==0:bpy.data.node_groups.remove(tree)
        bpy.context.view_layer.objects.active=previous_active
        scene.frame_set(previous_frame)
