"""Projected particle layers on a prepared map (review approximation).

The game projects these layers onto world surfaces inside a box around the particle. Here the layer is recorded as
a projector while effects are built (the map is not in the scene yet). After the map is composed, the map faces
inside the projector box are copied into one decal mesh; its material projects the layer's texture along the
particle normal and follows the particle's radius, colour and alpha.

Limits: static projectors only; the colour texture's alpha and the renderer colour are used, the game decal's
normal/roughness contribution is not; surfaces steeper than the slope fade are left out; judged by eye only.
"""
import json
import math
from pathlib import Path

PENDING = 'agr_projected_decal_pending'
OFFSET_METRES = 0.01


def _colour_scale(renderer):
    scale = renderer.get('m_vecColorScale', {})
    if isinstance(scale, dict) and scale.get('m_nType') == 'PVEC_TYPE_LITERAL_COLOR':
        return [(v / 255) ** 2.2 for v in scale.get('m_LiteralColor', [255, 255, 255])[:3]]
    if isinstance(scale, dict) and scale.get('m_nType') == 'PVEC_TYPE_LITERAL':
        return [float(v) for v in scale.get('m_vLiteralValue', [1, 1, 1])[:3]]
    return None


def build_projector(name, resource, renderer, assets, samples, simulation, collection, unit_scale, frame_step):
    """Record one projected particle as an empty. Returns a note when the layer cannot be drawn, else None."""
    import bpy
    rows = renderer.get('m_vecProjectedMaterials', [])
    material = rows[0].get('m_hMaterial') if len(rows) == 1 and isinstance(rows[0], dict) else None
    descriptor = assets.get('projected_materials', {}).get(material)
    if descriptor is None or descriptor.get('status') != 'supported':
        return 'projected layer not drawn; its decal material is not available (' + str(
            (descriptor or {}).get('reason', 'not decoded')) + ')'
    tint = _colour_scale(renderer)
    if tint is None:
        return 'projected layer not drawn; its colour scale is not a literal'
    positions = [tuple(p['position']) for _, _, p in samples]
    normals = [tuple(p['normal']) for _, _, p in samples]
    if max(max(abs(a - b) for a, b in zip(row, positions[0])) for row in positions) > 0.5 or len(set(normals)) != 1:
        return 'projected layer not drawn; moving projectors are not handled'
    if math.sqrt(sum(v * v for v in normals[0])) < 1e-6:
        return 'projected layer not drawn; the particle has no normal'
    keys = []
    for tick, _, p in samples:
        frame = 1 + (tick - simulation['start_tick']) * frame_step
        keys.append([frame, p['radius'] * unit_scale, max(0.0, p['alpha'] * p['alpha_alternate']),
                     *[c * t for c, t in zip(p['color'], tint)]])
    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    obj.empty_display_type = 'CIRCLE'; obj.empty_display_size = 0.25; obj.hide_render = True
    obj.location = [v * unit_scale for v in positions[0]]
    obj[PENDING] = True
    obj['agr_particle_resource'] = resource
    obj['agr_projected_spec'] = json.dumps(dict(material=material, directory=descriptor['directory'],
        image=descriptor['image'], sha256=descriptor.get('sha256'), opacity=descriptor.get('opacity', 1.0),
        normal=list(normals[0]), yaw=float(samples[0][2]['rotation'][2]), frame_step=frame_step,
        depth=[float(renderer.get('m_flMinProjectionDepth', -64.0)) * unit_scale,
               float(renderer.get('m_flMaxProjectionDepth', 64.0)) * unit_scale], keys=keys))
    return None


