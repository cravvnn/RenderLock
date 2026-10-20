"""Optional bounded hero-follow camera for offline scene review."""
import math


def _clear_ray(scene, depsgraph, origin, aim, actor):
    """Ignore render-hidden helper surfaces, without skipping visible scenery."""
    delta = aim - origin
    remaining = delta.length
    if remaining <= 1e-6:
        return True
    direction = delta.normalized()
    for _ in range(32):
        hit = scene.ray_cast(depsgraph, origin, direction, distance=remaining)
        if not hit[0]:
            return True
        obj = hit[4].original
        if not obj.hide_render and getattr(obj, 'visible_camera', True):
            return obj in actor
        advance = (hit[1] - origin).length + 1e-4
        remaining -= advance
        if remaining <= 0:
            return True
        origin = hit[1] + direction * 1e-4
    return False  # Dense helper geometry cannot silently imply a clear view.


def frame_actor(scene, root):
    import bpy
    from mathutils import Vector
    camera = scene.camera
    if camera is None or camera.constraints or camera.parent:
        raise ValueError('Hero shot requires an unparented camera without constraints')
    actor = {root, *root.children_recursive}
    meshes = [o for o in actor if o.type == 'MESH']
    if not meshes:
        raise ValueError('Hero shot has no visible character meshes')
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    if len(frames) > 4096:
        raise ValueError('Hero shot is limited to 4096 frames; choose a shorter scene')
    positions, points = {}, {}
    try:
        for frame in frames:
            scene.frame_set(frame)
            deps = bpy.context.evaluated_depsgraph_get()
            positions[frame] = root.matrix_world.translation.copy()
            points[frame] = [obj.matrix_world @ Vector(corner)
                             for mesh in meshes if not mesh.hide_render for obj in [mesh.evaluated_get(deps)]
                             for corner in obj.bound_box]
        relative = [point - positions[f] for f in frames for point in points[f]]
        if not relative:raise ValueError('Hero shot has no render-visible mesh frames')
        low = Vector([min(p[a] for p in relative) for a in range(3)])
        high = Vector([max(p[a] for p in relative) for a in range(3)])
        center = (low + high) / 2
        if not all(math.isfinite(x) for x in (*low, *high)):
            raise ValueError('Non-finite animated character bounds')
        # Fixed lens/aspect, conservative union across the entire animation.
        aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x /
                  (scene.render.resolution_y * scene.render.pixel_aspect_y))
        tan_x = 36 / (2 * 35)
        tan_y = tan_x / aspect
        sample_frames = sorted({frames[round(i * (len(frames) - 1) / 6)] for i in range(7)})
        preferred_view = camera.rotation_euler.to_quaternion() @ Vector((0, 0, 1))
        candidates = []
        for index in range(24):
            direction = Vector((math.cos(index * math.tau / 24), math.sin(index * math.tau / 24), .24)).normalized()
            rotation = (-direction).to_track_quat('-Z', 'Y')
            inverse = rotation.inverted()
            corners = [inverse @ (Vector((x, y, z)) - center)
                       for x in (low.x, high.x) for y in (low.y, high.y) for z in (low.z, high.z)]
            distance = max(1., max(p.z + max(abs(p.x) / tan_x, abs(p.y) / tan_y) / .82 for p in corners))
            offset = direction * distance
            scores = []
            for frame in sample_frames:
                scene.frame_set(frame)
                deps = bpy.context.evaluated_depsgraph_get()
                target = positions[frame] + center
                aims = [target, target + Vector((0, 0, (high.z-low.z) * .3)),
                        target - Vector((0, 0, (high.z-low.z) * .3))]
                clear = 0
                for aim in aims:
                    origin = target + offset
                    clear += int(_clear_ray(scene, deps, origin, aim, actor))
                scores.append(clear / len(aims))
            candidates.append((min(scores), sum(scores), direction.dot(preferred_view), -index, offset, rotation))
        chosen = max(candidates, key=lambda c: c[:4])
        offset, rotation = chosen[4:]
        camera.animation_data_clear()
        camera.data.type = 'PERSP'
        camera.data.sensor_fit = 'HORIZONTAL'
        camera.data.sensor_width = 36
        camera.data.lens = 35
        camera.data.shift_x = camera.data.shift_y = 0
        camera.data.clip_start = .01
        camera.data.clip_end = max(2000, offset.length * 10)
        for frame in frames:
            camera.location = positions[frame] + center + offset
            camera.rotation_euler = rotation.to_euler()
            camera.keyframe_insert('location', frame=frame)
            camera.keyframe_insert('rotation_euler', frame=frame)
        return {'mode': 'hero follow', 'hero_id': root.get('hero_id'),
                'entity_id': root.get('agr_entity_id'), 'frames': len(frames),
                'offset_m': list(offset), 'target_offset_m': list(center),
                'bounds_min_m': list(low), 'bounds_max_m': list(high),
                'render_aspect': aspect,
                'sample_frames': sample_frames, 'minimum_sampled_visibility': chosen[0],
                'scope': 'Animated render-visible mesh bounds fitted at integer frames; three visibility rays on seven frames. No whole-image occlusion or subframe guarantee.'}
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
