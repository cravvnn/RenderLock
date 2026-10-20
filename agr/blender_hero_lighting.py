"""Apply explicitly partial probe/energy/reflection lighting to exported heroes."""
import json
import math
import numpy as np
from .source_probe_assets import vector,rotation,bounds_array
from .source_hero_lighting_package import validate_hero_lighting_package


def verify_surface_coverage(scene,roots,package,coordinates):
    """Bound every render-visible evaluated mesh at each integer export frame."""
    from .blender_npr_selection import actor_meshes
    from .source_probe_blending import HIDDEN_MODEL_TRACK
    actors=[]
    for root in roots:
        identity=root.get('agr_entity_id')
        meshes=set(actor_meshes(root))
        if not isinstance(identity,str) or not identity or not meshes:
            raise ValueError('Hero lighting requires distinct actor identities and evaluated meshes')
        actors.append((identity,meshes))
    if len({identity for identity,_ in actors})!=len(actors):raise ValueError('Duplicate hero lighting actor identity')
    meshes={obj for _,objects in actors for obj in objects}
    if not meshes:raise ValueError('Hero lighting requires evaluated meshes')
    bounds=bounds_array(package['plan']['source_bounds']);probes=package['plan']['probes']
    origin=vector(coordinates['origin']);scale=float(coordinates['unit_scale']);yaw=float(coordinates['world_yaw_degrees'])
    if not math.isfinite(scale) or scale<=0 or not math.isfinite(yaw):raise ValueError('Invalid hero lighting coordinates')
    undo=rotation([0,yaw,0]);original=scene.frame_current;subframe=scene.frame_subframe;rows=[];count=0;visible_checks=0
    try:
        for frame in range(scene.frame_start,scene.frame_end+1):
            scene.frame_set(frame);graph=scene.evaluated_depsgraph_get() if hasattr(scene,'evaluated_depsgraph_get') else None
            if graph is None:
                import bpy
                graph=bpy.context.evaluated_depsgraph_get()
            frame_actors=[]
            for identity,objects in actors:
                low=np.full(3,np.inf);high=-low
                for obj in objects:
                    # Disabled body-group alternatives may have no evaluated
                    # viewport instance/matrix. Their later visible frames are
                    # independently checked; they contribute no rendered surface
                    # at this frame.
                    if getattr(obj,'hide_render',False):continue
                    visible_checks+=1
                    evaluated=obj.evaluated_get(graph);mesh=evaluated.to_mesh()
                    try:
                        points=np.empty(len(mesh.vertices)*3,dtype='f4');mesh.vertices.foreach_get('co',points);points=points.reshape(-1,3)
                        matrix=np.array(evaluated.matrix_world);source=(points@matrix[:3,:3].T+matrix[:3,3])@undo.T/scale+origin
                        count+=len(source)
                        if len(source):low=np.minimum(low,source.min(axis=0));high=np.maximum(high,source.max(axis=0))
                    finally:evaluated.to_mesh_clear()
                if not np.isfinite([low,high]).all():
                    root=next(r for r in roots if r.get('agr_entity_id')==identity)
                    if root.get('agr_model_track') and all(getattr(o,'hide_render',False) for o in objects):
                        frame_actors.append(dict(entity_id=identity,visibility=HIDDEN_MODEL_TRACK,surface_checks=0))
                        continue
                    raise ValueError(f'Missing finite hero surface for {identity}')
                low-=.01;high+=.01
                if np.any(low<bounds[:,0]) or np.any(high>bounds[:,1]):raise ValueError(f'Hero lighting selection bounds exceeded for {identity} at frame {frame}')
                import itertools
                corners=np.array(list(itertools.product(*zip(low,high))));full=[]
                for p in probes:
                    local=(corners-vector(p['origin']))@rotation(p['angles']).T
                    # A full convex actor box proves coverage of its triangle
                    # interiors. Different actors need not share one volume.
                    lo=vector(p['box_mins'])+vector(p['edge_fade_dists']);hi=vector(p['box_maxs'])-vector(p['edge_fade_dists'])
                    if np.all(local>=lo) and np.all(local<=hi):full.append(str(p['hammeruniqueid']))
                if not full:raise ValueError(f'No fully covering hero probe for {identity} at frame {frame}; fallback unqualified')
                frame_actors.append(dict(entity_id=identity,low=low.tolist(),high=high.tolist(),full_probes=full))
            rows.append(dict(frame=frame,actors=frame_actors))
    finally:scene.frame_set(original,subframe=subframe)
    return dict(status='verified_integer_frame_bounds',scope='per_actor_convex_bounds',actor_count=len(actors),mesh_count=len(meshes),visible_mesh_frame_checks=visible_checks,vertex_checks=count,frames=rows,
        limits=['Evaluated render-visible hero AABBs include triangle interiors at integer frames; each newly visible mesh is checked at that frame. Fractional frames and runtime probe state remain unverified.'])


