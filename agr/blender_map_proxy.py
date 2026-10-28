"""Create an optional, render-hidden approximation of the downloaded map.

The original map remains intact for rendering. This deliberately supports the
qualified static Midtown snapshot only; it is not a general mesh optimizer.
"""
from __future__ import annotations

from collections import defaultdict
import json
import re
import time

GRID_METRES = 0.25
PROXY_COLLECTION = 'RenderLockProxy'
PROXY_ALGORITHM = 'grid-0.25m-v1'
_CELL = re.compile(r'(cell_[^_]+_[^_]+)__')
_MAX_GROUP_VERTICES = 4_000_000
_MAX_GROUP_FACES = 3_000_000


def build_map_proxy(scene, map_result):
    """Build a viewport-only proxy after the full map passes its own checks."""
    try:
        return _build_map_proxy(scene, map_result)
    except Exception as exc:
        if str(exc).startswith('Viewport proxy: '):
            raise
        raise ValueError(f'Viewport proxy: could not create the simplified map ({type(exc).__name__}: {exc}).') from exc


def _build_map_proxy(scene, map_result):
    import bpy
    import numpy as np
    from .external_map import MAP_BLEND_SHA256, MAP_COLLECTION, MAP_MESHES, MAP_VERTICES

    if map_result.get('library_sha256') != MAP_BLEND_SHA256:
        raise ValueError('Viewport proxy: only the current downloaded Midtown map supports this option.')
    if bpy.data.collections.get(PROXY_COLLECTION) is not None:
        raise ValueError('Viewport proxy: the reserved proxy collection already exists.')
    source = scene.collection.children.get(MAP_COLLECTION)
    if source is None or source.hide_render or source.hide_viewport:
        raise ValueError('Viewport proxy: the full map is missing or already hidden.')
    # The map alignment was just parented during append. Force Blender to
    # evaluate those transforms before baking world positions into the proxy.
    bpy.context.view_layer.update()
    for collection in source.children_recursive:
        if collection.hide_render or collection.hide_viewport:
            raise ValueError('Viewport proxy: the map has unsupported collection visibility settings.')

    objects = list(source.all_objects)
    if any(obj.type not in {'MESH', 'EMPTY'} or obj.animation_data or obj.instance_type != 'NONE'
           for obj in objects):
        raise ValueError('Viewport proxy: the map contains animated or unsupported objects.')
    groups = defaultdict(list)
    for obj in objects:
        if obj.type != 'MESH':
            continue
        if obj.hide_render or obj.hide_viewport or obj.modifiers or obj.data.shape_keys:
            raise ValueError('Viewport proxy: the map has unsupported mesh settings.')
        match = _CELL.match(obj.name)
        if match is None:
            raise ValueError('Viewport proxy: the map has an unsupported mesh name.')
        groups[match.group(1)].append(obj)
    if (sum(len(group) for group in groups.values()) != MAP_MESHES
            or sum(len(obj.data.vertices) for group in groups.values() for obj in group) != MAP_VERTICES):
        raise ValueError('Viewport proxy: the map geometry inventory differs from the qualified download.')

    proxy = bpy.data.collections.new(PROXY_COLLECTION)
    # Both collection and object render gates survive saves and independently
    # keep the approximation out of normal renders, shadows and reflections.
    proxy.hide_render = True
    material = bpy.data.materials.new('RenderLock | viewport map')
    material.diffuse_color = (0.38, 0.38, 0.38, 1.0)
    material.use_nodes = True
    shader = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    shader.inputs['Base Color'].default_value = material.diffuse_color
    source_faces = proxy_faces = proxy_vertices = 0
    started = time.monotonic()

    for cell, cell_objects in sorted(groups.items()):
        vertex_count = sum(len(obj.data.vertices) for obj in cell_objects)
        face_count = sum(len(obj.data.polygons) for obj in cell_objects)
        if not 0 < vertex_count <= _MAX_GROUP_VERTICES or not 0 < face_count <= _MAX_GROUP_FACES:
            raise ValueError(f'Viewport proxy: {cell} exceeds the supported geometry bounds.')
        coordinates, indices = [], []
        vertex_offset = 0
        for obj in cell_objects:
            mesh = obj.data
            if len(mesh.loops) != 3 * len(mesh.polygons):
                raise ValueError(f'Viewport proxy: {obj.name} contains unsupported non-triangle faces.')
            totals = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get('loop_total', totals)
            if not np.all(totals == 3):
                raise ValueError(f'Viewport proxy: {obj.name} contains unsupported non-triangle faces.')
            coords = np.empty(3 * len(mesh.vertices), dtype=np.float32)
            mesh.vertices.foreach_get('co', coords)
            transform = np.asarray(obj.matrix_world, dtype=np.float64)
            if not np.isfinite(transform).all():
                raise ValueError(f'Viewport proxy: {obj.name} has an invalid transform.')
            coords = coords.reshape(-1, 3) @ transform[:3, :3].T + transform[:3, 3]
            if not np.isfinite(coords).all() or np.any(np.abs(coords) > 100_000):
                raise ValueError(f'Viewport proxy: {obj.name} has unsupported coordinates.')
            coordinates.append(coords.astype(np.float32))
            vertices = np.empty(len(mesh.loops), dtype=np.int32)
            mesh.loops.foreach_get('vertex_index', vertices)
            if vertices.size and (vertices.min() < 0 or vertices.max() >= len(mesh.vertices)):
                raise ValueError(f'Viewport proxy: {obj.name} has invalid vertex indices.')
            indices.append(vertices + vertex_offset)
            vertex_offset += len(mesh.vertices)

        points = np.concatenate(coordinates)
        triangles = np.concatenate(indices).reshape(-1, 3)
        keys = np.rint(points / GRID_METRES).astype(np.int32)
        grid_points, remap = np.unique(keys, axis=0, return_inverse=True)
        simplified = remap[triangles]
        keep = ((simplified[:, 0] != simplified[:, 1]) & (simplified[:, 1] != simplified[:, 2])
                & (simplified[:, 2] != simplified[:, 0]))
        simplified = simplified[keep]
        if len(simplified) == 0:
            raise ValueError(f'Viewport proxy: simplification removed the whole {cell} cell.')
        used, inverse = np.unique(simplified, return_inverse=True)
        grid_points = grid_points[used].astype(np.float32) * GRID_METRES
        simplified = inverse.reshape(-1, 3).astype(np.int32)

        mesh = bpy.data.meshes.new('RenderLock proxy | ' + cell)
        mesh.vertices.add(len(grid_points))
        mesh.vertices.foreach_set('co', grid_points.reshape(-1))
        mesh.loops.add(3 * len(simplified))
        mesh.loops.foreach_set('vertex_index', simplified.reshape(-1))
        mesh.polygons.add(len(simplified))
        mesh.polygons.foreach_set('loop_start', np.arange(len(simplified), dtype=np.int32) * 3)
        mesh.polygons.foreach_set('loop_total', np.full(len(simplified), 3, dtype=np.int32))
        mesh.materials.append(material)
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new('RenderLock proxy | ' + cell, mesh)
        obj.hide_render = True
        proxy.objects.link(obj)
        source_faces += face_count
        proxy_faces += len(simplified)
        proxy_vertices += len(grid_points)
        print(f'RenderLock map proxy: {cell} ready', flush=True)
        del coordinates, indices, points, triangles, keys, grid_points, remap, simplified, keep, used, inverse

    scene.collection.children.link(proxy)
    # The Linux test branch creates this separate characters-only view layer.
    # Match by collection identity: Blender can suffix the layer-node name.
    for layer in scene.view_layers:
        if layer.name == 'RenderLockChar':
            node = next((child for child in layer.layer_collection.children if child.collection is proxy), None)
            if node is None:
                raise ValueError('Viewport proxy: could not isolate the characters-only view layer.')
            node.exclude = True
    source.hide_viewport = True
    proxy['renderlock_proxy_note'] = 'Viewport approximation only. The full map supplies the render.'
    result = dict(status='ready', collection=PROXY_COLLECTION, algorithm=PROXY_ALGORITHM,
                  source_map_sha256=map_result['library_sha256'], source_meshes=MAP_MESHES,
                  source_vertices=MAP_VERTICES, source_faces=source_faces,
                  proxy_meshes=len(proxy.objects), proxy_vertices=proxy_vertices, proxy_faces=proxy_faces,
                  full_map_hidden_in_viewport=source.hide_viewport and not source.hide_render,
                  proxy_hidden_in_render=proxy.hide_render and all(obj.hide_render for obj in proxy.objects),
                  seconds=round(time.monotonic() - started, 3))
    from .external_map import validate_map_proxy_completion
    validate_map_proxy_completion(result, map_result)
    scene['renderlock_map_proxy'] = json.dumps(result)
    return result
