"""Apply explicitly partial probe/energy/reflection lighting to exported heroes."""
import json
import math
import numpy as np
from .source_probe_assets import vector,rotation,bounds_array
from .source_hero_lighting_package import validate_hero_lighting_package


def verify_surface_coverage(scene,roots,package,coordinates):
    """Bound all evaluated mesh triangles at every integer exported frame."""
    from .blender_npr_selection import actor_meshes
    meshes={obj for root in roots for obj in actor_meshes(root)}
    if not meshes:raise ValueError('Hero lighting requires evaluated meshes')
    bounds=bounds_array(package['plan']['source_bounds']);probes=package['plan']['probes']
    origin=vector(coordinates['origin']);scale=float(coordinates['unit_scale']);yaw=float(coordinates['world_yaw_degrees'])
    if not math.isfinite(scale) or scale<=0 or not math.isfinite(yaw):raise ValueError('Invalid hero lighting coordinates')
    undo=rotation([0,yaw,0]);original=scene.frame_current;subframe=scene.frame_subframe;rows=[];count=0
    try:
        for frame in range(scene.frame_start,scene.frame_end+1):
            scene.frame_set(frame);graph=scene.evaluated_depsgraph_get() if hasattr(scene,'evaluated_depsgraph_get') else None
            if graph is None:
                import bpy
                graph=bpy.context.evaluated_depsgraph_get()
            low=np.full(3,np.inf);high=-low
            for obj in meshes:
                evaluated=obj.evaluated_get(graph);mesh=evaluated.to_mesh()
                try:
                    points=np.empty(len(mesh.vertices)*3,dtype='f4');mesh.vertices.foreach_get('co',points);points=points.reshape(-1,3)
                    matrix=np.array(evaluated.matrix_world);source=(points@matrix[:3,:3].T+matrix[:3,3])@undo.T/scale+origin
                    count+=len(source)
                    if len(source):low=np.minimum(low,source.min(axis=0));high=np.maximum(high,source.max(axis=0))
                finally:evaluated.to_mesh_clear()
            if not np.isfinite([low,high]).all():raise ValueError('Missing finite hero surface')
            low-=.01;high+=.01
            if np.any(low<bounds[:,0]) or np.any(high>bounds[:,1]):raise ValueError(f'Hero lighting selection bounds exceeded at frame {frame}')
            import itertools
            corners=np.array(list(itertools.product(*zip(low,high))));full=[]
            for p in probes:
                local=(corners-vector(p['origin']))@rotation(p['angles']).T
                # Expansion and fade offsets cancel at alpha=1. Requiring one
                # full volume is conservative; unions need separate proof.
                lo=vector(p['box_mins'])+vector(p['edge_fade_dists']);hi=vector(p['box_maxs'])-vector(p['edge_fade_dists'])
                if np.all(local>=lo) and np.all(local<=hi):full.append(str(p['hammeruniqueid']))
            if not full:raise ValueError(f'No fully covering hero probe at frame {frame}; fallback unqualified')
            rows.append(dict(frame=frame,low=low.tolist(),high=high.tolist(),full_probes=full))
    finally:scene.frame_set(original,subframe=subframe)
    return dict(status='verified_integer_frame_bounds',mesh_count=len(meshes),vertex_checks=count,frames=rows,
        limits=['Evaluated whole-hero AABBs include triangle interiors at integer frames; fractional frames and runtime probe state remain unverified.'])


def apply_hero_lighting(scene,roots,package,world_sha256,manifest_dir,coordinates):
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
    from .blender_npr_selection import actor_meshes
    meshes={obj for root in roots for obj in actor_meshes(root)}
    materials={slot.material for obj in meshes for slot in obj.material_slots}
    for mat in materials:
        if mat is None or not mat.use_nodes:raise ValueError('Hero material missing')
        nodes=mat.node_tree.nodes;bsdfs=[n for n in nodes if n.type=='BSDF_PRINCIPLED'];outputs=[n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        if len(bsdfs)!=1 or len(outputs)!=1:raise ValueError('Unqualified hero material graph')
        surface=outputs[0].inputs['Surface'];alpha=bsdfs[0].inputs['Alpha']
        if len(surface.links)!=1 or surface.links[0].from_node!=bsdfs[0] or alpha.is_linked or alpha.default_value!=1:raise ValueError('Unqualified layered/alpha hero material')
    images=prepare_images(package,world_sha256,manifest_dir);probes=package['plan']['probes'];raw={};cubes={};coeff={};tints={}
    for p in probes:
        identity=str(p['hammeruniqueid']);prefix=identity.replace(':','_')+'-'
        raw[identity]=create_probe_group(p,{k[len(prefix):]:v for k,v in images['atlas'].items() if k.startswith(prefix)},world_sha256=world_sha256)
        key=(compiled(p['cubemaptexture']),p['array_index']);cubes[identity]=create_cube_group(images['cubes'][key]);coeff[identity]=normalization_sh(images['coefficients'][key]);tints[identity]=[1,1,1]
    diffuse_group=create_blend_group(probes,raw);reflection_group=create_reflections_group(probes,cubes,coeff,tints,package['assumptions']['normalization_ceiling'])
    energy_group=create_energy_group(images['brdf']);material_group=create_diffuse_material_group();copies={}
    for original in sorted(materials,key=lambda m:m.name):
        mat=original.copy();mat.name=original.name+' | Source hero indirect';nodes,links=mat.node_tree.nodes,mat.node_tree.links
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
        final=vector_op('AGR hero final sum','ADD',combined,single)
        from .blender_material_emission import add_converted_emission
        final=add_converted_emission(nodes,links,bsdf,final)
        emit=nodes.new('ShaderNodeEmission');links.new(final,emit.inputs['Color']);links.new(emit.outputs[0],output.inputs['Surface'])
        mat['agr_source_hero_lighting']='partial_indirect_reference';copies[original]=mat
    for obj in meshes:
        for slot in obj.material_slots:slot.material=copies[slot.material]
    result=dict(status='partial',mode=package['mode'],materials=[m.name for m in copies.values()],coverage=coverage,assumptions=package['assumptions'],limits=package['limits'])
    scene['agr_hero_lighting']=json.dumps(dict(status='partial',assumptions=package['assumptions'],limits=package['limits']))
    return result
