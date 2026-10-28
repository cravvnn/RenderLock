"""Connect the supported map's existing image nodes to its verified PNG files.

This module is also the standalone Blender repair script. It has no RenderLock
imports, so the exact same code can run in Blender's Text Editor on an old scene.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath


bl_info = dict(name='RenderLock Map Texture Repair', author='RenderLock', version=(1, 0, 0),
               blender=(5, 2, 0), location='File > External Data', category='Import-Export',
               description='Reconnect the current downloaded map textures in an existing RenderLock scene')


PACKAGE_ID = 'midtown-rebatched-20261024'
LIBRARY_SHA256 = 'bd48e9355123bf01945753e74a2799d40b66b4d681dbfced35638ea356d5082b'
MANIFEST_SHA256 = 'fa94c9b8c398f8aa9a71ea712c6387d17070a99ea444e77188e05f4df21b24cc'
MAP_COLLECTION = 'RenderLockMap'


def validate_texture_completion(result, manifest_sha256=MANIFEST_SHA256):
    expected = dict(status='connected', package_id=PACKAGE_ID, library_sha256=LIBRARY_SHA256,
                    manifest_sha256=manifest_sha256, referenced_images=1556,
                    connected_images=1556, already_packed=0, already_available=0,
                    packed_images=0, copied_shared_images=0)
    if (not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items())
            or any(type(result.get(k)) is not int for k in expected if type(expected[k]) is int)):
        raise RuntimeError('External map: textures: Blender did not connect all requested map images; the scene was not published.')


def _error(message):
    return ValueError('External map: textures: ' + message)


def texture_package(path, *, verify_files=True):
    """Read the pinned download manifest; never search by an ambiguous basename."""
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        folder = folder.parent
    if folder.name == 'Textures':
        folder = folder.parent
    manifest = folder / 'map-package.json'
    try:
        if manifest.stat().st_size > 1024 * 1024:
            raise _error('The map manifest is too large.')
        raw = manifest.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
            raise _error('Choose the unchanged current map download, including map-package.json and Textures. Older map packages can still use Blender\'s Find Missing Files.')
        doc = json.loads(raw)
        if (doc['schema_version'] != 1 or doc['package_id'] != PACKAGE_ID
                or doc['library']['sha256'] != LIBRARY_SHA256):
            raise _error('The texture package does not match the supported map.')
        textures = {}
        for row in doc['textures']:
            name = PurePosixPath(row['file'])
            if (len(name.parts) != 2 or name.parts[0] != 'Textures'
                    or name.suffix != '.png' or '\\' in row['file']
                    or name.name in textures or name.name in {'.', '..'}):
                raise _error('The texture manifest has an invalid or duplicate path.')
            target = folder.joinpath(*name.parts).resolve()
            if not target.is_relative_to(folder):
                raise _error('A texture path leaves the extracted map folder.')
            textures[name.name] = dict(path=target, bytes=row['bytes'], sha256=row['sha256'])
        if verify_files:
            for name, row in textures.items():
                target = row['path']
                try:
                    before = target.stat()
                    if not target.is_file() or before.st_size != row['bytes']:
                        raise _error(f'Missing or changed {name}. Extract the entire map ZIP again, including Textures.')
                    with target.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    after = target.stat()
                    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                    if digest != row['sha256'] or identity(before) != identity(after):
                        raise _error(f'Changed {name}. Extract the entire map ZIP again, including Textures.')
                except OSError as exc:
                    raise _error(f'Cannot read {name}. Extract the entire map ZIP again, including Textures.') from exc
        return dict(folder=folder, package_id=PACKAGE_ID, library_sha256=LIBRARY_SHA256,
                    manifest_sha256=MANIFEST_SHA256, textures=textures)
    except OSError as exc:
        raise _error('Keep map-package.json and the entire Textures folder beside RenderLockMap.blend. Extract the whole map ZIP, not just the .blend.') from exc


def _image_nodes(tree, seen=None):
    if tree is None:
        return []
    if seen is None:
        seen = set()
    if tree in seen:
        return []
    seen.add(tree)
    nodes = []
    for node in tree.nodes:
        if getattr(node, 'image', None) is not None:
            nodes.append((tree, node))
        group = getattr(node, 'node_tree', None)
        if group is not None:
            nodes.extend(_image_nodes(group, seen))
    return nodes


def map_image_plan(scene, collection, package, *, only_missing=False, pack=False):
    """Plan all changes before mutation; artist-shared node trees are refused."""
    import bpy
    if collection.library is not None:
        raise _error('The map is linked. Make the map local before repairing its textures.')
    profile = json.loads(collection.get('renderlock_map_profile', '{}'))
    if profile.get('package_id') != package['package_id']:
        raise _error('This scene and texture download use different map versions. Use the matching download or Blender\'s Find Missing Files.')
    objects = set(collection.all_objects)
    materials = {slot.material for obj in objects for slot in obj.material_slots if slot.material}
    outside_materials = {slot.material for obj in bpy.data.objects if obj not in objects
                         for slot in obj.material_slots if slot.material}
    if materials & outside_materials:
        raise _error('Map materials are shared with objects outside RenderLockMap. Make those materials single-user before repairing.')
    nodes = []
    seen = set()
    for material in materials:
        if material.library is not None:
            raise _error('A map material is linked; make the map local first.')
        nodes.extend(_image_nodes(material.node_tree, seen))
    # Include unused/non-map materials, world, lights and compositors: changing
    # a shared image must never affect a character or other artist-created data.
    outside = []
    for block in [*(m for m in bpy.data.materials if m not in materials),
                  *bpy.data.worlds, *bpy.data.lights, *bpy.data.scenes]:
        outside.extend(_image_nodes(getattr(block, 'node_tree', None)))
    map_trees = {tree for tree, node in nodes}
    if any(tree in map_trees for tree, node in outside):
        raise _error('Map nodes are shared with other scene materials. Make those materials and node groups single-user before repairing.')
    outside_images = {node.image for tree, node in outside}
    by_image = {}
    for tree, node in nodes:
        if tree.library is not None:
            raise _error('A map node group is linked; make it local first.')
        by_image.setdefault(node.image, []).append(node)
    if not by_image:
        raise _error('No image texture nodes were found in RenderLockMap.')
    plan = []
    packed = available = 0
    for image, image_nodes in by_image.items():
        if image.library is not None or image.source != 'FILE':
            raise _error(f'Unsupported or linked map image: {image.name}.')
        if image.packed_file:
            packed += 1
            continue
        existing = Path(bpy.path.abspath(image.filepath, library=image.library)) if image.filepath else None
        if only_missing and existing is not None and existing.is_file() and not pack:
            available += 1
            continue
        if only_missing and existing is not None and existing.is_file() and pack:
            # Do not silently replace or embed an artist's replacement texture.
            basename = Path(image.filepath.replace('\\', '/')).name
            row = package['textures'].get(basename)
            if row is None or existing.resolve() != row['path']:
                available += 1
                continue
        basename = Path(image.filepath.replace('\\', '/')).name
        row = package['textures'].get(basename)
        if row is None:
            raise _error(f'No exact package filename for {image.name}: {basename}. No textures were changed.')
        plan.append(dict(image=image, nodes=image_nodes, source=row,
                         copy=image in outside_images, had_data=image.has_data))
    return plan, dict(referenced_images=len(by_image), already_packed=packed,
                      already_available=available)


def connect_map_textures(scene, collection, path, *, only_missing=False, pack=False):
    """Relink map nodes in place; geometry, poses and material inputs stay intact."""
    import bpy
    package = texture_package(path)
    if (only_missing and bpy.data.filepath
            and Path(bpy.data.filepath).resolve() == package['folder'] / 'RenderLockMap.blend'):
        raise _error('Open the exported replay scene to repair it. Keep the downloaded RenderLockMap.blend unchanged.')
    plan, counts = map_image_plan(scene, collection, package, only_missing=only_missing, pack=pack)
    changed = []
    copies = []
    try:
        for item in plan:
            original = item['image']
            image = original.copy() if item['copy'] else original
            if item['copy']:
                copies.append(image)
            before = image.filepath_raw
            changed.append((item, image, before))
            # filepath_raw avoids decoding all 1,556 PNGs into memory. Reload
            # only previously loaded pixels that need an in-place UI refresh.
            image.filepath_raw = str(item['source']['path'])
            if pack:
                raw = item['source']['path'].read_bytes()
                if hashlib.sha256(raw).hexdigest() != item['source']['sha256']:
                    raise _error('A texture changed while it was being packed.')
                image.pack(data=raw, data_len=len(raw))
                if not image.packed_file or hashlib.sha256(image.packed_file.data).hexdigest() != item['source']['sha256']:
                    raise _error('Blender did not retain the exact packed texture bytes.')
            if item['had_data']:
                image.reload()
            if item['copy']:
                for node in item['nodes']:
                    node.image = image
        result = dict(status='connected', package_id=package['package_id'],
                      library_sha256=package['library_sha256'], manifest_sha256=package['manifest_sha256'],
                      texture_folder=str(package['folder'] / 'Textures'),
                      connected_images=len(plan), copied_shared_images=len(copies),
                      packed_images=len(plan) if pack else 0, **counts)
        scene['renderlock_map_textures'] = json.dumps(result)
        return result
    except Exception:
        for item, image, before in reversed(changed):
            for node in item['nodes']:
                node.image = item['image']
            if image.packed_file:
                image.unpack(method='REMOVE')
            image.filepath_raw = before
            if item['had_data']:
                image.reload()
        for image in copies:
            bpy.data.images.remove(image)
        raise


def register():
    """Register a Blender-native repair command, without a separate app window."""
    import bpy
    from bpy.props import BoolProperty, StringProperty
    from bpy_extras.io_utils import ImportHelper

    class RENDERLOCK_OT_repair_map_textures(bpy.types.Operator, ImportHelper):
        bl_idname = 'renderlock.repair_map_textures'
        bl_label = 'Repair RenderLock map textures'
        bl_description = 'Choose the extracted RenderLockMap.blend to reconnect missing map textures in this scene'
        bl_options = {'REGISTER', 'UNDO'}
        filename_ext = '.blend'
        filter_glob: StringProperty(default='*.blend', options={'HIDDEN'})
        pack_textures: BoolProperty(name='Pack repaired textures into scene', default=False,
            description='Store the PNGs inside this scene so they survive moving it; adds up to about 1.34 GB')

        @staticmethod
        def draw_menu(menu, context):
            menu.layout.operator('renderlock.repair_map_textures', text='Repair RenderLock map textures')

        def execute(self, context):
            try:
                collection = context.scene.collection.children.get(MAP_COLLECTION)
                if collection is None:
                    raise _error('This scene has no RenderLockMap collection. Open the exported replay scene first.')
                result = connect_map_textures(context.scene, collection, self.filepath,
                                             only_missing=True, pack=self.pack_textures)
            except Exception as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            self.report({'INFO'}, f"RenderLock: connected {result['connected_images']} map images. Save your scene to keep the repair.")
            return {'FINISHED'}

    old = getattr(bpy.types, 'RENDERLOCK_OT_repair_map_textures', None)
    if old is not None:
        bpy.types.TOPBAR_MT_file_external_data.remove(old.draw_menu)
        bpy.utils.unregister_class(old)
    bpy.utils.register_class(RENDERLOCK_OT_repair_map_textures)
    bpy.types.TOPBAR_MT_file_external_data.append(RENDERLOCK_OT_repair_map_textures.draw_menu)


def unregister():
    import bpy
    operator = getattr(bpy.types, 'RENDERLOCK_OT_repair_map_textures', None)
    if operator is not None:
        bpy.types.TOPBAR_MT_file_external_data.remove(operator.draw_menu)
        bpy.utils.unregister_class(operator)


if __name__ == '__main__':
    register()
    import bpy
    bpy.ops.renderlock.repair_map_textures('INVOKE_DEFAULT')
