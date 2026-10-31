"""Build the cached viewport geometry of a qualified prepared map once."""
from __future__ import annotations

from collections import defaultdict
import re

import numpy as np

ALGORITHM = 'grid-0.25m-v2'
GRID_METRES = .25
COLLECTION = 'RenderLockProxy'
_CELL = re.compile(r'^(cell_[^_]+_[^_]+)__')


def simplify(points, triangles):
    points = np.asarray(points)
    triangles = np.asarray(triangles)
    if (points.ndim != 2 or points.shape[1] != 3 or len(points) > 4_000_000
            or not np.isfinite(points).all() or np.any(np.abs(points) > 100_000)
            or triangles.ndim != 2 or triangles.shape[1] != 3
            or not np.issubdtype(triangles.dtype, np.integer) or len(triangles) > 3_000_000
            or triangles.size == 0 or triangles.min() < 0 or triangles.max() >= len(points)):
        raise ValueError('Viewport proxy: invalid or oversized map cell.')
    keys, remap = np.unique(np.rint(points / GRID_METRES).astype(np.int32), axis=0, return_inverse=True)
    faces = remap[triangles]
    keep = ((faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 2] != faces[:, 0]))
    faces = faces[keep]
    if not len(faces):
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int32)
    a = keys[faces[:, 1]].astype(np.int64) - keys[faces[:, 0]]
    b = keys[faces[:, 2]].astype(np.int64) - keys[faces[:, 0]]
    faces = faces[np.any(np.cross(a, b) != 0, axis=1)]
    if not len(faces):
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int32)
    _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    faces = faces[np.sort(first)]
    used, inverse = np.unique(faces, return_inverse=True)
    return keys[used].astype(np.float32) * GRID_METRES, inverse.reshape(-1, 3).astype(np.int32)


def build_cached_proxy(scene, source):
    import bpy
    if source.library or source.hide_render or source.hide_viewport or bpy.data.collections.get(COLLECTION):
        raise ValueError('Viewport proxy: prepared source is linked, hidden, or already has a proxy.')
    groups = defaultdict(list)
    ignored = []
    objects = list(source.all_objects)
    lights = {o.name for o in objects if o.type == 'LIGHT'}
    for obj in objects:
        if obj.type not in {'MESH', 'LIGHT', 'EMPTY', 'VOLUME'} or obj.animation_data or obj.instance_type != 'NONE':
            raise ValueError('Viewport proxy: unqualified prepared map object: ' + obj.name)
        if obj.type != 'MESH':
            continue
        match = _CELL.match(obj.name)
        if match is None:
            visibility = ('visible_camera', 'visible_diffuse', 'visible_glossy',
                          'visible_transmission', 'visible_volume_scatter')
            if (obj.get('dlr_barn_mask') not in lights or len(obj.data.vertices) != 192
                    or len(obj.data.polygons) != 96 or any(getattr(obj, flag) for flag in visibility)
                    or not obj.visible_shadow):
                raise ValueError('Viewport proxy: unqualified non-cell render mesh: ' + obj.name)
            ignored.append(obj.name)
            continue
        if obj.hide_render or obj.hide_viewport or obj.modifiers or obj.data.shape_keys:
            raise ValueError('Viewport proxy: unqualified static mesh settings: ' + obj.name)
        groups[match.group(1)].append(obj)
    if not groups or len(ignored) > 4096:
        raise ValueError('Viewport proxy: incomplete qualified Midtown source hierarchy.')
    bpy.context.view_layer.update()
    proxy = bpy.data.collections.new(COLLECTION)
    proxy.hide_render = True
    material = bpy.data.materials.new('RenderLock | viewport map')
    material.diffuse_color = (.38, .38, .38, 1)
    material.use_nodes = True
    material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = material.diffuse_color
    counts = dict(source_meshes=0, source_vertices=0, source_faces=0,
                  proxy_meshes=0, proxy_vertices=0, proxy_faces=0)
    omitted = []
    for cell, meshes in sorted(groups.items()):
        points = []; indices = []; offset = 0
        if (sum(len(o.data.vertices) for o in meshes) > 4_000_000
                or sum(len(o.data.polygons) for o in meshes) > 3_000_000):
            raise ValueError('Viewport proxy: map cell exceeds qualified work bounds: ' + cell)
        for obj in meshes:
            mesh = obj.data
            totals = np.empty(len(mesh.polygons), np.int32)
            mesh.polygons.foreach_get('loop_total', totals)
            if not np.all(totals == 3):
                raise ValueError('Viewport proxy: non-triangle map cell: ' + obj.name)
            coords = np.empty(len(mesh.vertices) * 3, np.float32)
            mesh.vertices.foreach_get('co', coords)
            matrix = np.asarray(obj.matrix_world, dtype=np.float64)
            points.append(coords.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3])
            faces = np.empty(len(mesh.loops), np.int32)
            mesh.loops.foreach_get('vertex_index', faces)
            indices.append(faces + offset)
            offset += len(mesh.vertices)
            counts['source_meshes'] += 1
            counts['source_vertices'] += len(mesh.vertices)
            counts['source_faces'] += len(mesh.polygons)
        vertices, faces = simplify(np.concatenate(points), np.concatenate(indices).reshape(-1, 3))
        if not len(faces):
            omitted.append(cell)
            continue
        mesh = bpy.data.meshes.new('RenderLock proxy | ' + cell)
        mesh.vertices.add(len(vertices))
        mesh.vertices.foreach_set('co', vertices.reshape(-1))
        mesh.loops.add(faces.size)
        mesh.loops.foreach_set('vertex_index', faces.reshape(-1))
        mesh.polygons.add(len(faces))
        mesh.polygons.foreach_set('loop_start', np.arange(len(faces), dtype=np.int32) * 3)
        mesh.polygons.foreach_set('loop_total', np.full(len(faces), 3, np.int32))
        mesh.materials.append(material)
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new('RenderLock proxy | ' + cell, mesh)
        obj.hide_render = True
        proxy.objects.link(obj)
        counts['proxy_meshes'] += 1
        counts['proxy_vertices'] += len(vertices)
        counts['proxy_faces'] += len(faces)
        print(f'RenderLock map proxy: {cell} cached', flush=True)
    if counts['proxy_meshes'] == 0 or counts['proxy_faces'] >= counts['source_faces']:
        raise ValueError('Viewport proxy: qualified geometry was not reduced.')
    scene.collection.children.link(proxy)
    source.hide_viewport = True
    return dict(status='ready', algorithm=ALGORITHM, collection=COLLECTION, **counts,
                excluded_render_masks=ignored, collapsed_cells=omitted,
                full_map_hidden_in_viewport=True, proxy_hidden_in_render=True)
