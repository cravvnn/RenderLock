"""Whole uploaded trail frames; the normal package transaction owns rollback."""
import math
from .observed_slide_trail_states import BACKEND, RESOURCE, SCOPE, validate_package, require
from .particle_trail_geometry import build_uploaded_quad
from .blender_slide_sprite_material import build_material


def build_captured_frame(system, camera_position):
    """Stateless expansion after strict reference admission; no root reuse."""
    vertices, faces, colors, uv = [], [], [], []
    for point in system['points']:
        q = build_uploaded_quad(point['controls_xyzw'], point['endpoint_rgba'], camera_position,
                                point['crop_flip_rgba'], gamma=True)
        base = len(vertices);vertices.extend(q['vertices']);colors.extend(q['colors'])
        uv.extend((u, 1-v) for u, v in q['source_card_uv'])
        faces.extend(tuple(base+i for i in face) for face in q['faces'])
    return dict(vertices=vertices, faces=faces, colors=colors, uv=uv)


def build_observed_slide_trails(package, data, assets, scene, camera):
    import bpy
    from .blender_effects import _linear
    validate_package(package, data)
    require(camera is not None and camera.type == 'CAMERA', 'evaluated camera required')
    scale = data.get('metadata', {}).get('coordinate_system', {}).get('unit_scale')
    rate = data['tick_rate'];fps = scene.render.fps/scene.render.fps_base
    require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (scale, rate, fps)), 'units/timing invalid')
    original = scene.frame_current, scene.frame_subframe
    geometry, objects = [], []
    try:
        # Precompute all camera-bound geometry before allocating anything.
        for sample in package['reference']['capture']['frames']:
            tick = sample['tick'];frame = 1+(tick-data['start_tick'])*fps/rate
            scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
            graph = bpy.context.evaluated_depsgraph_get();evaluated = camera.evaluated_get(graph)
            eye = [float(v/scale) for v in evaluated.matrix_world.translation]
            for system in sample['systems']:
                if system['points']:
                    geometry.append((tick, frame, system, build_captured_frame(system, eye)))
        collection = bpy.data.collections.new('RenderLock | native slide trail frames')
        scene.collection.children.link(collection)
        material = None
        for tick, frame, system, row in geometry:
            if material is None:
                material = build_material(assets['definitions'][RESOURCE]['m_Renderers'][0], assets, {}, 'native slide trails')
                material.use_backface_culling = False
                material['agr_trail_material_scope'] = 'Neutral scene-light material; Source lighting/fog/depth/pixel parity incomplete'
            mesh = bpy.data.meshes.new(f"Native slide trail | {system['root_ordinal']} | {tick}")
            mesh.from_pydata([[v*scale for v in p] for p in row['vertices']], [], row['faces'])
            mesh.materials.append(material);mesh['agr_native_trail_point_count'] = len(system['points'])
            for name, kind, values in (
                ('AGR Sprite Depth Ratio', 'FLOAT', [1.]*len(row['vertices'])),
                ('AGR Sprite Extra1', 'FLOAT', [1.]*len(row['vertices'])),
                ('AGR Sprite Depth RGB', 'FLOAT_VECTOR', [c[:3] for c in row['colors']]),
                ('AGR Sprite Depth Alpha', 'FLOAT', [c[3] for c in row['colors']]),
                ('AGR Sprite Source Position', 'FLOAT_VECTOR', [[v*scale for v in p] for p in row['vertices']])):
                attr = mesh.attributes.new(name=name, type=kind, domain='POINT')
                for item, value in zip(attr.data, values, strict=True):
                    if kind == 'FLOAT':item.value = value
                    else:item.vector = value
            layer = mesh.uv_layers.new(name='AGR Sprite UV 0')
            for loop in mesh.loops:layer.data[loop.index].uv = row['uv'][loop.vertex_index]
            obj = bpy.data.objects.new(mesh.name, mesh);collection.objects.link(obj);objects.append(obj)
            obj.visible_shadow = False
            obj['agr_observed_slide_trail_tick'] = tick
            obj['agr_observed_slide_trail_root_ordinal'] = system['root_ordinal']
            obj['agr_particle_resource'] = RESOURCE
            obj['agr_observed_collection_backend'] = BACKEND
            obj['agr_native_reference_sha256'] = package['reference']['capture_sha256']
            obj['agr_trail_camera_object'] = camera.name
            for at, hidden in ((frame-fps/rate, True), (frame, False), (frame+fps/rate, True)):
                obj.hide_render = hidden;obj.hide_viewport = hidden
                obj.keyframe_insert('hide_render', frame=at);obj.keyframe_insert('hide_viewport', frame=at)
            _linear(obj)
    finally:
        scene.frame_set(original[0], subframe=original[1]);bpy.context.view_layer.update()
    return dict(backend=BACKEND, objects=len(objects), point_lights=0, particle_tracks=0,
                rendered_collection_layers=int(bool(objects)), collection_frame_meshes=len(objects),
                point_samples=sum(len(system['points']) for _, _, system, _ in geometry),
                observed_reference_sha256=package['reference']['capture_sha256'], camera_object=camera.name,
                limitations=[SCOPE])
