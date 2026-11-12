"""Evaluated point-light frames under the normal package rollback owner."""
import math
from .particle_point_light import blender_parameters, require, SCOPE
from .observed_slide_light_states import BACKEND, RESOURCE, validate_package


def configure_falloff(light, parameters):
    """Adapt the observed quadratic-minus-offset curve without a radius guess.

    Light Falloff Constant supplies squared ray distance to compensate Cycles'
    built-in inverse-square term. Saved/rendered checks must qualify this graph
    separately; this is not proof of Source photometry or shading equivalence.
    """
    light.use_nodes = True
    nodes, links = light.node_tree.nodes, light.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputLight')
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (*parameters['color'], 1.)
    links.new(emission.outputs[0], output.inputs['Surface'])
    distance = nodes.new('ShaderNodeLightFalloff')
    distance.inputs['Strength'].default_value = 1.
    distance.inputs['Smooth'].default_value = 0.

    def operation(kind, a, b):
        node = nodes.new('ShaderNodeMath');node.operation = kind
        for i, value in enumerate((a, b)):
            if type(value) in (float, int): node.inputs[i].default_value = value
            else: links.new(value, node.inputs[i])
        return node.outputs[0]

    squared_source_distance = operation('DIVIDE', distance.outputs['Constant'], parameters['unit_scale']**2)
    clamped_squared_distance = operation('MAXIMUM', squared_source_distance, 1.)
    attenuation = operation('DIVIDE', 1., clamped_squared_distance)
    attenuation = operation('SUBTRACT', attenuation, parameters['falloff_offset'])
    attenuation = operation('MINIMUM', operation('MAXIMUM', attenuation, 0.), 1.)
    in_range = operation('LESS_THAN', squared_source_distance, (parameters['range']/parameters['unit_scale'])**2)
    multiplier = operation('MULTIPLY', operation('MULTIPLY', squared_source_distance, attenuation), in_range)
    links.new(multiplier, emission.inputs['Strength'])


def build_observed_slide_lights(package, data, assets, scene, camera):
    import bpy
    from .blender_effects import _linear
    validate_package(package, data)
    require(scene.render.engine in ('CYCLES', 'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'), 'unsupported light render engine')
    scale = data.get('metadata', {}).get('coordinate_system', {}).get('unit_scale')
    fps, rate = scene.render.fps/scene.render.fps_base, data['tick_rate']
    require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (scale, fps, rate)),
            'scene units/timing unavailable')
    rows = []
    for sample in package['reference']['capture']['frames']:
        frame = 1+(sample['tick']-data['start_tick'])*fps/rate
        for system in sample['systems']:
            for point in system['points']:
                rows.append((sample['tick'], frame, system['root_ordinal'], point['particle_id'],
                             blender_parameters(bytes.fromhex(point['descriptor_raw']), scale)))
    collection = bpy.data.collections.new('RenderLock | evaluated slide light frames')
    scene.collection.children.link(collection)
    for tick, frame, root, particle_id, parameters in rows:
        name = f'Native slide light | {root} | {tick} | {particle_id}'
        light = bpy.data.lights.new(name, 'POINT')
        light.energy = parameters['energy'];light.color = parameters['color']
        light.shadow_soft_size = 0.;light.use_shadow = parameters['casts_shadows']
        if scene.render.engine == 'CYCLES':
            light.color = (1., 1., 1.)
            light.use_custom_distance = False
            configure_falloff(light, parameters)
        else:
            light.use_nodes = False
            light.use_custom_distance = True;light.cutoff_distance = parameters['range']
        light['agr_native_light_scope'] = SCOPE
        light['agr_native_light_range'] = parameters['range']
        light['agr_native_light_falloff_offset'] = parameters['falloff_offset']
        obj = bpy.data.objects.new(name, light);collection.objects.link(obj)
        obj.location = parameters['location']
        obj['agr_observed_slide_light_tick'] = tick
        obj['agr_observed_slide_light_root_ordinal'] = root
        obj['agr_particle_resource'] = RESOURCE
        obj['agr_observed_collection_backend'] = BACKEND
        obj['agr_native_reference_sha256'] = package['reference']['capture_sha256']
        for at, hidden in ((frame-fps/rate, True), (frame, False), (frame+fps/rate, True)):
            obj.hide_render = hidden;obj.hide_viewport = hidden
            obj.keyframe_insert('hide_render', frame=at);obj.keyframe_insert('hide_viewport', frame=at)
        _linear(obj)
    return dict(backend=BACKEND, objects=len(rows), point_lights=len(rows), particle_tracks=0,
                rendered_collection_layers=int(bool(rows)), point_samples=len(rows),
                light_adaptation='cycles_falloff_graph' if scene.render.engine == 'CYCLES' else 'eevee_approximate_cutoff',
                observed_reference_sha256=package['reference']['capture_sha256'], limitations=[SCOPE])
