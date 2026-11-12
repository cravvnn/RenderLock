"""Bake qualified modifier-driven model components on evaluated hero poses."""
import math
from .modifier_mask_model import BACKEND, require, constant_track
from .particle_authored_model import MODEL


def build_modifier_mask(package, data, assets, scene, camera):
    require(not any(key in package for key in ('model_reference', 'lifecycle_reference',
        'observed_model_samples', 'lifecycle_model_samples')),
        'Measured mask references require separate current-build integration')
    constant_track(package['track'])
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector, Quaternion
    from .blender_particle_authored_model import _prototypes
    from .blender_particle_skinning import snapshot_rig_fingerprint
    from .blender_effects import _linear
    identity = package['entity_ids'][0]
    rigs = [o for o in scene.objects if o.type == 'ARMATURE' and o.get('agr_entity_id') == identity]
    require(len(rigs) == 1 and snapshot_rig_fingerprint(rigs[0]) == package['rig_fingerprint'],
            'selected evaluated mask rig changed')
    rig = rigs[0]; binding = package['attachment']; bone = binding['bone']
    require(rig.data.pose_position == 'POSE' and bone in rig.pose.bones, 'animated mask bone is unavailable')
    entity = next(e for e in data['entities'] if e['id'] == identity)
    lifecycle = package.get('lifecycle_model_samples')
    ticks = ([r['tick'] for r in lifecycle] if lifecycle is not None else
             [r['tick'] for r in package['track']['gate_samples']])
    observed = {s['tick']: s for s in (lifecycle if lifecycle is not None else package.get('observed_model_samples', []))}
    with np.load(entity['pose_file'], allow_pickle=False) as poses:
        available = set(poses['ticks'].tolist())-set(entity.get('pose_unavailable_ticks', []))
    pose_times = {t for t in ticks if not observed or observed[t]['model_present']}
    if lifecycle is not None:
        # A hidden terminal pose key bounds interpolation on its visible side.
        # It uses the preceding measured radius, never count-zero scalar defaults.
        pose_times.update(t for left, t in zip(ticks, ticks[1:]) if observed[left]['model_present'])
    required_ticks = {v for t in pose_times for v in (math.floor(t), math.ceil(t))}
    require(required_ticks <= available, 'mask pose samples are unavailable')
    scale = data.get('metadata', {}).get('coordinate_system', {}).get('unit_scale', .0254)
    require(type(scale) in (float, int) and math.isfinite(scale) and scale > 0, 'invalid mask scene units')
    fps = scene.render.fps/scene.render.fps_base; rate = data['tick_rate']
    require(type(rate) in (int, float) and math.isfinite(rate) and rate > 0, 'invalid mask tick rate')
    offset = Matrix.LocRotScale(Vector(binding['offset'])*scale,
        Quaternion((binding['rotation_xyzw'][3], *binding['rotation_xyzw'][:3])), Vector((1, 1, 1)))
    radius = package['model_scale']*1.5
    basis = Matrix.Rotation(math.pi/2, 4, 'Z')
    chain = []; cursor = rig
    while cursor is not None:
        require(cursor not in [r[0] for r in chain] and len(chain) < 64, 'mask rig ancestry cycle/bound')
        chain.append((cursor, cursor.hide_viewport, cursor.hide_get()))
        cursor = cursor.parent
    original_frame = (scene.frame_current, scene.frame_subframe)
    objects = []
    try:
        for obj, _, _ in chain:
            obj.hide_viewport = False; obj.hide_set(False)
        scene.frame_set(*original_frame[:1], subframe=original_frame[1])
        bpy.context.view_layer.update()
        prototypes = _prototypes(assets['models'][MODEL], {})
        collection = bpy.data.collections.new('RenderLock | modifier mask '+identity)
        scene.collection.children.link(collection)
        for prototype in prototypes:
            index = prototype['source_index']
            obj = bpy.data.objects.new(f'Modifier mask {identity} | mesh {index}', prototype['mesh'])
            collection.objects.link(obj); obj.rotation_mode = 'QUATERNION'; obj.visible_shadow = False
            obj['agr_modifier_mask_backend'] = BACKEND
            obj['agr_modifier_serial'] = package['track']['serial_number']
            obj['agr_mask_owner'] = identity; obj['agr_source_mesh_index'] = index
            obj['agr_mask_model_scale'] = package['model_scale']
            if lifecycle is not None:
                obj['agr_mask_lifecycle_reference'] = package['lifecycle_reference']['capture_sha256']
            objects.append((obj, prototype))
        preceding_radius = radius
        for tick in ticks:
            frame = 1+(tick-data['start_tick'])*fps/rate
            scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
            bpy.context.view_layer.update()
            present = not observed or observed[tick]['model_present']
            if tick in pose_times:
                sample_radius = observed[tick]['radius'] if observed and present else preceding_radius
                if present:
                    preceding_radius = sample_radius
                size = Matrix.Diagonal((sample_radius*scale/.0254,)*3+(1,))
                world = rig.matrix_world@rig.pose.bones[bone].matrix@offset
                linear = world.to_3x3(); gram = linear.transposed()@linear
                require(all(math.isfinite(x) for row in world for x in row)
                    and max(abs(gram[i][j]-(i == j)) for i in range(3) for j in range(3)) <= 2e-4
                    and abs(linear.determinant()-1) <= 2e-4, 'mask attachment is not a finite rigid pose')
            for obj, prototype in objects:
                if tick in pose_times:
                    obj.matrix_world = world@size@basis@prototype['source_matrix']
                    for prop in ('location', 'rotation_quaternion', 'scale'):
                        obj.keyframe_insert(prop, frame=frame)
                obj.hide_render = not present or prototype['source_index'] not in package['selection']['source_mesh_indices']
                obj.hide_viewport = obj.hide_render
                for prop in ('hide_render', 'hide_viewport'): obj.keyframe_insert(prop, frame=frame)
        for obj, _ in objects:
            # These are output-range crop keys, never inferred birth/death.
            for tick in (data['start_tick']-1, data['end_tick']+1):
                obj.hide_render = True; obj.hide_viewport = True
                for prop in ('hide_render', 'hide_viewport'):
                    obj.keyframe_insert(prop, frame=1+(tick-data['start_tick'])*fps/rate)
            _linear(obj)
    finally:
        for obj, hidden, local_hidden in reversed(chain):
            obj.hide_viewport = hidden; obj.hide_set(local_hidden)
        scene.frame_set(original_frame[0], subframe=original_frame[1])
        bpy.context.view_layer.update()
    return dict(backend=BACKEND, objects=len(objects), particle_tracks=1, point_lights=0,
        modifier_model_branches=1, model_pose_samples=len(pose_times), model_presence_keys=len(ticks),
        sampled_model_reference=package.get('model_reference', {}).get('capture_sha256'),
        lifecycle_model_reference=package.get('lifecycle_reference', {}).get('capture_sha256'),
        limitations=[(package['lifecycle_reference']['capture']['scope'] if lifecycle is not None else
            'Complete native sampled MODEL presence reference; between-tick presence is held, not engine subframe/pixel visibility.'
            if observed else 'Static sustained mask MODEL component only; stop/availability transitions are refused.'),
            'Exported evaluated hero pose is used without an ad-hoc client interpolation offset.',
            'Output-range crop is not birth/death. Source NPR, shaders, lighting and other root layers remain incomplete.'])