def _material(name, image, axis_u, axis_v, normal, depth, opacity):
    import bpy
    mat = bpy.data.materials.new(name); mat.use_nodes = True
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
    nodes = mat.node_tree.nodes; links = mat.node_tree.links; nodes.clear()
    geometry = nodes.new('ShaderNodeNewGeometry'); info = nodes.new('ShaderNodeObjectInfo')
    offset = nodes.new('ShaderNodeVectorMath'); offset.operation = 'SUBTRACT'
    links.new(geometry.outputs['Position'], offset.inputs[0]); links.new(info.outputs['Location'], offset.inputs[1])

    def dot(source, axis):
        node = nodes.new('ShaderNodeVectorMath'); node.operation = 'DOT_PRODUCT'
        links.new(source, node.inputs[0]); node.inputs[1].default_value = tuple(axis)
        return node.outputs['Value']

    def math_node(operation, a, b):
        node = nodes.new('ShaderNodeMath'); node.operation = operation
        for socket, value in zip(node.inputs, (a, b)):
            if isinstance(value, (int, float)):
                socket.default_value = value
            else:
                links.new(value, socket)
        return node.outputs['Value']

    def attribute(prop):
        node = nodes.new('ShaderNodeAttribute'); node.attribute_type = 'OBJECT'; node.attribute_name = prop
        return node.outputs['Fac']

    diameter = math_node('MULTIPLY', attribute('agr_proj_radius'), 2.0)
    u = math_node('ADD', math_node('DIVIDE', dot(offset.outputs['Vector'], axis_u), diameter), 0.5)
    v = math_node('ADD', math_node('DIVIDE', dot(offset.outputs['Vector'], axis_v), diameter), 0.5)
    uv = nodes.new('ShaderNodeCombineXYZ'); links.new(u, uv.inputs['X']); links.new(v, uv.inputs['Y'])
    texture = nodes.new('ShaderNodeTexImage'); texture.image = image; texture.extension = 'CLIP'
    links.new(uv.outputs['Vector'], texture.inputs['Vector'])
    along = dot(offset.outputs['Vector'], normal)
    inside = math_node('MULTIPLY', math_node('GREATER_THAN', along, depth[0]), math_node('LESS_THAN', along, depth[1]))
    slope = nodes.new('ShaderNodeMapRange'); slope.clamp = True; slope.name = 'AGR decal slope fade'
    links.new(dot(geometry.outputs['True Normal'], normal), slope.inputs['Value'])
    slope.inputs['From Min'].default_value = 0.2; slope.inputs['From Max'].default_value = 0.6
    alpha = math_node('MULTIPLY', math_node('MULTIPLY', texture.outputs['Alpha'], attribute('agr_proj_alpha')),
                      math_node('MULTIPLY', math_node('MULTIPLY', inside, slope.outputs['Result']), opacity))
    colour = nodes.new('ShaderNodeVectorMath'); colour.operation = 'MULTIPLY'
    links.new(texture.outputs['Color'], colour.inputs[0]); links.new(info.outputs['Color'], colour.inputs[1])
    diffuse = nodes.new('ShaderNodeBsdfDiffuse'); links.new(colour.outputs['Vector'], diffuse.inputs['Color'])
    clear = nodes.new('ShaderNodeBsdfTransparent'); mix = nodes.new('ShaderNodeMixShader')
    links.new(alpha, mix.inputs['Fac']); links.new(clear.outputs['BSDF'], mix.inputs[1]); links.new(diffuse.outputs['BSDF'], mix.inputs[2])
    output = nodes.new('ShaderNodeOutputMaterial'); links.new(mix.outputs['Shader'], output.inputs['Surface'])
    return mat


def _faces_in_box(obj, centre, axes, half, depth):
    """World-space corner positions of this mesh object's faces that overlap the projector box."""
    import numpy as np
    mesh = obj.data
    if not len(mesh.polygons):
        return None
    world = np.asarray(obj.matrix_world, dtype=np.float64)
    points = np.empty(len(mesh.vertices) * 3, dtype=np.float32); mesh.vertices.foreach_get('co', points)
    points = points.reshape(-1, 3).astype(np.float64) @ world[:3, :3].T + world[:3, 3]
    loops = np.empty(len(mesh.loops), dtype=np.int32); mesh.loops.foreach_get('vertex_index', loops)
    starts = np.empty(len(mesh.polygons), dtype=np.int32); mesh.polygons.foreach_get('loop_start', starts)
    totals = np.empty(len(mesh.polygons), dtype=np.int32); mesh.polygons.foreach_get('loop_total', totals)
    if not np.array_equal(starts, np.concatenate(([0], np.cumsum(totals)[:-1]))):
        return None
    local = (points - centre) @ axes.T                     # columns: u, v, along the normal
    corner = local[loops]
    low = np.minimum.reduceat(corner, starts, axis=0); high = np.maximum.reduceat(corner, starts, axis=0)
    box_low = np.array([-half, -half, depth[0]]); box_high = np.array([half, half, depth[1]])
    keep = np.all(high >= box_low, axis=1) & np.all(low <= box_high, axis=1)
    if not keep.any():
        return None
    # Leave out faces that point away from the projector.
    first = points[loops[starts]]; second = points[loops[starts + 1]]; third = points[loops[starts + 2]]
    facing = np.cross(second - first, third - first) @ axes[2]
    lengths = np.linalg.norm(np.cross(second - first, third - first), axis=1)
    keep &= facing > 0.15 * np.maximum(lengths, 1e-20)
    faces = []
    for index in np.nonzero(keep)[0]:
        faces.append(points[loops[starts[index]:starts[index] + totals[index]]])
    return faces


