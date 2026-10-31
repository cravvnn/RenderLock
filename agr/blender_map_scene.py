"""Compose local replay data into a directly opened prepared map library."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np


def _image_bindings(scene, names):
    import bpy
    result = {}; digests = {}

    def visit(tree, prefix, active):
        if tree is None:
            return
        if tree.as_pointer() in active:
            raise ValueError('External map: recursive character material graph.')
        active = active | {tree.as_pointer()}
        for node in tree.nodes:
            key = prefix + '/' + node.name
            image = getattr(node, 'image', None)
            if image is not None:
                pointer = image.as_pointer()
                if pointer not in digests:
                    if not image.packed_file:
                        raise ValueError('External map: character material image was not packed before composition.')
                    digests[pointer] = hashlib.sha256(image.packed_file.data).hexdigest()
                result[key] = (digests[pointer], image.colorspace_settings.name)
            if node.type == 'GROUP':
                visit(node.node_tree, key, active)

    for name in names:
        obj = scene.objects[name]
        if obj.type != 'MESH':
            continue
        for index, slot in enumerate(obj.material_slots):
            if slot.material:
                visit(slot.material.node_tree, f'{name}/slot{index}', set())
    return result


def _animation_hash():
    import bpy
    digest = hashlib.sha256()
    for action in sorted(bpy.data.actions, key=lambda item: item.name):
        digest.update(action.name.encode('utf-8'))
        if getattr(action, 'is_action_legacy', False):
            curves = list(action.fcurves)
        else:
            curves = [curve for layer in action.layers for strip in layer.strips
                      for bag in getattr(strip, 'channelbags', ()) for curve in bag.fcurves]
        for curve in curves:
            digest.update(f'{curve.data_path}/{curve.array_index}/{curve.extrapolation}'.encode('utf-8'))
            for field in ('co', 'handle_left', 'handle_right'):
                values = np.empty(len(curve.keyframe_points)*2, np.float32)
                curve.keyframe_points.foreach_get(field, values)
                digest.update(values.tobytes())
            interpolation = np.empty(len(curve.keyframe_points), np.int32)
            curve.keyframe_points.foreach_get('interpolation', interpolation)
            digest.update(interpolation.tobytes())
    return digest.hexdigest()


def character_snapshot(scene, names):
    import bpy
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    end = float(scene.get('agr_end_frame_exact', scene.frame_end))
    frames = sorted({float(scene.frame_start), (scene.frame_start+end)/2, end})
    transform = hashlib.sha256()
    values = []
    layout = []
    geometry = hashlib.sha256()
    for name in names:
        obj = scene.objects[name]
        layout.append(dict(name=name,bones=[b.name for b in obj.pose.bones] if obj.type == 'ARMATURE' else []))
        geometry.update(name.encode('utf-8'))
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        for items, field, width, dtype in ((mesh.vertices,'co',3,np.float32),
                (mesh.loops,'vertex_index',1,np.int32),(mesh.polygons,'loop_total',1,np.int32)):
            data = np.empty(len(items)*width,dtype)
            items.foreach_get(field,data);geometry.update(data.tobytes())
        for uv in mesh.uv_layers:
            data = np.empty(len(uv.data)*2,np.float32)
            uv.data.foreach_get('uv',data);geometry.update(uv.name.encode('utf-8'));geometry.update(data.tobytes())
    for frame in frames:
        scene.frame_set(math.floor(frame), subframe=frame % 1)
        for name in names:
            obj = scene.objects[name]
            matrix = np.asarray(obj.matrix_world,dtype=np.float64)
            transform.update(matrix.tobytes());values.extend(matrix.reshape(-1).tolist())
            if obj.type == 'ARMATURE':
                for bone in obj.pose.bones:
                    matrix = np.asarray(bone.matrix,dtype=np.float64)
                    transform.update(matrix.tobytes());values.extend(matrix.reshape(-1).tolist())
    scene.frame_set(old_frame, subframe=old_subframe)
    return dict(transforms=transform.hexdigest(), frames=frames,
        transform_values=values,transform_layout=layout,geometry=geometry.hexdigest(),
        animation=_animation_hash(), bindings=_image_bindings(scene, names),
        packed={image.name:hashlib.sha256(image.packed_file.data).hexdigest()
                for image in bpy.data.images if image.packed_file},
        camera=scene.camera.name, fps=scene.render.fps, fps_base=scene.render.fps_base,
        frame_start=scene.frame_start, frame_end=scene.frame_end)


def snapshot_differences(before, after):
    """Keep source keys/geometry/resources exact; allow float32 matrix reevaluation."""
    changed = [key for key in before if key not in {'transforms','transform_values'}
               and before[key] != after.get(key)]
    first = np.asarray(before.get('transform_values'),dtype=np.float64)
    second = np.asarray(after.get('transform_values'),dtype=np.float64)
    if (first.ndim != 1 or not first.size or first.shape != second.shape
            or not np.isfinite(first).all() or not np.isfinite(second).all()
            or not np.allclose(first,second,rtol=2e-7,atol=2e-7)):
        changed.append('transforms')
    return changed


def compose_prepared_map(character_path, output, library, coordinates, *, proxy_enabled, project_scene):
    import bpy
    from mathutils import Matrix
    from . import map_package
    from .blender_map_basis import prepare_lighting_basis
    from .blender_prepared_map import geometry_fingerprint
    from .blender_external_map import remove_unused_library_records
    from .external_map import map_coordinate_matrix, validate_map_completion, validate_map_proxy_completion
    if bpy.app.version[:2] != (5, 2):
        raise ValueError('External map: prepared maps are qualified on Blender 5.2 LTS.')
    package = map_package.load_package(library['path'])
    doc = package['document']
    scene = bpy.context.scene
    active_layer_name = bpy.context.view_layer.name
    names = sorted(obj.name for obj in scene.objects)
    original = character_snapshot(scene, names)
    bpy.ops.wm.open_mainfile(filepath=library['path'], load_ui=False)
    map_scene = bpy.context.scene
    mapping = bpy.data.collections.get(map_package.COLLECTION)
    proxy = bpy.data.collections.get(map_package.PROXY_COLLECTION)
    if mapping is None or proxy is None or bpy.data.libraries or set(names) & {o.name for o in bpy.data.objects}:
        raise ValueError('External map: prepared map is incomplete, linked, or collides with character object names.')
    # A cached proxy deliberately hides the full map in the saved viewport.
    # Evaluate its authored hierarchy before checking or parenting world matrices.
    mapping.hide_viewport = False
    bpy.context.view_layer.update()
    objects = list(mapping.all_objects); meshes = [o for o in objects if o.type == 'MESH']
    inventory = doc['inventory']
    if (len(objects) != inventory['objects'] or len(meshes) != inventory['meshes']
            or sum(len(o.data.vertices) for o in meshes) != inventory['vertices']
            or geometry_fingerprint(mapping) != inventory['geometry_sha256']
            or len(bpy.data.images) != inventory['file_images']):
        raise ValueError('External map: prepared map geometry or image inventory differs from its manifest.')
    proxy_meshes = list(proxy.all_objects)
    expected = doc['proxy']
    if (mapping.hide_render or not proxy.hide_render
            or any(o.type != 'MESH' or not o.hide_render or o.parent for o in proxy_meshes)
            or len(proxy_meshes) != expected['proxy_meshes']
            or sum(len(o.data.vertices) for o in proxy_meshes) != expected['proxy_vertices']
            or sum(len(o.data.polygons) for o in proxy_meshes) != expected['proxy_faces']):
        raise ValueError('Viewport proxy: cached geometry or render isolation differs from its manifest.')
    map_images = list(bpy.data.images); volumes = list(bpy.data.volumes)
    world = bpy.data.worlds.get(doc['world'])
    matrix = Matrix(map_coordinate_matrix(coordinates))
    if not np.isclose(np.linalg.norm(np.asarray(matrix, dtype=float)[:3, 0]), 1., rtol=0, atol=2e-6):
        raise ValueError('External map: prepared lighting currently requires metre-based replay coordinates (unit_scale 0.0254).')
    lighting = prepare_lighting_basis(world, matrix, namespace=doc['namespace'])
    with bpy.data.libraries.load(str(character_path), link=False) as (available, loaded):
        for kind in ('scenes', 'images', 'node_groups', 'materials', 'worlds', 'actions', 'texts'):
            setattr(loaded, kind, list(getattr(available, kind)))
    if len(loaded.scenes) != 1:
        raise ValueError('External map: generated character stage has an ambiguous scene.')
    for kind in ('images', 'node_groups', 'materials', 'worlds', 'actions', 'texts'):
        for item in getattr(loaded, kind):
            if item is not None:
                item.use_fake_user = True
    scene = loaded.scenes[0]
    bpy.context.window.scene = scene
    bpy.context.window.view_layer = scene.view_layers[active_layer_name]
    scene.collection.children.link(mapping)
    scene.collection.children.link(proxy)
    scene.world = world
    for layer in scene.view_layers:
        if layer.name == 'RenderLockChar':
            for collection in (mapping, proxy):
                node = next((child for child in layer.layer_collection.children if child.collection is collection), None)
                if node is None:
                    raise ValueError('External map: could not preserve the characters-only view layer.')
                node.exclude = True
    bpy.data.scenes.remove(map_scene)
    basis = bpy.data.objects.new('RenderLock | map alignment', None)
    mapping.objects.link(basis);basis.matrix_world = matrix
    for obj in objects:
        if obj.parent is None:
            authored = obj.matrix_world.copy()
            obj.parent = basis;obj.matrix_parent_inverse = Matrix.Identity(4);obj.matrix_basis = authored
    for obj in proxy_meshes:
        obj.matrix_world = matrix @ obj.matrix_world
    mapping.hide_viewport = bool(proxy_enabled)
    proxy.hide_viewport = not proxy_enabled
    remove_unused_library_records()
    if any(block.library for block in bpy.data.user_map()):
        raise ValueError('External map: composition left linked data behind.')
    composed = character_snapshot(scene, names)
    changed = snapshot_differences(original, composed)
    if changed:
        Path(output).with_suffix('.character-integrity.json').write_text(
            json.dumps(dict(changed=changed,before=original,after=composed),indent=2)+'\n',encoding='utf-8')
        raise ValueError('External map: composition changed character animation, materials or packed resources: ' + ', '.join(changed))
    assets = map_package.install_project_assets(package, project_scene)
    resource_paths = {row['file']:row for row in doc['files'] if row['file'] != map_package.LIBRARY}
    for block in [*map_images, *volumes]:
        source = Path(bpy.path.abspath(block.filepath)).resolve()
        if not source.is_relative_to(package['folder']):
            raise ValueError('External map: loaded resource leaves the prepared package.')
        relative = source.relative_to(package['folder']).as_posix()
        if relative not in resource_paths:
            raise ValueError('External map: undeclared image or volume dependency.')
        target = map_package.project_asset_path(assets, resource_paths[relative])
        block.filepath = '//' + target.relative_to(Path(project_scene).resolve().parent).as_posix()
    result = dict(status='appended', workflow='prepared_map_first', package_id=doc['package_id'],
        library_sha256=library['sha256'], prepared_manifest_sha256=package['manifest_sha256'],
        collection=mapping.name, character_collection='RenderLockChar', imported_objects=len(objects),
        mesh_objects=len(meshes), vertices=inventory['vertices'], coordinate_matrix=[list(row) for row in matrix],
        texture_relink='automatic', map_cutting='manual', linked_libraries=len(bpy.data.libraries),
        lighting_basis=lighting,
        textures=dict(status='connected', manifest_sha256=package['manifest_sha256'],
            referenced_images=len(map_images), connected_images=len(map_images),
            external_files=len(resource_paths), project_relative=True),
        limits=['Static map snapshot; geometry and ambient prop motion can differ from the replay.',
                'Map materials, baked lighting, reflections and fog are reconstructed approximations; full game-render parity is not implied.',
                'Keep RenderLockMapAssets beside the exported scene. The viewport proxy does not remove full-map loading or render memory.'])
    validate_map_completion(result, library, coordinates)
    proxy_result = dict(expected, source_map_sha256=library['sha256'], cached=True)
    if proxy_enabled:
        validate_map_proxy_completion(proxy_result, result, library)
    scene['renderlock_external_map'] = json.dumps(result)
    if proxy_enabled:
        scene['renderlock_map_proxy'] = json.dumps(proxy_result)
    scene['renderlock_character_composition'] = json.dumps(dict(status='verified',
        sampled_frames=original['frames'], animation_keys=original['animation'],
        geometry_uv=original['geometry'], packed_images=len(original['packed']),
        material_image_bindings=len(original['bindings']),
        maximum_matrix_error=float(np.max(np.abs(np.asarray(original['transform_values'])-
                                                np.asarray(composed['transform_values'])))),
        matrix_tolerance=dict(rtol=2e-7,atol=2e-7)))
    return scene, result, proxy_result if proxy_enabled else None


def build_map_first(build, segment, assets, output, *, library, project_scene, **options):
    import bpy
    started = time.monotonic()
    output = Path(output).resolve()
    character_path = output.with_name(output.stem + '.characters.blend')
    if character_path.exists():
        raise ValueError('External map: use a fresh job folder for prepared-map composition.')
    proxy_enabled = options.pop('map_viewport_proxy')
    report = build(segment, assets, character_path, external_map=None, map_viewport_proxy=False, **options)
    scene, mapping, proxy = compose_prepared_map(character_path, output, library, report['coordinates'],
        proxy_enabled=proxy_enabled, project_scene=project_scene or output)
    report['output'] = str(output);report['external_map'] = mapping
    report['warnings'].extend(mapping['limits'])
    if proxy is not None:
        report['map_proxy'] = proxy
    report['timings'].append(dict(stage='prepared_map_composition', seconds=round(time.monotonic()-started, 3)))
    readme = bpy.data.texts.get('AGR_README')
    if readme is not None:
        introduction = readme.as_string().split('\n{',1)[0]
        readme.clear();readme.write(introduction+'\n'+json.dumps(report,indent=2))
    # Project-relative map paths target the final published filename, not the
    # temporary job file. The publisher moves this file without rewriting it.
    bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=True, relative_remap=False)
    output.with_suffix('.scene.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report
