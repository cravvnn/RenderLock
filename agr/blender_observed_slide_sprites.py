"""Whole-frame slide sprite meshes; generic package transaction owns rollback."""
import math
from pathlib import Path
from .observed_slide_sprite_states import BACKEND,validate_package,require
from .observed_slide_sprite_geometry import build_captured_frame
from .blender_slide_sprite_material import build_material


def build_observed_slide_sprites(package,data,assets,scene,camera):
    import bpy
    from mathutils import Matrix,Vector
    from .blender_effects import _linear
    validate_package(package,data)
    require(camera is not None and camera.type=='CAMERA','evaluated scene camera required')
    scale=data.get('metadata',{}).get('coordinate_system',{}).get('unit_scale',.0254)
    rate=data['tick_rate'];fps=scene.render.fps/scene.render.fps_base
    require(type(scale) in (int,float) and math.isfinite(scale) and scale>0 and rate>0 and fps>0,'invalid units/timing')
    samples=package['reference']['capture']['samples'];original=scene.frame_current,scene.frame_subframe
    collection=bpy.data.collections.new('RenderLock | native slide sprite frames');scene.collection.children.link(collection)
    views={};images={};materials={};counts={};objects=[]
    try:
        # Sample before allocations; new mesh visibility must not repeatedly
        # dirty the graph while resolving the hero/camera animation.
        for sample in samples:
            tick=sample['tick'];frame=1+(tick-data['start_tick'])*fps/rate
            scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
            graph=bpy.context.evaluated_depsgraph_get();evaluated=camera.evaluated_get(graph)
            projection=evaluated.calc_matrix_camera(graph,x=scene.render.resolution_x,y=scene.render.resolution_y,
                scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)
            matrix=projection@evaluated.matrix_world.inverted()@Matrix.Diagonal((scale,scale,scale,1));basis=evaluated.matrix_world.to_3x3()
            views[tick]=dict(position=[float(v/scale) for v in evaluated.matrix_world.translation],
                right=list((basis@Vector((1,0,0))).normalized()),up=list((basis@Vector((0,1,0))).normalized()),
                projection=dict(world_to_clip=[list(r) for r in matrix],forward=list((basis@Vector((0,0,-1))).normalized())))
        for sample in samples:
            tick=sample['tick'];frame=1+(tick-data['start_tick'])*fps/rate
            for row in sample['collections']:
                resource=row['resource'];root=row['root_ordinal']
                for native in row['renderers']:
                    index=native['index'];key=resource,index
                    authored=assets['definitions'][resource]['m_Renderers'][index]
                    if key not in materials:
                        material=build_material(authored,assets,images,str(key))
                        material['agr_observed_sprite_material']='native_slide_measured_atlas_inputs_v1';materials[key]=material
                    geometry=build_captured_frame(dict(collection_time=row['collection_time'],points=native['points'],native_inputs=native['inputs']),
                        authored,assets['textures'],views[tick])
                    counts[key]=counts.get(key,0)+len(native['points'])
                    require(geometry['faces'],'positive native array produced no mesh')
                    mesh=bpy.data.meshes.new(f'Native slide | {root} | {Path(resource).stem} | {index} | {tick}')
                    mesh.from_pydata((geometry['vertices']*scale).tolist(),[],geometry['faces']);mesh.materials.append(materials[key])
                    mesh['agr_sprite_native_points']=geometry['native_points'];ratios=geometry['ratios']
                    for name,kind,values in (
                        ('AGR Sprite Depth Ratio','FLOAT',ratios),('AGR Sprite Extra1','FLOAT',geometry['extra']),
                        ('AGR Sprite Depth RGB','FLOAT_VECTOR',[[c*float(r) for c in rgba[:3]] for rgba,r in zip(geometry['colors'],ratios)]),
                        ('AGR Sprite Depth Alpha','FLOAT',[rgba[3]*float(r) for rgba,r in zip(geometry['colors'],ratios)]),
                        ('AGR Sprite Source Position','FLOAT_VECTOR',geometry['source_vertices']*scale)):
                        attr=mesh.attributes.new(name=name,type=kind,domain='POINT')
                        for target,value in zip(attr.data,values):
                            if kind=='FLOAT':target.value=float(value)
                            else:target.vector=list(value)
                    for layer,values in geometry['layers'].items():
                        current=mesh.uv_layers.new(name=f'AGR Sprite UV {layer}');nxt=mesh.uv_layers.new(name=f'AGR Sprite Next UV {layer}')
                        blend=mesh.attributes.new(name=f'AGR Sprite Blend {layer}',type='FLOAT',domain='POINT')
                        for item,value in zip(blend.data,values['blend']):item.value=float(value)
                        for loop in mesh.loops:
                            i=loop.vertex_index;weight=float(ratios[i])
                            current.data[loop.index].uv=[v*weight for v in values['current'][i]]
                            nxt.data[loop.index].uv=[v*weight for v in values['next'][i]]
                    obj=bpy.data.objects.new(mesh.name,mesh);collection.objects.link(obj);objects.append(obj);obj.visible_shadow=False
                    obj['agr_observed_slide_tick']=tick;obj['agr_observed_slide_root_ordinal']=root
                    obj['agr_particle_resource']=resource;obj['agr_observed_sprite_renderer']=index
                    obj['agr_observed_collection_backend']=BACKEND;obj['agr_native_reference_sha256']=package['reference']['capture_sha256']
                    obj['agr_sprite_camera_object']=camera.name;obj['agr_sprite_depth_bias']=geometry['depth_bias']
                    for at,hidden in ((frame-fps/rate,True),(frame,False),(frame+fps/rate,True)):
                        obj.hide_render=hidden;obj.hide_viewport=hidden
                        obj.keyframe_insert('hide_render',frame=at);obj.keyframe_insert('hide_viewport',frame=at)
                    _linear(obj)
    finally:
        scene.frame_set(original[0],subframe=original[1]);bpy.context.view_layer.update()
    return dict(backend=BACKEND,objects=len(objects),point_lights=0,particle_tracks=0,
        rendered_collection_layers=sum(v>0 for v in counts.values()),collection_frame_meshes=len(objects),
        observed_reference_sha256=package['reference']['capture_sha256'],camera_object=camera.name,
        layers=[dict(resource=k[0],renderer=k[1],point_samples=v) for k,v in sorted(counts.items())],
        limitations=['Current property arrays hold between captured ticks; frame-local root ordinals are not lifetimes.',
            'Geometry/UVs use the sampled camera; camera edits require rebaking.',
            'Materials use neutral scene-light study; native depth feathering, lighting/fog/GPU filtering/pixel parity remain incomplete.',
            'Trail/model/light, Mina, orphan/endcap/history and common hero/render clock are not qualified by these meshes.'])
