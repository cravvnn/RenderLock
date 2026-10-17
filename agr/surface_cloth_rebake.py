"""Recompute only AGR-owned native surface caches after animation edits."""
from __future__ import annotations

import json
import math
import numpy as np


def rebake_surface_cloth_scene():
    import bpy
    from .blender_surface_cloth import INDEX_ATTRIBUTE, _validate_surface_cache

    scene = bpy.context.scene
    fps = scene.render.fps/scene.render.fps_base
    if not math.isfinite(fps) or not 1 <= fps <= 240:
        raise ValueError('Use a scene frame rate between 1 and 240 FPS for AGR cloth.')
    frames = list(range(scene.frame_start, scene.frame_end+1))
    if not frames:
        raise ValueError('The scene frame range is empty.')
    rigs = [obj for obj in scene.objects if obj.type == 'ARMATURE' and obj.get('agr_surface_cloth_collection')]
    previous_frame = scene.frame_current
    previous_active = bpy.context.view_layer.objects.active
    reports = {}
    try:
        for rig in rigs:
            collection = bpy.data.collections.get(rig['agr_surface_cloth_collection'])
            owned, visibility = [], []
            try:
                if collection is None or collection.get('agr_rig') != rig.name:
                    raise ValueError('The AGR cloth collection is missing or belongs to another rig.')
                carriers = [obj for obj in collection.all_objects if obj.get('agr_native_cloth_carrier')]
                if not carriers:
                    raise ValueError('No AGR native cloth carrier was found for this rig.')
                owned = [m for obj in collection.all_objects for m in obj.modifiers
                         if (obj in carriers and m.type == 'CLOTH'
                             and m.name.startswith('AGR native waist cloth'))
                         or (obj.get('agr_cloth_carrier') and m.type == 'NODES'
                             and m.name.startswith('AGR shared cloth backfaces'))]
                followers = []
                for obj in collection.all_objects:
                    if obj.get('agr_cloth_collision_proxy'):
                        visibility.append((obj, obj.hide_get()))
                        obj.hide_set(False)
                    if obj.get('agr_cloth_carrier'):
                        carrier = bpy.data.objects.get(obj['agr_cloth_carrier'])
                        attribute = obj.data.attributes.get(INDEX_ATTRIBUTE)
                        if carrier not in carriers or attribute is None:
                            raise ValueError('A shared backface is missing its carrier or vertex mapping.')
                        indices = np.empty(len(attribute.data), dtype=np.int32)
                        attribute.data.foreach_get('value', indices)
                        if (len(indices) != len(obj.data.vertices) or np.any(indices < 0)
                                or np.any(indices >= len(carrier.data.vertices))):
                            raise ValueError('A shared backface mapping no longer matches the cloth mesh.')
                        shared = [m for m in obj.modifiers if m.type == 'NODES'
                                  and m.name.startswith('AGR shared cloth backfaces')]
                        if len(shared) != 1 or shared[0].node_group is None:
                            raise ValueError('The shared backface modifier is missing or ambiguous.')
                        shared[0].show_viewport = shared[0].show_render = True
                        followers.append((obj, carrier, indices))
                for carrier in carriers:
                    modifiers = [m for m in carrier.modifiers if m.type == 'CLOTH'
                                 and m.name.startswith('AGR native waist cloth')]
                    if len(modifiers) != 1:
                        raise ValueError('Expected one AGR native cloth modifier on '+carrier.name)
                    modifier = modifiers[0]
                    modifier.show_viewport = modifier.show_render = True
                    cache = modifier.point_cache
                    bpy.context.view_layer.objects.active = carrier
                    with bpy.context.temp_override(object=carrier, active_object=carrier, point_cache=cache):
                        bpy.ops.ptcache.free_bake()
                        cache.frame_start, cache.frame_end = frames[0], frames[-1]
                        cache.use_disk_cache = False
                        scene.frame_set(frames[0])
                        bpy.ops.ptcache.bake(bake=True)
                    if not cache.is_baked:
                        raise ValueError('The AGR cloth cache did not finish baking.')
                validation = _validate_surface_cache(carriers, followers, frames)
                report = json.loads(collection.get('agr_surface_cloth_report', '{}'))
                report.update(status='baked_partial', scope='Seven waist cloth only',
                              rebaked=True, sample_count=len(frames), fps=fps,
                              cache_frames=[frames[0], frames[-1]], cache_embedded=True,
                              body_action_edited=False, **validation)
                collection['agr_surface_cloth_report'] = json.dumps(report, allow_nan=False)
            except Exception as exc:
                # Do not leave an unstable rebake visible. The existing armature
                # still provides ordinary recorded skinning below this modifier.
                for modifier in owned:
                    modifier.show_viewport = modifier.show_render = False
                report = {'status': 'failed', 'error': str(exc),
                          'fallback': 'AGR cloth disabled; recorded skinned waist motion retained',
                          'previous_cache_restored': False, 'body_action_edited': False}
                if getattr(exc, 'diagnostics', None):
                    report['diagnostics'] = exc.diagnostics
            finally:
                for obj, hidden in visibility:
                    obj.hide_set(hidden)
            rig['agr_cloth_report'] = json.dumps(report, allow_nan=False)
            reports[rig.name] = report
    finally:
        bpy.context.view_layer.objects.active = previous_active
        scene.frame_set(previous_frame)
    text = bpy.data.texts.get('AGR_CLOTH_REBAKE_REPORT') or bpy.data.texts.new('AGR_CLOTH_REBAKE_REPORT')
    text.clear()
    text.write(json.dumps(reports, indent=2, allow_nan=False))
    return reports
