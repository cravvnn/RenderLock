"""Bake installed particle-subset geometry and additive EEVEE materials.

No frame handlers, embedded executable scripts, or alteration of actor actions.
The attached effect manifest records every source resource and approximation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .installed_effects import fade, scalar, UnsupportedEffect
from .coverage_diagnostics import merge_stage_coverage


def particle_state(definition, particle, renderer, instance, tick, tick_rate):
    age = max(0., (tick-particle['born_tick'])/tick_rate)
    if renderer.get('ribbon_instance'):
        alpha = particle['alpha']*renderer['alpha_scale']*min(1., age/max(1e-9, particle['fade_in']))
        stop = instance.get('destroy_tick')
        if stop is not None and tick >= stop:
            alpha *= max(0., 1-(tick-stop)/max(1e-9, particle['decay']*tick_rate))
        return {'scale': [1, 1, 1], 'position': [0, 0, 0], 'alpha': alpha}
    normalized = min(1., age/particle['life'])
    radius = particle['radius']
    endcap_delta = 0.
    alpha = particle['alpha']*fade(definition, normalized)*renderer['alpha_scale']
    for op in definition.get('m_Operators', []):
        if op['_class'] == 'C_OP_InterpolateRadius':
            start, end = op.get('m_flStartTime', 0), op.get('m_flEndTime', 1)
            value = max(0., min(1., (normalized-start)/(end-start))) if end > start else float(normalized >= end)
            radius *= op.get('m_flStartScale', 1)+(op.get('m_flEndScale', 1)-op.get('m_flStartScale', 1))*value
        elif op['_class'] == 'C_OP_SetFloat':
            radius = particle['radius']*scalar(op['m_InputValue'], age=age)
        elif op['_class'] == 'C_OP_RampScalarLinearSimple':
            stop = instance.get('destroy_tick')
            if stop is not None and tick >= stop:
                endcap_delta += op.get('m_Rate', 0)*(tick-stop)/tick_rate
    radius = max(0, radius+endcap_delta)*renderer['radius_scale']
    render = renderer['definition']
    if render['_class'] == 'C_OP_RenderTrails':
        length = particle['trail']*math.sqrt(sum(v*v for v in particle['velocity']))/tick_rate
        length *= min(1., age/max(1e-9, render.get('m_flLengthFadeInTime', 0)))
        length = max(render.get('m_flMinLength', 0), min(render.get('m_flMaxLength', 2000), length))
        radius = min(radius, length)
        scale = [length, 1., radius]
        position = [*particle['offset']]
        position[0] -= render.get('m_flForwardShift', 0)*length
    else:
        scale, position = [radius]*3, particle['offset']
    return {'scale': scale, 'position': position, 'alpha': max(0, alpha)}


def _channels(action):
    if hasattr(action, 'layers'):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    else:
        yield from action.fcurves


def _linear(obj):
    if obj.animation_data and obj.animation_data.action:
        for curve in _channels(obj.animation_data.action):
            for key in curve.keyframe_points:
                key.interpolation = 'CONSTANT' if curve.data_path in ('hide_render', 'hide_viewport') else 'LINEAR'


def _visibility(obj, birth, end):
    # Include negative keys for pre-range tails; no duplicate frame-1 key may
    # override an effect that was already alive when the excerpt begins.
    for frame, value in [(min(0., birth-1), True), (birth-.001, True), (birth, False), (end, True)]:
        obj.hide_render = value
        obj.hide_viewport = value
        obj.keyframe_insert('hide_render', frame=frame)
        obj.keyframe_insert('hide_viewport', frame=frame)


def _texture_sequence(descriptor, sequence):
    result = descriptor['sequences'].get(str(sequence))
    if result is None and not descriptor.get('is_sprite_sheet', len(descriptor['sequences']) > 1):
        result = descriptor['sequences'].get('0')
    if not result or result['status'] != 'supported' or len(result['frames']) != 1:
        raise UnsupportedEffect('Unsupported source texture sequence '+str(sequence))
    return result['frames'][0]['image']


def ensure_particle_transparency(scene):
    """Let Cycles see through overlapping sprite cards and their empty texels.

    Eight transparent bounces can leave black cards behind a muzzle/regen stack.
    Preserve higher user budgets; this setting does not change Eevee materials.
    """
    scene.cycles.transparent_max_bounces = max(64, scene.cycles.transparent_max_bounces)
    return scene.cycles.transparent_max_bounces


def build_effects(data, manifest_dir, scene, camera, entity_objects, fps):
    """entity_objects maps exact entity IDs to {root, rig, model_root}."""
    import bpy
    from mathutils import Matrix, Quaternion, Vector
    descriptor = data.get('effects')
    if not descriptor or not descriptor.get('manifest_file'):
        return {'status': 'not_requested', 'objects': 0}
    manifest_file = Path(descriptor['manifest_file'])
    if not manifest_file.is_absolute():
        manifest_file = Path(manifest_dir)/manifest_file
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported effects manifest version')
    if manifest.get('backend') == 'simulated_states_v1':
        from .simulated_effect_package import build_package_effects
        return build_package_effects(manifest, data, manifest_file, scene, camera)
    if manifest.get('backend'):
        raise ValueError('Unknown prepared particle backend: ' + str(manifest['backend']))
    textures_root = manifest_file.parent
    coordinates = {'unit_scale': .0254, 'origin': [0, 0, 0], 'world_yaw_degrees': 0}
    declared = data.get('metadata', {}).get('coordinate_system', {})
    if isinstance(declared, dict):
        coordinates.update(declared)
    scale = coordinates['unit_scale']
    def frame(tick):
        return 1.+(tick-data['start_tick'])*fps/data['tick_rate']
    collection = bpy.data.collections.new('AGR | recorded effects (supported layers)')
    scene.collection.children.link(collection)
    controls = bpy.data.collections.new('AGR | effect attachment controls')
    scene.collection.children.link(controls)
    report = {'status': 'partial', 'stage': 'rendering', 'objects': 0, 'layers': 0, 'attachment_samples': 0,
              'unsupported': [], 'materials': 0, 'textures': 0, 'limitations': manifest['limitations']}
    images, materials, helpers = {}, {}, {}
    def new_object(name, mesh=None, control=False):
        obj = bpy.data.objects.new(name, mesh)
        (controls if control else collection).objects.link(obj)
        return obj
    # Helpers are sampled before effects. Only these new helper objects receive
    # keys; the baked hero/root actions are read through evaluated pose matrices.
    requested = {layer['attachment'] for layer in manifest['layers']}
    failed_helpers = {}
    for key in sorted(requested):
        try:
            attachment = manifest['attachments'][key]
            rig = entity_objects[attachment['entity_id']]['rig']
            if rig is None:
                raise UnsupportedEffect('Target entity has no imported rig')
            entry = attachment['definition']
            source_bone = entry['m_influenceNames'][0]
            matches = [b for b in rig.pose.bones if b.name.casefold() == source_bone.casefold()]
            if len(matches) != 1:
                raise UnsupportedEffect('Attachment bone absent or ambiguous: '+source_bone)
            q = entry['m_vInfluenceRotations'][0]
            quat = Quaternion((q[3], *q[:3]))
            if quat.magnitude < 1e-8:
                raise UnsupportedEffect('Zero attachment quaternion')
            quat.normalize()
            offset = Matrix.LocRotScale(Vector(entry['m_vInfluenceOffsets'][0])*scale, quat, Vector((1, 1, 1)))
            helper = new_object('AGR effect attachment | '+key+' | '+entry.get('m_name', ''), control=True)
            helper.empty_display_size = .08
            helper.rotation_mode = 'QUATERNION'
            helper['agr_entity_id'] = attachment['entity_id']
            helper['agr_attachment_index'] = attachment['index']
            helper['agr_attachment_bone'] = matches[0].name
            helpers[key] = (helper, rig, matches[0].name, offset)
        except (KeyError, UnsupportedEffect) as exc:
            failed_helpers[key] = str(exc)
    # Blender's keyframe_insert merges keys less than 0.01 frame apart. Mixing
    # fractional particle-death times into this transform bake could overwrite
    # the true pose at an integer render frame. Bake each render frame exactly;
    # fractional effect births sample the interpolated saved attachment curve.
    all_frames = range(scene.frame_start, scene.frame_end+1)
    previous = {}
    original_frame = scene.frame_current
    for f in sorted(all_frames):
        scene.frame_set(math.floor(f), subframe=f-math.floor(f))
        for key, (helper, rig, bone, offset) in helpers.items():
            matrix = rig.matrix_world @ rig.pose.bones[bone].matrix @ offset
            position, rotation, s = matrix.decompose()
            if key in previous and rotation.dot(previous[key]) < 0:
                rotation.negate()
            previous[key] = rotation.copy()
            helper.location, helper.rotation_quaternion, helper.scale = position, rotation, s
            for path in ('location', 'rotation_quaternion', 'scale'):
                helper.keyframe_insert(path, frame=f)
            report['attachment_samples'] += 1
    for helper, *_ in helpers.values():
        _linear(helper)
    def image_for(resource, sequence):
        desc = manifest['textures'][resource]
        image_name = _texture_sequence(desc, sequence)
        path = (textures_root/desc['directory']/image_name).resolve()
        if not path.is_relative_to(textures_root.resolve()) or not path.is_file():
            raise ValueError('Missing or unsafe effect texture '+str(path))
        if str(path) not in images:
            image = bpy.data.images.load(str(path), check_existing=True)
            image.pack()
            images[str(path)] = image
        return images[str(path)]
    def material(particle, settings):
        key = json.dumps([particle['color'], particle['sequence'], settings], sort_keys=True)
        if key in materials:
            return materials[key]
        mat = bpy.data.materials.new('AGR | source additive particle')
        mat.use_nodes = True
        if hasattr(mat, 'surface_render_method'):
            mat.surface_render_method = 'BLENDED'
        elif hasattr(mat, 'blend_method'):
            mat.blend_method = 'BLEND'
        mat.use_backface_culling = False
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        nodes.clear()
        info = nodes.new('ShaderNodeObjectInfo')
        uv = nodes.new('ShaderNodeTexCoord')
        color = nodes.new('ShaderNodeRGB')
        authored_color = particle['color']
        for tex in settings['definition']['m_vecTexturesInput']:
            if tex.get('m_bReplaceTextureWithGradient'):
                stops = tex.get('m_Gradient', {}).get('m_Stops', [])
                if stops:
                    authored_color = [v/255 for v in stops[0]['m_Color'][:3]]
        color.outputs[0].default_value = (*authored_color, 1)
        rgb, alpha = color.outputs[0], info.outputs['Alpha']
        if settings.get('ribbon_instance'):
            attribute = nodes.new('ShaderNodeAttribute')
            attribute.attribute_name = 'agr_ribbon_taper'
            taper = nodes.new('ShaderNodeMath')
            taper.operation = 'MULTIPLY'
            links.new(alpha, taper.inputs[0])
            links.new(attribute.outputs['Fac'], taper.inputs[1])
            alpha = taper.outputs[0]
        for index, texture in enumerate(settings['definition']['m_vecTexturesInput']):
            resource = texture.get('m_hTexture')
            if not resource:
                continue
            tex = nodes.new('ShaderNodeTexImage')
            tex.image = image_for(resource, particle['sequence'])
            mapping = nodes.new('ShaderNodeMapping')
            mapping.name = 'AGR source texture mapping '+str(index)
            controls_ = settings['uv_controls'][index]
            mapping.inputs['Scale'].default_value = (controls_['scale_u'], controls_['scale_v'], 1)
            mapping.inputs['Location'].default_value = (controls_['offset_u'], controls_['offset_v'], 0)
            links.new(uv.outputs['UV'], mapping.inputs['Vector'])
            links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
            multiply = nodes.new('ShaderNodeMixRGB')
            multiply.blend_type = 'MULTIPLY'
            multiply.inputs[0].default_value = 1
            links.new(rgb, multiply.inputs[1])
            links.new(tex.outputs['Color'], multiply.inputs[2])
            rgb = multiply.outputs[0]
            mul_alpha = nodes.new('ShaderNodeMath')
            mul_alpha.operation = 'MULTIPLY'
            links.new(alpha, mul_alpha.inputs[0])
            links.new(tex.outputs['Alpha'], mul_alpha.inputs[1])
            alpha = mul_alpha.outputs[0]
        power = nodes.new('ShaderNodeMath')
        power.operation = 'MULTIPLY'
        links.new(alpha, power.inputs[0])
        power.inputs[1].default_value = settings['strength']
        emission = nodes.new('ShaderNodeEmission')
        links.new(rgb, emission.inputs['Color'])
        links.new(power.outputs[0], emission.inputs['Strength'])
        transparent = nodes.new('ShaderNodeBsdfTransparent')
        add = nodes.new('ShaderNodeAddShader')
        links.new(transparent.outputs[0], add.inputs[0])
        links.new(emission.outputs[0], add.inputs[1])
        output = nodes.new('ShaderNodeOutputMaterial')
        links.new(add.outputs[0], output.inputs['Surface'])
        mat.diffuse_color = (*authored_color, 1)
        mat['agr_effect_approximation'] = 'Source RGBA texture multiplication; Blender additive emission'
        materials[key] = mat
        return mat
    for layer in manifest['layers']:
        created = []
        try:
            if layer['attachment'] in failed_helpers:
                raise UnsupportedEffect(failed_helpers[layer['attachment']])
            helper = helpers[layer['attachment']][0]
            for particle in layer['particles']:
                born, end = frame(particle['born_tick']), frame(particle['end_tick'])
                ribbon = layer['kind'] == 'recorded_ring_ribbon'
                if ribbon:
                    control = new_object('AGR recorded ribbon control | '+layer['instance']['id'], control=True)
                    created.append(control)
                    control.rotation_mode = 'QUATERNION'
                    basis = Matrix.Rotation(math.radians(coordinates['world_yaw_degrees']), 4, 'Z')
                    previous_q = None
                    for transform in particle['transforms']:
                        pos = transform['position']
                        q = transform.get('orientation', {})
                        rotation = Quaternion((q.get('w', 1), q.get('x', 0), q.get('y', 0), q.get('z', 0)))
                        if rotation.magnitude < 1e-8:
                            raise UnsupportedEffect('Zero recorded control-point quaternion')
                        rotation.normalize()
                        rotation = basis.to_quaternion() @ rotation
                        if previous_q is not None and rotation.dot(previous_q) < 0:
                            rotation.negate()
                        previous_q = rotation.copy()
                        control.location = basis.to_3x3() @ ((Vector([pos.get(k, 0) for k in 'xyz'])-Vector(coordinates['origin']))*scale)
                        control.rotation_quaternion = rotation
                        for path in ('location', 'rotation_quaternion'):
                            control.keyframe_insert(path, frame=frame(transform['tick']))
                    _linear(control)
                for index, settings in enumerate(particle['renderers']):
                    mat = material(particle, settings)
                    trail = settings['definition']['_class'] == 'C_OP_RenderTrails'
                    name = f"AGR FX | {layer['instance']['id']} | {Path(layer['resource']).stem} | {particle['index']}.{index}"
                    mesh = bpy.data.meshes.new(name)
                    if ribbon:
                        centers = [Vector((particle['radius']*math.cos(i*2*math.pi/particle['orbit']+math.radians(particle['yaw'])),
                                           particle['radius']*math.sin(i*2*math.pi/particle['orbit']+math.radians(particle['yaw'])), 0))
                                   +Vector(particle['offset']) for i in range(particle['count'])]
                        vertices = [(center+Vector((0, 0, sign*particle['width'])))*scale for center in centers for sign in (-1, 1)]
                        faces = [(2*i, 2*i+1, 2*i+3, 2*i+2) for i in range(len(centers)-1)]
                        mesh.from_pydata(vertices, [], faces)
                        attribute = mesh.attributes.new('agr_ribbon_taper', 'FLOAT', 'POINT')
                        for i, item in enumerate(attribute.data):
                            item.value = particle['taper'][i//2]
                    else:
                        vertices = [(0, 0, -1), (1, 0, -1), (1, 0, 1), (0, 0, 1)] if trail else [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
                        mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
                    mesh.materials.append(mat)
                    uv = mesh.uv_layers.new()
                    coords = [(0, 0), (0, 1), (1, 1), (1, 0)] if trail else [(0, 0), (1, 0), (1, 1), (0, 1)]
                    if ribbon:
                        distance = [0.]
                        for a, b in zip(centers, centers[1:]):
                            distance.append(distance[-1]+(b-a).length)
                        world_size = settings['definition'].get('m_flTextureVWorldSize', 1)
                        if world_size <= 0:
                            raise UnsupportedEffect('Non-positive ribbon texture world size')
                        for polygon in mesh.polygons:
                            for li in polygon.loop_indices:
                                vi = mesh.loops[li].vertex_index
                                uv.data[li].uv = (vi%2, distance[vi//2]/world_size)
                    else:
                        for item, coordinate in zip(uv.data, coords):
                            item.uv = coordinate
                    obj = new_object(name, mesh)
                    created.append(obj)
                    obj.parent = control if ribbon else helper
                    obj['agr_particle_instance'] = layer['instance']['id']
                    obj['agr_particle_resource'] = layer['resource']
                    obj['agr_particle_born_tick'] = particle['born_tick']
                    obj['agr_particle_end_tick'] = particle['end_tick']
                    obj['agr_effect_subset'] = True
                    obj.visible_shadow = False
                    if not ribbon:
                        constraint = obj.constraints.new('LOCKED_TRACK' if trail else 'TRACK_TO')
                        constraint.target = camera
                        if trail:
                            constraint.track_axis, constraint.lock_axis = 'TRACK_Y', 'LOCK_X'
                        else:
                            constraint.track_axis, constraint.up_axis = 'TRACK_Z', 'UP_Y'
                    start_sample, end_sample = max(1, born), min(scene.frame_end, end)
                    sample_frames = sorted({start_sample, end_sample, *range(math.ceil(start_sample), math.ceil(end_sample))})
                    for f in sample_frames:
                        tick = data['start_tick']+(f-1)*data['tick_rate']/fps
                        state = particle_state(layer['definition'], particle, settings, layer['instance'], tick, data['tick_rate'])
                        obj.location = Vector(state['position'])*scale
                        obj.scale = (1, 1, 1) if ribbon else tuple(max(1e-9, v*scale) if not (trail and i == 1) else 1 for i, v in enumerate(state['scale']))
                        obj.color = (*particle['color'], state['alpha'])
                        for path in ('location', 'scale', 'color'):
                            obj.keyframe_insert(path, frame=f)
                        if ribbon:
                            age = (tick-particle['born_tick'])/data['tick_rate']
                            for tex_i, uv_control in enumerate(settings['uv_controls']):
                                node = mat.node_tree.nodes.get('AGR source texture mapping '+str(tex_i))
                                if node:
                                    node.inputs['Location'].default_value[1] = (uv_control['offset_v']+age*settings['definition'].get('m_flTextureVScrollRate', 0)
                                                                                  /world_size*uv_control['scale_v'])
                                    node.inputs['Location'].keyframe_insert('default_value', frame=f)
                    _visibility(obj, born, end)
                    _linear(obj)
                    if ribbon:
                        _linear(mat.node_tree)
            report['layers'] += 1
            report['objects'] += sum(obj.type == 'MESH' for obj in created)
        except (UnsupportedEffect, ValueError, KeyError, OSError) as exc:
            for obj in created:
                bpy.data.objects.remove(obj, do_unlink=True)
            report['unsupported'].append({'instance': layer['instance']['id'], 'resource': layer['resource'], 'reason': str(exc)})
    report['materials'], report['textures'] = len(materials), len(images)
    if report['objects']:
        report['cycles_transparent_bounces'] = ensure_particle_transparency(scene)
    preparation = manifest.get('coverage', data.get('coverage', {}).get('scene_extras', {}).get('particles', {}))
    if not manifest['layers']:
        report.update(status='no_events', reason_code='no_prepared_layers',
                      reason='No prepared particle layers were supplied to Blender.')
    elif not report['objects']:
        report.update(status='unavailable', reason_code='no_renderable_particle_objects',
                      reason='Prepared particle layers produced no renderable objects. See rendering diagnostics.')
    report = merge_stage_coverage(preparation, report)
    scene.frame_set(original_frame)
    scene['agr_effects_report'] = json.dumps(report)
    readme = bpy.data.texts.new('AGR Effects Coverage')
    readme.write(json.dumps(report, indent=2)+'\n')
    return report
