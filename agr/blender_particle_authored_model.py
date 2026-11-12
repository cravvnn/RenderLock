"""Bake verified authored model meshes and selectors with authored material slots."""
import math
from pathlib import Path
from .particle_authored_model import validate_authored_model,resolved_sample_selections,require,POLICY
from .particle_model_math import model_matrix

def _prototypes(descriptor,cache):
    import bpy
    from .path_io import native_path
    from .blender_material_groups import resource,_vertex_color
    from .blender_materials import repair_imported_unlit
    key=(descriptor['geometry_sha256'],descriptor['slot_sha256'],descriptor['material_bundle']['sha256'])
    if key in cache:return cache[key]
    original_materials=set(bpy.data.materials);before=set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=native_path(Path(descriptor['directory'])/'slots.glb'),import_pack_images=True)
    imported=list(set(bpy.data.objects)-before)
    require(len(imported)==6 and all(obj.type=='MESH'for obj in imported),'Authored model import object coverage differs')
    source={int(obj.get('agr_source_mesh_index',-1)):obj for obj in imported}
    require(set(source)=={row['source_mesh_index']for row in descriptor['bindings']['meshes']},
        'Authored model imported source identities differ')
    before=set(bpy.data.objects);before_materials=set(bpy.data.materials)
    properties=bpy.ops.import_scene.gltf.get_rna_type().properties
    require('import_unused_materials'in properties,'Authored models require unused-material import support')
    bpy.ops.import_scene.gltf(filepath=native_path(descriptor['material_bundle']['path']),
        import_unused_materials=True,import_pack_images=True)
    require(set(bpy.data.objects)==before,'Authored material bundle created geometry')
    materials=list(set(bpy.data.materials)-before_materials)
    require(len(materials)==len(descriptor['material_bundle']['materials'])
        and {resource(m)for m in materials}==set(descriptor['material_bundle']['materials']),
        'Authored material bundle import coverage differs')
    repair_imported_unlit(materials);converted={resource(m):m for m in materials}
    for obj in imported:
        for index,slot in enumerate(obj.material_slots):
            placeholder=slot.material
            require(placeholder is not None and list(placeholder.get('agr_authored_tint',[]))==[1,1,1,1],
                'Authored particle source slot/tint missing')
            name=placeholder.get('agr_authored_vmat','')+'_c'
            require(name in converted,'Authored particle VMAT slot missing')
            material=converted[name];_vertex_color(material,obj);obj.data.materials[index]=material
    result=[dict(source_index=index,mesh=obj.data,source_matrix=obj.matrix_world.copy())for index,obj in sorted(source.items())]
    for obj in imported:bpy.data.objects.remove(obj,do_unlink=True)
    for material in list(bpy.data.materials):
        if material not in original_materials:
            material.use_fake_user=False
            if material.users==0:bpy.data.materials.remove(material)
    cache[key]=result
    return result

def build_authored_model_particle(name,renderer,descriptor,samples,simulation,collection,unit_scale,cache):
    import bpy
    from mathutils import Matrix
    from .blender_effects import _linear
    validate_authored_model(renderer,descriptor)
    selected=resolved_sample_selections(descriptor,samples,simulation)
    require(type(unit_scale)in(int,float) and math.isfinite(unit_scale) and unit_scale>0,
        'Positive finite authored model unit scale required')
    basis=Matrix.Rotation(math.pi/2,4,'Z')
    prototypes=_prototypes(descriptor,cache);objects=[]
    fps=bpy.context.scene.render.fps/bpy.context.scene.render.fps_base
    def frame(tick):return 1+(tick-simulation['start_tick'])*fps/simulation['tick_rate']
    for prototype in prototypes:
        index=prototype['source_index'];obj=bpy.data.objects.new(name+f' | mesh {index}',prototype['mesh'])
        collection.objects.link(obj);obj.rotation_mode='QUATERNION';obj.visible_shadow=False
        for (tick,_,particle),selection in zip(samples,selected):
            matrix=model_matrix(particle,renderer);matrix[:3,3]*=unit_scale
            matrix[:3,:3]*=unit_scale/.0254
            obj.matrix_world=Matrix(matrix.tolist())@basis@prototype['source_matrix']
            for prop in ('location','rotation_quaternion','scale'):obj.keyframe_insert(prop,frame=frame(tick))
            obj.hide_render=index not in selection['source_mesh_indices'];obj.hide_viewport=obj.hide_render
            for prop in ('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame(tick))
        for tick in (samples[0][0]-1,samples[-1][0]+1):
            obj.hide_render=True;obj.hide_viewport=True
            for prop in ('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame(tick))
        _linear(obj)
        obj['agr_model_particle_policy']=POLICY;obj['agr_model_resource']=descriptor['resource']
        obj['agr_model_source_sha256']=descriptor['source_sha256'];obj['agr_model_geometry_sha256']=descriptor['geometry_sha256']
        obj['agr_source_mesh_index']=index;obj['agr_model_selector']='qualified_body0_subtype0_captured_float26'
        objects.append(obj)
    return objects,[
        'Authored model geometry, VMAT slots and subtype0 selectors are retained; Source NPR lighting, shader dynamics and render-pass parity are approximate.',
        'Authored mesh transforms and selector changes are sampled at replay ticks; root activation and lifetime require independent input qualification.']
