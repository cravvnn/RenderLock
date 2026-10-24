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
                          'Missing map textures can cause EEVEE GPU texture errors and pink characters. Relink the retained textures before using EEVEE, or hide RenderLockMap while checking characters.',
                          'The rebatching bakes ambient map props to a static pose; authored fan, sign and similar animations are not replayed.',
                          'The map supplies no Source lighting data, recorded destruction or cloth collision.'])
    validate_map_completion(result, library, coordinates)
    scene['renderlock_external_map'] = json.dumps(result)
    return result
