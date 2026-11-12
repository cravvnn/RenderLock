"""Bake complete observed collection frames through the existing rope renderer."""
import math
from pathlib import Path
from .observed_rope_states import BACKEND, PROFILES, geometry_frames, validate_package, require


def build_observed_ropes(package, data, assets, scene, camera):
    import bpy
    from mathutils import Matrix, Vector
    from .blender_particle_rope import bake_rope_geometry
    from .blender_rope_material import prepare_rope_shader_colors, build_rope_material
    from .particle_rope_inputs import resolve_rope_inputs
    from .particle_texture_layers import enabled_texture_layers
    from .blender_simulated_effects import static_texture_sequence
    from .blender_images import load_packed_image
    validate_package(package, data)
    require(camera is not None and camera.type == 'CAMERA', 'evaluated scene camera required')
    scale = data.get('metadata', {}).get('coordinate_system', {}).get('unit_scale', .0254)
    require(type(scale) in (int, float) and math.isfinite(scale) and scale > 0, 'invalid world units')
    rate = data['tick_rate']; fps = scene.render.fps/scene.render.fps_base
    require(type(rate) in (int, float) and math.isfinite(rate) and rate > 0 and fps > 0,
            'invalid capture/render timing')
    original_frame = scene.frame_current, scene.frame_subframe
    collection = bpy.data.collections.new('RenderLock | native ambient rope frames')
    scene.collection.children.link(collection)
    images = {}; objects = []; reports = []; limitations = []

    def image(resource):
        if resource not in images:
            descriptor = assets['textures'][resource]
            require(not descriptor.get('source_sheet') and not descriptor.get('is_sprite_sheet'),
                    'observed rope profile requires a flat static texture')
            # This is the flat texture's only image, not a fabricated per-particle
            # sequence attribute. Sheet/sequence-driven profiles remain refused.
            seq = static_texture_sequence(descriptor, 0, resource)
            require(len(seq['frames']) == 1, 'unexpected animated rope texture')
            frame = seq['frames'][0]
            images[resource] = load_packed_image(str(Path(descriptor['directory'])/frame['image']),
                check_existing=False, expected_sha256=frame.get('sha256'))
        return images[resource]

    try:
        for resource in sorted(PROFILES):
            definition = assets['definitions'][resource]
            renderer, notes = resolve_rope_inputs(definition['m_Renderers'][0], None, resource, None)
            require(not notes, 'profile unexpectedly needs a substitute input')
            samples = geometry_frames(package['reference'], data, resource)
            for sample in samples:
                frame = 1+(sample['tick']-data['start_tick'])*fps/rate
                scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
                graph = bpy.context.evaluated_depsgraph_get(); evaluated = camera.evaluated_get(graph)
                projection = evaluated.calc_matrix_camera(graph, x=scene.render.resolution_x,
                    y=scene.render.resolution_y, scale_x=scene.render.pixel_aspect_x,
                    scale_y=scene.render.pixel_aspect_y)
                py = float(projection[1][1])
                require(evaluated.data.type in ('PERSP', 'ORTHO') and py > 0, 'camera projection unavailable')
                parameters = dict(type=evaluated.data.type, projection_y=py)
                if evaluated.data.type == 'ORTHO':
                    parameters['height'] = 2/(py*scale)
                world_to_clip = projection@evaluated.matrix_world.inverted()@Matrix.Diagonal((scale, scale, scale, 1))
                parameters.update(world_to_clip=[list(row) for row in world_to_clip],
                    forward=list((evaluated.matrix_world.to_3x3()@Vector((0, 0, -1))).normalized()))
                sample.update(camera_position=[float(v/scale) for v in evaluated.matrix_world.translation],
                              camera_projection=parameters)
            baked = bake_rope_geometry(samples, renderer, scene, collection,
                start_tick=data['start_tick'], tick_rate=rate, unit_scale=scale,
                definition=definition, triangle_pairs=True)
            textures = {layer['m_hTexture']: image(layer['m_hTexture']) for _, layer in
                enabled_texture_layers(renderer.get('m_vecTexturesInput', [])) if layer.get('m_hTexture')}
            for obj in baked:
                prepare_rope_shader_colors(obj.data, renderer)
                material, notes = build_rope_material(renderer, textures, scene_light=(1, 1, 1),
                    age=0., mesh=obj.data)
                obj.data.materials.append(material); limitations.extend(notes)
                obj.name = f'Native ambient rope | {Path(resource).stem} | {obj["agr_rope_geometry_tick"]}'
                obj['agr_observed_collection_backend'] = BACKEND
                obj['agr_particle_resource'] = resource
                obj['agr_native_reference_sha256'] = package['reference']['capture_sha256']
                obj['agr_rope_geometry_only'] = False
                obj['agr_observed_array_identity'] = 'frame-local geometry slots; no particle lifetime inferred'
            objects.extend(baked)
            reports.append(dict(resource=resource, complete_array_samples=len(samples),
                meshes=len(baked), point_samples=sum(len(s['particles']) for s in samples),
                empty_array_samples=sum(not s['particles'] for s in samples)))
    finally:
        scene.frame_set(original_frame[0], subframe=original_frame[1])
        bpy.context.view_layer.update()
    return dict(backend=BACKEND, objects=len(objects), point_lights=0, particle_tracks=0,
        rendered_collection_layers=sum(bool(r['meshes']) for r in reports),
        collection_frame_meshes=len(objects), layers=reports,
        observed_reference_sha256=package['reference']['capture_sha256'],
        camera_binding='sampled_native_collection_rope_meshes', camera_object=camera.name,
        limitations=list(dict.fromkeys(limitations))+[
            'Native point arrays are held between captured ticks; there is no invented per-particle identity, birth, age or seed.',
            'Captured field26 supplies texture position; these pinned rope profiles have no time/random renderer input.',
            'Geometry uses the evaluated Blender camera. Changing its path requires rebuilding rope meshes.',
            'Source shaders, depth passes, scene lighting and native/hero render-clock alignment remain separate; rope scene-light input is neutral RGB.'])
