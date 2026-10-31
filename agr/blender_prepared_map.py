"""Turn a supported builder output into RenderLock's portable map package.

Run in background Blender. Game extraction and shader construction are performed
by the separate upstream builder; this module qualifies and packages its output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np


def geometry_fingerprint(collection):
    digest = hashlib.sha256()
    for obj in sorted(collection.all_objects, key=lambda item: item.name):
        digest.update(obj.name.encode('utf-8'))
        digest.update(np.asarray(obj.matrix_world, dtype=np.float64).tobytes())
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        for items, field, width, dtype in ((mesh.vertices, 'co', 3, np.float32),
                (mesh.loops, 'vertex_index', 1, np.int32), (mesh.polygons, 'loop_total', 1, np.int32)):
            values = np.empty(len(items) * width, dtype)
            items.foreach_get(field, values)
            digest.update(values.tobytes())
        for uv in mesh.uv_layers:
            values = np.empty(len(uv.data) * 2, np.float32)
            uv.data.foreach_get('uv', values)
            digest.update(uv.name.encode('utf-8'))
            digest.update(values.tobytes())
    return digest.hexdigest()


def retain_sky_mips(world, output):
    """Store fog's box-filtered sky images as explicit float resources."""
    import bpy
    sources = {n.image for n in world.node_tree.nodes if n.type == 'TEX_ENVIRONMENT' and n.image}
    if len(sources) != 1:
        raise ValueError('External map: prepared sky image is ambiguous.')
    sky = next(iter(sources))
    width, height = sky.size
    original = np.empty(width * height * 4, np.float32)
    sky.pixels.foreach_get(original)
    if not np.isfinite(original).all():
        raise ValueError('External map: non-finite sky pixels.')
    original = original.reshape(height, width, 4)
    result = []
    for level in (4, 7):
        old = bpy.data.images.get(f'{sky.name} mip{level}')
        factor = 2**level
        if old is None or width % factor or height % factor:
            raise ValueError('External map: missing or unqualified fog sky mip.')
        pixels = original.reshape(height//factor, factor, width//factor, factor, 4).mean(axis=(1, 3))
        image = bpy.data.images.new(f'RenderLock | sky mip {level}', width//factor, height//factor,
                                    alpha=True, float_buffer=True)
        image.colorspace_settings.name = sky.colorspace_settings.name
        image.pixels.foreach_set(pixels.reshape(-1))
        target = output / 'lightmaps' / f'sky-mip-{level}.exr'
        target.parent.mkdir(parents=True, exist_ok=True)
        scene = bpy.context.scene
        settings = scene.render.image_settings
        previous = (settings.file_format, settings.color_mode, settings.color_depth,
                    scene.view_settings.view_transform, scene.view_settings.look,
                    scene.view_settings.exposure, scene.view_settings.gamma)
        try:
            settings.file_format = 'OPEN_EXR'
            settings.color_mode = 'RGBA'
            settings.color_depth = '32'
            scene.view_settings.view_transform = 'Standard'
            scene.view_settings.exposure = 0
            scene.view_settings.gamma = 1
            # Image.save() applies an image colour conversion here, including
            # for EXR. Float render output preserves the tested linear values.
            image.save_render(str(target), scene=scene)
        finally:
            settings.file_format, settings.color_mode, settings.color_depth = previous[:3]
            scene.view_settings.view_transform = previous[3]
            scene.view_settings.look = previous[4]
            scene.view_settings.exposure, scene.view_settings.gamma = previous[5:]
        saved = bpy.data.images.load(str(target), check_existing=False)
        saved.colorspace_settings.name = sky.colorspace_settings.name
        check = np.empty(pixels.size, np.float32)
        saved.pixels.foreach_get(check)
        error = float(np.max(np.abs(check.reshape(pixels.shape) - pixels)))
        if not np.isfinite(check).all() or error > 1e-3 * max(1., float(np.max(np.abs(pixels)))):
            raise ValueError('External map: stored fog sky pixels differ from their filtered source.')
        old.user_remap(saved)
        bpy.data.images.remove(old)
        bpy.data.images.remove(image)
        result.append(dict(level=level, file=target.relative_to(output).as_posix(),
                           maximum_pixel_error=error))
    return result


def prepare(source, output, provenance):
    import bpy
    from agr import map_package
    from agr.blender_prepared_proxy import build_cached_proxy
    from agr.blender_map_basis import _GROUP_NODES
    source = Path(source).resolve(); output = Path(output).resolve()
    if (source == output or output.is_relative_to(source.parent)
            or source.is_relative_to(output) or output.exists() and any(output.iterdir())):
        raise ValueError('External map: prepare into a new folder separate from the builder output.')
    if bpy.app.version[:2] != (5, 2):
        raise ValueError('External map: new map preparation is qualified on Blender 5.2 LTS.')
    digest = map_package.file_hash(source)
    if provenance.get('native_blend_sha256', digest) != digest:
        raise ValueError('External map: builder output changed before package preparation.')
    if (provenance.get('kind') not in {'hosted_snapshot', 'installed_game'}
            or not str(provenance.get('game_version', '')).isdigit()):
        raise ValueError('External map: missing builder source version.')
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False)
    if bpy.data.libraries:
        raise ValueError('External map: builder output still contains library dependencies.')
    collection = bpy.data.collections.get('Midtown')
    world = bpy.data.worlds.get('Midtown sky')
    if collection is None or world is None or bpy.data.collections.get(map_package.COLLECTION):
        raise ValueError('External map: incomplete or ambiguous builder hierarchy.')
    for name, count in _GROUP_NODES.items():
        group = bpy.data.node_groups.get(name)
        if group is None or len(group.nodes) != count or group.get('renderlock_lighting_basis'):
            raise ValueError('External map: unsupported prepared lighting graph: ' + name)
    output.mkdir(parents=True, exist_ok=True)
    root = source.parent
    copied = {}

    def copy_file(filepath):
        target_source = Path(bpy.path.abspath(filepath)).resolve()
        if not target_source.is_file() or not target_source.is_relative_to(root):
            raise ValueError('External map: builder resource is missing or outside its folder: ' + str(target_source))
        original = target_source.relative_to(root).as_posix()
        map_package.relative_path(original)
        kind = original.split('/')[0]
        if kind not in {'textures', 'lightmaps', 'volumes'}:
            raise ValueError('External map: unqualified builder resource location: ' + original)
        expected = map_package.file_hash(target_source)
        # Game material basenames can be very long. Short, content-addressed
        # package paths also work when users extract into a nested Downloads folder.
        relative = kind + '/' + expected[:24] + target_source.suffix.lower()
        if relative in copied and copied[relative] != expected:
            raise ValueError('External map: resource identifiers collided.')
        target = output / relative
        if relative not in copied:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target_source, target)
            if map_package.file_hash(target) != expected:
                raise ValueError('External map: resource copy failed: ' + relative)
            copied[relative] = expected
        return '//' + relative

    rebased = []
    for image in bpy.data.images:
        if image.source == 'FILE' and not image.packed_file:
            rebased.append((image,copy_file(image.filepath)))
    for volume in bpy.data.volumes:
        if volume.filepath:
            rebased.append((volume,copy_file(volume.filepath)))
    # Resolve the original sky before rewriting relative paths to the output.
    # FILE pixels can be lazy-loaded; the new hashed name does not exist beside
    # the still-open source mainfile.
    sky_mips = retain_sky_mips(world, output)
    for block,filepath in rebased:
        block.filepath = filepath
    for image in bpy.data.images:
        if (image.source == 'FILE' and not image.packed_file and not image.filepath.startswith('//')
                and Path(image.filepath).is_absolute()):
            relative = Path(image.filepath).resolve().relative_to(output).as_posix()
            image.filepath = '//' + relative
    if any(image.source != 'FILE' or image.packed_file for image in bpy.data.images):
        raise ValueError('External map: package has unexpected generated or packed image data.')
    # Map data must not steal generated character image/material/group names.
    for blocks in (bpy.data.images, bpy.data.materials, bpy.data.node_groups, bpy.data.meshes):
        for block in list(blocks):
            block.name = map_package.NAMESPACE + block.name
    collection.name = map_package.COLLECTION
    bpy.context.view_layer.update()
    geometry = geometry_fingerprint(collection)
    proxy = build_cached_proxy(bpy.context.scene, collection)
    if geometry_fingerprint(collection) != geometry:
        raise ValueError('External map: proxy preparation changed full-map geometry or UVs.')
    objects = list(collection.all_objects)
    inventory = dict(objects=len(objects), meshes=sum(o.type == 'MESH' for o in objects),
        vertices=sum(len(o.data.vertices) for o in objects if o.type == 'MESH'),
        faces=sum(len(o.data.polygons) for o in objects if o.type == 'MESH'),
        file_images=len(bpy.data.images), geometry_sha256=geometry)
    provenance = dict(provenance, native_blend_sha256=digest)
    profile = dict(format=map_package.FORMAT, schema_version=2,
        package_id=f'midtown-{provenance["game_version"]}-{digest[:12]}-v2',
        builder=dict(commit=map_package.BUILDER_COMMIT, map_builder=14),
        source=provenance, map_name='dl_midtown', blender_min=[5, 2, 0],
        source_frame='vrf_blender_metres', collection=map_package.COLLECTION,
        world=world.name, namespace=map_package.NAMESPACE,
        inventory=inventory, proxy=proxy, stored_sky_mips=sky_mips)
    collection['renderlock_map_profile'] = json.dumps(profile, sort_keys=True)
    bpy.context.scene['renderlock_prepared_map'] = json.dumps(profile, sort_keys=True)
    bpy.context.scene.world = world
    # Every dependency has deliberately been rebased to the output folder.
    bpy.ops.wm.save_as_mainfile(filepath=str(output / map_package.LIBRARY), relative_remap=False)
    files = []
    for path in sorted(output.rglob('*')):
        if path.is_file():
            files.append(dict(file=path.relative_to(output).as_posix(), bytes=path.stat().st_size,
                              sha256=map_package.file_hash(path)))
    profile['files'] = files
    profile['library'] = next(row for row in files if row['file'] == map_package.LIBRARY)
    map_package.validate_document(profile)
    (output / map_package.MANIFEST).write_text(json.dumps(profile, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    map_package.load_package(output)
    print('RENDERLOCK_MAP_READY=' + str(output / map_package.LIBRARY), flush=True)
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else None)
    provenance = json.loads(args.provenance.read_text(encoding='utf-8'))
    if provenance.get('kind') == 'installed_game':
        from agr.map_builder import adapter_identity
        if provenance.get('adapter_sha256') != adapter_identity():
            raise ValueError('Map preparation changed during the native build. The completed native map is retained; run Build from game again with the current version.')
    prepare(args.source, args.output, provenance)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
