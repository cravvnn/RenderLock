"""Blender side of the general 3D model layer: one static copy of the converted model per particle."""
import math

from .particle_generic_model import NOTE, POLICY, key, particle_matrix, placement


def _override_material(profile, texture):
    import bpy
    mat = bpy.data.materials.new('AGR FX model | ' + profile['resource'].rsplit('/', 1)[-1])
    mat.use_nodes = True
    mat['agr_model_particle_material'] = POLICY
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    info = nodes.new('ShaderNodeObjectInfo')
    colour, alpha = info.outputs['Color'], info.outputs['Alpha']
    if profile.get('colour_texture'):
        image = nodes.new('ShaderNodeTexImage')
        image.image = texture(profile['colour_texture'], 0)
        tint = nodes.new('ShaderNodeMixRGB')
        tint.blend_type = 'MULTIPLY'
        tint.inputs[0].default_value = 1
        links.new(image.outputs['Color'], tint.inputs[1])
        links.new(colour, tint.inputs[2])
        colour = tint.outputs[0]
        if profile['translucent'] and not profile['additive']:
            fade = nodes.new('ShaderNodeMath')
            fade.operation = 'MULTIPLY'
            links.new(image.outputs['Alpha'], fade.inputs[0])
            links.new(alpha, fade.inputs[1])
            alpha = fade.outputs[0]
    output = nodes.new('ShaderNodeOutputMaterial')
    if profile['emissive']:
        glow = nodes.new('ShaderNodeEmission')
        links.new(colour, glow.inputs['Color'])
        clear = nodes.new('ShaderNodeBsdfTransparent')
        if profile['additive']:
            links.new(alpha, glow.inputs['Strength'])
            mix = nodes.new('ShaderNodeAddShader')
            links.new(clear.outputs[0], mix.inputs[0])
            links.new(glow.outputs[0], mix.inputs[1])
        else:
            mix = nodes.new('ShaderNodeMixShader')
            links.new(alpha, mix.inputs[0])
            links.new(clear.outputs[0], mix.inputs[1])
            links.new(glow.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], output.inputs['Surface'])
    else:
        shader = nodes.new('ShaderNodeBsdfPrincipled')
        links.new(colour, shader.inputs['Base Color'])
        links.new(alpha, shader.inputs['Alpha'])
        links.new(shader.outputs[0], output.inputs['Surface'])
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
    return mat


def _prototypes(descriptor, texture, cache):
    """Converted meshes of one model (with the override material applied), imported once per build."""
    import bpy
    from .blender_helpers import gltf_import_options
    from .path_io import native_path
    identity = (descriptor['glb_sha256'], descriptor.get('override', {}).get('resource'))
    if identity in cache:
        return cache[identity]
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=native_path(descriptor['glb']), import_pack_images=True, **gltf_import_options(bpy))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    meshes = [obj for obj in imported if obj.type == 'MESH' and len(obj.data.polygons)]
    bpy.context.view_layer.update()
    override = _override_material(descriptor['override'], texture) if descriptor.get('override') else None
    result = []
    for obj in meshes:
        mesh = obj.data
        if override is not None:
            if not mesh.materials:
                mesh.materials.append(override)
            for slot in range(len(mesh.materials)):
                mesh.materials[slot] = override
        mesh.use_fake_user = True
        result.append(dict(mesh=mesh, matrix=obj.matrix_world.copy()))
    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)
    if not result:
        raise ValueError('converted model has no mesh')
    cache[identity] = result
    return result


def build_generic_model_particle(name, renderer, assets, samples, simulation, collection, texture, unit_scale, cache):
    """Returns (objects, notes). A failure removes whatever was created and is reported as a note."""
    import bpy
    from mathutils import Matrix
    from .blender_effects import _linear
    descriptor = assets['generic_models'][key(renderer)]
    reduced, rotation, notes = placement(renderer)
    scene = bpy.context.scene
    frame_step = (scene.render.fps / scene.render.fps_base) / simulation['tick_rate']
    basis = Matrix.Rotation(math.pi / 2, 4, 'Z')
    objects = []
    try:
        matrices = []
        for _, _, particle in samples:
            matrix = particle_matrix(particle, reduced, rotation)
            matrix[:3, 3] *= unit_scale
            matrix[:3, :3] *= unit_scale / .0254
            matrices.append(Matrix(matrix.tolist()))
        for number, prototype in enumerate(_prototypes(descriptor, texture, cache)):
            obj = bpy.data.objects.new(f'{name} | mesh {number}', prototype['mesh'])
            collection.objects.link(obj)
            objects.append(obj)
            obj.rotation_mode = 'QUATERNION'
            obj.visible_shadow = False
            previous = None
            for (tick, _, particle), matrix in zip(samples, matrices):
                frame = 1 + (tick - simulation['start_tick']) * frame_step
                obj.matrix_world = matrix @ basis @ prototype['matrix']
                if previous is not None and obj.rotation_quaternion.dot(previous) < 0:
                    obj.rotation_quaternion.negate()
                previous = obj.rotation_quaternion.copy()
                obj.color = (*particle['color'], max(0, particle['alpha'] * particle['alpha_alternate']))
                obj.hide_render = obj.hide_viewport = False
                for prop in ('location', 'rotation_quaternion', 'scale', 'color', 'hide_render', 'hide_viewport'):
                    obj.keyframe_insert(prop, frame=frame)
            for tick in (samples[0][0] - 1, samples[-1][0] + 1):
                obj.hide_render = obj.hide_viewport = True
                for prop in ('hide_render', 'hide_viewport'):
                    obj.keyframe_insert(prop, frame=1 + (tick - simulation['start_tick']) * frame_step)
            _linear(obj)
            obj['agr_model_particle_policy'] = POLICY
            obj['agr_model_resource'] = descriptor['resource']
            obj['agr_model_source_sha256'] = descriptor['source_sha256']
    except (ValueError, RuntimeError, KeyError) as reason:
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        return [], ['model layer left out: ' + str(reason)]
    if 'override' not in descriptor:
        notes.append("model drawn with its own materials; the particle's colour and fade are not applied")
    return objects, [NOTE] + notes
