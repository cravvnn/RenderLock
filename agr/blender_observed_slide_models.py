"""Dense model renderer inside the existing package allocation transaction.

No separate rollback/cache owner. Caller validates the entire package before
allocating. All data created here belongs to build_package_effects' existing
snapshot/batch_remove boundary, modeled in agr/ModifierMaskRender.tla.
"""
import math
from .blender_effects import _linear
from .blender_slide_model_frame import import_slide_material,build_slide_model_frame
from .observed_slide_model_states import BACKEND,MODEL,SCOPE,prepare_frames,require


def build_observed_slide_models(package,data,assets,scene,camera):
    import bpy
    rate=data['tick_rate'];fps=scene.render.fps/scene.render.fps_base
    require(all(type(v)in(int,float)and math.isfinite(v)and v>0 for v in(rate,fps)),
            'scene timing unavailable')
    # Complete numeric/asset/color/UV validation before any Blender allocations.
    rows=prepare_frames(package,data,assets)
    collection=bpy.data.collections.new('RenderLock | native slide model frames')
    scene.collection.children.link(collection)
    prototype=import_slide_material(assets['models'][MODEL]['material_bundle']['path'])
    counts={};objects=[]
    for tick,system,row,prepared in rows:
        frame=1+(tick-data['start_tick'])*fps/rate
        name=f"Native slide model | {system['root_ordinal']} | {system['resource'].rsplit('/',1)[1]} | {tick}"
        obj=build_slide_model_frame(name,prepared,prototype,assets['textures'],collection)
        obj['agr_observed_slide_model_tick']=tick
        obj['agr_observed_slide_root_ordinal']=system['root_ordinal']
        obj['agr_particle_resource']=system['resource']
        obj['agr_observed_collection_backend']=BACKEND
        obj['agr_native_reference_sha256']=package['reference']['capture_sha256']
        obj['agr_native_cpu_cohort_sha256']=row['cohort_sha256']
        for at,hidden in((frame-fps/rate,True),(frame,False),(frame+fps/rate,True)):
            obj.hide_render=hidden;obj.hide_viewport=hidden
            obj.keyframe_insert('hide_render',frame=at)
            obj.keyframe_insert('hide_viewport',frame=at)
        _linear(obj);objects.append(obj)
        counts[system['resource']]=counts.get(system['resource'],0)+1
    return dict(backend=BACKEND,objects=len(objects),point_lights=0,particle_tracks=0,
                rendered_collection_layers=len(counts),collection_frame_meshes=len(objects),
                observed_reference_sha256=package['reference']['capture_sha256'],
                layers=[dict(resource=k,model_frames=v)for k,v in sorted(counts.items())],
                limitations=[SCOPE])
