"""Append the qualified map without relinking textures or changing replay poses."""
from __future__ import annotations

import json
import time


def remove_unused_library_records():
    """Drop append bookkeeping only when no datablock is actually linked."""
    import bpy
    live = {block.library for block in bpy.data.user_map() if block.library is not None}
    for library in list(bpy.data.libraries):
        if library not in live:
            bpy.data.libraries.remove(library)


# Six thousand appended map meshes overflow EEVEE's default 512 MiB shadow pool
# and silently drop shadows ("Shadow buffer full"). Widen it only when the map is
# part of the scene, and only from the lower default so an explicit setting wins.
MAP_SHADOW_POOL_SIZE = '2048'
_SHADOW_POOL_LADDER = ('16', '32', '64', '128', '256', '512', '1024', '1536', '2048')


def raise_shadow_pool(scene):
    """Widen EEVEE's shadow pool for a map scene; never lower a larger value."""
    eevee = getattr(scene, 'eevee', None)
    if eevee is None or not hasattr(eevee, 'shadow_pool_size'):
        return None
    try:
        rung = _SHADOW_POOL_LADDER.index(str(eevee.shadow_pool_size))
    except ValueError:
        return None
    target = _SHADOW_POOL_LADDER.index(MAP_SHADOW_POOL_SIZE)
    previous = str(eevee.shadow_pool_size)
    if rung < target:
        eevee.shadow_pool_size = MAP_SHADOW_POOL_SIZE
    return {'previous': previous, 'value': str(eevee.shadow_pool_size),
            'raised': rung < target}


def isolate_character_view_layer(scene):
    """Exclude the map from the character layer so characters-only views stay cheap.

    The append adds ``RenderLockMap`` directly to the scene root after the
    character master layer node was restored. A second view layer that excludes
    it is the documented characters-only toggle; the render layer keeps
    everything. Nothing is deleted and no render visibility flag changes.
    """
    import bpy
    from .external_map import MAP_COLLECTION
    layer = next((vl for vl in scene.view_layers if vl.name == 'RenderLockChar'), None)
    if layer is None:
        layer = scene.view_layers.new('RenderLockChar')
    collection = bpy.data.collections.get(MAP_COLLECTION)
    if collection is None:
        return {'status': 'not_excluded', 'reason': 'no map collection'}
    # Match by collection, not by node name: a name collision with the map's own
    # root collection makes Blender store the node under a suffixed name.
    node = next((child for child in layer.layer_collection.children
                 if child.collection is collection), None)
    if node is None:
        return {'status': 'not_excluded', 'reason': 'map is not a root layer child'}
    node.exclude = True
    return {'status': 'excluded', 'view_layer': layer.name, 'collection': MAP_COLLECTION,
            'layer_node': node.name,
            'toggle': 'View Layer collection exclude (Characters only)'}


def hide_reference_grid(scene):
    """Hide the 88 m review grid once the real map supplies the ground reference."""
    import bpy
    hidden = [obj.name for obj in bpy.data.objects
              if obj.name.startswith('Reference grid (not map geometry)') and not obj.hide_render]
    for obj in list(bpy.data.objects):
        if obj.name.startswith('Reference grid (not map geometry)'):
            obj.hide_render = True
            obj.hide_viewport = True
    return {'hidden': hidden, 'status': 'hidden' if hidden else 'absent'}


def neutralize_map_scene(scene, camera_root, generated_shot=None):
    """Apply the reviewed map-scene settings after a successful append.

    ``generated_shot`` is the ``frame_actor`` report of a generated hero-follow
    camera, if any. Returns a report dict; no recorded animation, camera keys or
    geometry change.
    """
    import bpy
    from .external_map import MAP_COLLECTION
    report = {'shadow_pool': raise_shadow_pool(scene),
              'reference_grid': hide_reference_grid(scene),
              'character_view_layer': isolate_character_view_layer(scene)}
    if camera_root is not None:
        camera = scene.camera
        if camera is None:
            report['camera_visibility'] = {'status': 'not_checked', 'reason': 'Scene has no active camera'}
        elif (generated_shot or {}).get('mode') == 'hero follow' and generated_shot.get('sample_frames'):
            # The generated follow camera is already sampled by its caller.
            report['camera_visibility'] = {'status': 'not_checked', 'reason': 'generated hero-follow camera'}
        else:
            try:
                if camera.type != 'CAMERA':
                    raise ValueError('Active object is not a camera')
                report['camera_visibility'] = assess_active_camera(scene, camera, camera_root)
            except Exception as exc:
                report['camera_visibility'] = {'status': 'not_checked',
                                               'reason': f'{type(exc).__name__}: {exc}'}
    return report