def reuse_surface_coverage(coverage,roots):
    """Accept the coverage already verified in this builder run for the same roots.

    Material-only lighting stages do not change evaluated geometry or object
    visibility, so repeating the full per-frame vertex scan would only duplicate
    identical evidence. Saved-scene validation still re-verifies from scratch.
    """
    from .blender_npr_selection import actor_meshes
    if (not isinstance(coverage,dict) or coverage.get('status')!='verified_integer_frame_bounds'
            or coverage.get('scope')!='per_actor_convex_bounds' or not coverage.get('frames')):
        raise ValueError('Verified per-actor surface coverage required')
    identities=[]
    for root in roots:
        identity=root.get('agr_entity_id')
        if not isinstance(identity,str) or not identity or not actor_meshes(root):
            raise ValueError('Hero lighting requires distinct actor identities and evaluated meshes')
        identities.append(identity)
    if (len(set(identities))!=len(identities) or coverage.get('actor_count')!=len(roots)
            or any({actor.get('entity_id') for actor in frame['actors']}!=set(identities)
                   for frame in coverage['frames'])):
        raise ValueError('Reused coverage does not match the current actors')
    return coverage


def apply_hero_lighting(scene,roots,package,world_sha256,manifest_dir,coordinates,*,components=None):
    components=['diffuse','reflections'] if components is None else components
    if components not in (['diffuse'],['diffuse','reflections']):
        raise ValueError('Unqualified hero lighting component selection')
    import bpy
    from .blender_hero_lighting_images import prepare_images
    from .blender_probe_light import create_probe_group
    from .blender_probe_blend import create_blend_group
    from .blender_cube_sampling import create_cube_group
    from .blender_reflections import create_reflections_group
    from .source_reflection_coordinates import normalization_sh
    from .asset_converter import compiled
    from .blender_indirect_energy import create_energy_group
    from .blender_source_material import create_diffuse_material_group
    from .blender_source_position import source_position
    validate_hero_lighting_package(package,world_sha256,manifest_dir)
    coverage=verify_surface_coverage(scene,roots,package,coordinates)
    from .source_probe_blending import contributing_actor_probes
    actor_probes=contributing_actor_probes(package['plan']['probes'],coverage)
    from .blender_npr_selection import actor_meshes
    meshes={obj for root in roots for obj in actor_meshes(root)}
    materials={slot.material for obj in meshes for slot in obj.material_slots}
    from .blender_material_alpha import validate_alpha,alpha_surface
    from .source_material_routes import NPR_ROUTE,PBR_ROUTE,UNLIT_ROUTE
    from .blender_materials import UNLIT_VISIBILITY
    for mat in materials:
        if mat is None or not mat.use_nodes:raise ValueError('Hero material missing')
        if mat.get('vmat',{}).get('IntParams',{}).get('F_UNLIT')==1:
            if mat.get('agr_unlit_visibility_applied')!=UNLIT_VISIBILITY:
                raise ValueError('Source unlit material requires the explicit hero-materials conversion repair: '+mat.name)
            continue
        nodes=mat.node_tree.nodes;bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED'];outputs=[n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        if len(bsdfs)!=1 or len(outputs)!=1:raise ValueError('Unqualified hero material graph')
        surface=outputs[0].inputs['Surface']
        if len(surface.links)!=1 or surface.links[0].from_node!=bsdfs[0]:raise ValueError('Unqualified layered hero material')
        validate_alpha(mat,bsdfs[0])
    images=prepare_images(package,world_sha256,manifest_dir);probes=package['plan']['probes'];raw={};cubes={};coeff={};tints={}
    for p in probes:
        identity=str(p['hammeruniqueid']);prefix=identity.replace(':','_')+'-'
        raw[identity]=create_probe_group(p,{k[len(prefix):]:v for k,v in images['atlas'].items() if k.startswith(prefix)},world_sha256=world_sha256)
        key=(compiled(p['cubemaptexture']),p['array_index']);cubes[identity]=create_cube_group(images['cubes'][key],pack_mips=True);coeff[identity]=normalization_sh(images['coefficients'][key]);tints[identity]=[1,1,1]
    groups={}
    for identity,subset in actor_probes.items():
        ids={str(p['hammeruniqueid']) for p in subset}
        diffuse=create_blend_group(subset,raw)
        reflection=create_reflections_group(subset,*({key:value for key,value in source.items() if key in ids} for source in (cubes,coeff,tints)),package['assumptions']['normalization_ceiling'])
        for tree in (diffuse,reflection):
            tree['agr_hero_probe_ids']=json.dumps([str(p['hammeruniqueid']) for p in subset])
        groups[identity]=(diffuse,reflection)
    energy_group=create_energy_group(images['brdf']);material_group=create_diffuse_material_group();copies={};alpha_rows=[];routes=[]
    tasks=[]
    for root in roots:
        identity=root['agr_entity_id']
        actor_materials={slot.material for obj in actor_meshes(root) for slot in obj.material_slots}
        tasks.extend((identity,original) for original in sorted(actor_materials,key=lambda m:m.name))
    for identity,original in tasks:
        diffuse_group,reflection_group=groups[identity]
        mat=original.copy();mat.name=original.name+' | Source hero indirect '+identity;nodes,links=mat.node_tree.nodes,mat.node_tree.links
        flags=mat.get('vmat',{}).get('IntParams',{})
        route=UNLIT_ROUTE if flags.get('F_UNLIT')==1 else PBR_ROUTE
        mat['agr_source_material_route']=route
        mat['agr_source_hero_lighting']='partial_indirect_reference'
        mat['agr_hero_actor_id']=identity
        mat['agr_hero_probe_ids']=json.dumps([str(p['hammeruniqueid']) for p in actor_probes[identity]])
        routes.append(dict(entity_id=identity,material=mat.name,route=route,
                           applied_components=[] if route==UNLIT_ROUTE else components,
                           status='preserved_unlit_reference' if route==UNLIT_ROUTE else 'applied_generic_reference'))
        if route==UNLIT_ROUTE:
            copies[identity,original]=mat
            continue
        bsdf=next(n for n in nodes if n.type=='BSDF_PRINCIPLED');output=next(n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output)
        geometry=nodes.new('ShaderNodeNewGeometry');normal=bsdf.inputs['Normal'].links[0].from_socket if bsdf.inputs['Normal'].is_linked else geometry.outputs['Normal'];world_normal=normal;view=geometry.outputs['Incoming']
        def source_direction(value):
            if coordinates['world_yaw_degrees']:
                node=nodes.new('ShaderNodeVectorRotate');node.rotation_type='Z_AXIS';node.inputs['Angle'].default_value=-math.radians(coordinates['world_yaw_degrees']);links.new(value,node.inputs['Vector']);return node.outputs['Vector']
            return value
        normal=source_direction(normal);view=source_direction(view)
        position=source_position(nodes,links,geometry.outputs['Position'],coordinates)
        def group(name,tree):
            node=nodes.new('ShaderNodeGroup');node.name=name;node.node_tree=tree;return node
        diffuse=group('AGR hero probes',diffuse_group);reflection=group('AGR hero reflections',reflection_group);energy=group('AGR indirect energy',energy_group);response=group('AGR hero diffuse response',material_group)
        for node in (diffuse,reflection):links.new(position,node.inputs['Position']);links.new(normal,node.inputs['Normal'])
        links.new(view,reflection.inputs['View']);links.new(diffuse.outputs['Irradiance'],reflection.inputs['Irradiance'])
        for old,new in [('Base Color','Albedo'),('Metallic','Metallic'),('Roughness','Roughness')]:
            socket=bsdf.inputs[old]
            for node in ([energy,response] if new!='Roughness' else [energy,reflection]):
                if socket.is_linked:links.new(socket.links[0].from_socket,node.inputs[new])
                else:node.inputs[new].default_value=(socket.default_value[:3] if node==energy else socket.default_value) if old=='Base Color' else socket.default_value
        dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT';links.new(geometry.outputs['Incoming'],dot.inputs[0]);links.new(world_normal,dot.inputs[1]);links.new(dot.outputs['Value'],energy.inputs['NdotV'])
        def vector_op(name,operation,a,b):
            node=nodes.new('ShaderNodeVectorMath');node.name=name;node.operation=operation;links.new(a,node.inputs[0]);links.new(b,node.inputs[1]);return node.outputs[0]
        reduced=vector_op('AGR indirect reduced','MULTIPLY',diffuse.outputs['Irradiance'],energy.outputs['Diffuse Energy'])
        corrected=vector_op('AGR indirect corrected','MULTIPLY',reduced,energy.outputs['Correction']);links.new(corrected,response.inputs['Lighting'])
        multiple=vector_op('AGR indirect multiple','MULTIPLY',diffuse.outputs['Irradiance'],energy.outputs['Multiple'])
        single=vector_op('AGR reflected single','MULTIPLY',reflection.outputs['Reflection'],energy.outputs['Single'])
        combined=vector_op('AGR indirect base sum','ADD',response.outputs['Diffuse'],multiple)
        final=vector_op('AGR hero final sum','ADD',combined,single) if 'reflections' in components else combined
        from .blender_material_emission import add_converted_emission
        final=add_converted_emission(nodes,links,bsdf,final)
        emit=nodes.new('ShaderNodeEmission');links.new(final,emit.inputs['Color'])
        surface,alpha=alpha_surface(mat,bsdf,emit.outputs[0]);links.new(surface,output.inputs['Surface'])
        alpha_rows.append(dict(material=mat.name,entity_id=identity,**alpha))
        mat['agr_source_hero_lighting']='partial_indirect_reference'
        mat['agr_hero_actor_id']=identity
        mat['agr_hero_probe_ids']=json.dumps([str(p['hammeruniqueid']) for p in actor_probes[identity]])
        copies[identity,original]=mat
    for root in roots:
        for obj in actor_meshes(root):
            for slot in obj.material_slots:slot.material=copies[root['agr_entity_id'],slot.material]
    result=dict(status='partial',mode=package['mode'],materials=[m.name for m in copies.values()],
        material_count=len(copies),applied_components=components,
        applied_material_count=sum(bool(row['applied_components']) for row in routes),
        preserved_material_count=sum(not row['applied_components'] for row in routes),
        reflections='applied' if 'reflections' in components else 'not_requested',
        opacity=alpha_rows,
        material_routes=routes,
        coverage=coverage,actor_probes={identity:[str(p['hammeruniqueid']) for p in subset] for identity,subset in actor_probes.items()},
        assumptions=package['assumptions'],limits=package['limits'])
    scene['agr_hero_lighting']=json.dumps(dict(status='partial',assumptions=package['assumptions'],limits=package['limits']))
    return result