def build_pending_decals(scene, map_collection):
    """After map composition: turn every recorded projector into a decal mesh cut from the map surfaces."""
    import bpy
    import numpy as np
    from mathutils import Vector, Quaternion
    from .blender_images import load_packed_image
    report = dict(policy='agr_projected_world_map_decal_review_v1', projectors=0, decals=0, faces=0, without_surface=0)
    pending = [obj for obj in scene.objects if obj.get(PENDING)]
    if not pending:
        return report
    meshes = []
    for obj in map_collection.all_objects:
        if obj.type != 'MESH' or obj.hide_render:
            continue
        box = np.asarray([obj.matrix_world @ Vector(corner) for corner in obj.bound_box], dtype=np.float64)
        meshes.append((obj, box.min(axis=0), box.max(axis=0)))
    for empty in pending:
        report['projectors'] += 1
        spec = json.loads(empty['agr_projected_spec'])
        centre = np.asarray(empty.matrix_world.translation, dtype=np.float64)
        normal = (empty.matrix_world.to_3x3() @ Vector(spec['normal'])).normalized()
        tangent = Vector((0, 0, 1)).cross(normal) if abs(normal.z) < 0.999 else Vector((1, 0, 0))
        axis_u = Quaternion(normal, spec['yaw']) @ tangent.normalized(); axis_v = normal.cross(axis_u)
        axes = np.asarray([axis_u, axis_v, normal], dtype=np.float64)
        half = max(key[1] for key in spec['keys']); depth = spec['depth']
        reach = math.sqrt(2) * half + max(abs(depth[0]), abs(depth[1]))
        faces = []
        for obj, low, high in meshes:
            if np.any(low > centre + reach) or np.any(high < centre - reach):
                continue
            found = _faces_in_box(obj, centre, axes, half, depth)
            if found:
                faces += found
        del empty[PENDING]
        if not faces:
            report['without_surface'] += 1
            continue
        vertices = []; polygons = []
        lift = np.asarray(normal, dtype=np.float64) * OFFSET_METRES
        for face in faces:
            start = len(vertices)
            vertices += [tuple(row) for row in (face - centre + lift)]
            polygons.append(tuple(range(start, start + len(face))))
        name = empty.name.replace('AGR FX projector', 'AGR FX decal')
        mesh = bpy.data.meshes.new(name); mesh.from_pydata(vertices, [], polygons); mesh.update()
        image = load_packed_image(str(Path(spec['directory']) / spec['image']), check_existing=False,
                                  expected_sha256=spec.get('sha256'))
        mesh.materials.append(_material('AGR projected | ' + Path(spec['material']).stem, image, axis_u, axis_v,
                                        normal, depth, spec.get('opacity', 1.0)))
        decal = bpy.data.objects.new(name, mesh)
        for collection in empty.users_collection:
            collection.objects.link(decal)
        decal.location = Vector(centre); decal.visible_shadow = False
        decal['agr_particle_resource'] = empty['agr_particle_resource']
        decal['agr_projected_decal'] = True
        keys = spec['keys']; step = spec['frame_step']
        keys = [[keys[0][0] - step, keys[0][1], 0.0, *keys[0][3:]]] + keys + [[keys[-1][0] + step, keys[-1][1], 0.0, *keys[-1][3:]]]
        for frame, radius, alpha, red, green, blue in keys:
            decal['agr_proj_radius'] = float(max(radius, 1e-6)); decal['agr_proj_alpha'] = float(alpha)
            decal.color = (red, green, blue, 1.0)
            decal.keyframe_insert('["agr_proj_radius"]', frame=frame); decal.keyframe_insert('["agr_proj_alpha"]', frame=frame)
            decal.keyframe_insert('color', frame=frame)
        report['decals'] += 1; report['faces'] += len(faces)
    return report