def assess_active_camera(scene, camera, root, samples=7):
    """Sample an artist/overview camera against the appended map for obstructions.

    The hero-follow path samples its own keys. Any other active camera that the
    user or an earlier export restored otherwise gets no check at all, so a shot
    framed before the map existed can sit inside a wall unnoticed.
    """
    import bpy
    from .blender_shot import _clear_ray
    start, end = int(scene.frame_start), int(scene.frame_end)
    if end < start:
        return {'status': 'not_checked', 'reason': 'Empty frame range'}
    step = max(1, (end - start) // max(1, samples - 1))
    frames = sorted({*range(start, end + 1, step), end})[:samples]
    actor = {root, *root.children_recursive}
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    rows = []
    try:
        for frame in frames:
            scene.frame_set(frame)
            deps = bpy.context.evaluated_depsgraph_get()
            origin = camera.evaluated_get(deps).matrix_world.translation.copy()
            aim = root.evaluated_get(deps).matrix_world.translation.copy()
            visible, blocker, distance = _clear_ray(scene, deps, origin, aim, actor)
            rows.append({'frame': frame, 'clear_rays': int(visible), 'rays': 1,
                         'hit_object': None if visible else blocker,
                         'hit_distance_m': None if visible else distance})
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
    first = next((row for row in rows if not row['clear_rays']), None)
    return {'status': 'obstructed' if first else 'sampled_clear',
            'minimum_sampled_visibility': min(row['clear_rays'] for row in rows),
            'first_obstructed_frame': first['frame'] if first else None,
            'hit_object': first['hit_object'] if first else None,
            'hit_distance_m': first['hit_distance_m'] if first else None,
            'camera': camera.name, 'samples': rows,
            'scope': 'One ray to the character root at evenly spaced frames of the scene range '
                     'for a non-generated active camera, after map append. Not every frame.'}


def group_renderlock_content(scene):
    import bpy
    from .external_map import CHAR_COLLECTION
    if bpy.data.collections.get(CHAR_COLLECTION) is not None:
        raise ValueError('External map: reserved character collection already exists.')
    flags = {}
    attributes = ('exclude', 'hide_viewport', 'holdout', 'indirect_only')

    def remember(node, path, output):
        for child in node.children:
            key = (*path, child.collection.name)
            output[key] = {a: getattr(child, a) for a in attributes}
            remember(child, key, output)

    for layer in scene.view_layers:
        flags[layer.name] = {}
        remember(layer.layer_collection, (), flags[layer.name])
    master = bpy.data.collections.new(CHAR_COLLECTION)
    scene.collection.children.link(master)
    for child in list(scene.collection.children):
        if child is not master:
            master.children.link(child)
            scene.collection.children.unlink(child)
    for obj in list(scene.collection.objects):
        master.objects.link(obj)
        scene.collection.objects.unlink(obj)

    def restore(node, path, saved):
        for child in node.children:
            key = (*path, child.collection.name)
            for attribute, value in saved.get(key, {}).items():
                setattr(child, attribute, value)
            restore(child, key, saved)

    for layer in scene.view_layers:
        restore(layer.layer_collection.children[master.name], (), flags[layer.name])
    return master


def append_external_map(scene, path, coordinates):
    try:
        return _append_external_map(scene, path, coordinates)
    except Exception as exc:
        if str(exc).startswith('External map: '):
            raise
        raise ValueError(f'External map: append failed ({type(exc).__name__}): {exc}') from exc


def _append_external_map(scene, path, coordinates):
    import bpy
    from mathutils import Matrix
    from .external_map import (validate_map_library, validate_map_completion, map_coordinate_matrix,
        map_definition, MAP_COLLECTION, CHAR_COLLECTION)
    library = validate_map_library(path)
    definition = map_definition(library)
    if any(block.library is not None for block in bpy.data.user_map()):
        raise ValueError('External map: generated character data must be local before adding the map; existing linked data was left unchanged.')
    if bpy.data.collections.get(MAP_COLLECTION) is not None:
        raise ValueError('External map: reserved map collection already exists.')
    if scene.collection.children.get(CHAR_COLLECTION) is None:
        raise ValueError('External map: generated scene has no character master collection.')
    started = time.monotonic()
    # Load as one graph and localize the batch. Final scene data must be local;
    # no live library dependencies may remain in the completed append.
    print('AGR map: loading downloaded map library...', flush=True)
    with bpy.data.libraries.load(library['path'], link=True) as (available, loaded):
        if MAP_COLLECTION not in available.collections:
            raise ValueError('External map: the library has no RenderLockMap collection.')
        loaded.collections = [MAP_COLLECTION]
    collection = loaded.collections[0]
    if collection is None or collection.name != MAP_COLLECTION:
        raise ValueError('External map: the map collection was not appended locally.')
    scene.collection.children.link(collection)
    loaded_at = time.monotonic()
    print(f'AGR map: library loaded in {loaded_at-started:.1f}s', flush=True)
    print('AGR map: localizing map data; this can take several minutes. '
          'Blender is still working; no texture relinking is performed here.', flush=True)
    bpy.ops.object.make_local(type='ALL')
    collection = scene.collection.children.get(MAP_COLLECTION)
    if collection is None or collection.library is not None:
        raise ValueError('External map: the map collection could not be made local.')
    localized_at = time.monotonic()
    print(f'AGR map: graph localized in {localized_at-loaded_at:.1f}s '
          f'({localized_at-started:.1f}s total)', flush=True)
    profile = json.loads(collection.get('renderlock_map_profile', '{}'))
    if (profile.get('package_id') != definition['package_id'] or profile.get('source_frame') != 'vrf_blender_metres'
            or tuple(collection.instance_offset) != (0, 0, 0)):
        raise ValueError('External map: library coordinate profile differs from the supported map.')
    objects = list(collection.all_objects)
    members = set(objects)
    meshes = [o for o in objects if o.type == 'MESH']
    if (len(objects) != definition['objects'] or len(meshes) != definition['meshes']
            or sum(len(o.data.vertices) for o in meshes) != definition['vertices']
            or any(o.parent is not None and o.parent not in members for o in objects)):
        raise ValueError('External map: library object hierarchy or geometry inventory is incomplete.')
    basis = bpy.data.objects.new('RenderLock | map alignment', None)
    collection.objects.link(basis)
    basis.matrix_world = Matrix(map_coordinate_matrix(coordinates))
    for obj in objects:
        if obj.parent is None:
            authored = obj.matrix_world.copy()
            obj.parent = basis
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = authored
    # Blender reports one bookkeeping user even for a fully local append.
    # Actual ID.library references decide whether a Library record is unused.
    remove_unused_library_records()
    result = dict(status='appended', package_id=definition['package_id'],
                  library_sha256=library['sha256'], collection=collection.name,
                  character_collection=CHAR_COLLECTION, imported_objects=len(objects),
                  mesh_objects=len(meshes), vertices=sum(len(o.data.vertices) for o in meshes),
                  coordinate_matrix=[list(row) for row in basis.matrix_world],
                  texture_relink='manual', map_cutting='manual', linked_libraries=len(bpy.data.libraries),
                  timings_seconds={'library_load': loaded_at-started,
                                   'localize': localized_at-loaded_at},
                  limits=['Map snapshot revision was not supplied; geometry can differ from a replay or later game update.',
                          'Cut the map in Solid view with the separate Map Optimizer before relinking retained textures with File > External Data > Find Missing Files. Whole-map relinking can use substantial RAM.',
                          'Missing map textures can cause EEVEE GPU texture errors and pink map props; packed character textures are unaffected. Relink the retained textures before using EEVEE, or hide RenderLockMap while checking map props.',
                          'The rebatching bakes ambient map props to a static pose; authored fan, sign and similar animations are not replayed.',
                          'The map supplies no Source lighting data, recorded destruction or cloth collision.'])
    validate_map_completion(result, library, coordinates)
    scene['renderlock_external_map'] = json.dumps(result)
    return result
