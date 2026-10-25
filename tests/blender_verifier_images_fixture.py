"""Small background Blender fixture for the saved-scene image contract.

Run with Blender --background --factory-startup --disable-autoexec
--python-exit-code 1 --python this_script -- --output /owned/test-case

It creates RenderLockChar/RenderLockMap collections with packed and external
images, then checks that only character-owned images fail the verifier. No
private game assets or full scene package are required because only the image
classification function runs.
"""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import bpy
from agr.external_map import CHAR_COLLECTION, MAP_COLLECTION
from agr.pose_verification import verification_status
from verify_saved_scene import classify_scene_images


def texture(path, name):
    image = bpy.data.images.new(name, 4, 4, alpha=True)
    image.filepath_raw = str(path)
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)
    return bpy.data.images.load(str(path))


def material(name, image):
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    node = value.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = image
    return value


def collection(name, image_name, *, packed):
    master = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(master)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = name + ' mesh'
    obj.data.materials.append(material(name + ' material', bpy.data.images[image_name]))
    if packed:
        bpy.data.images[image_name].pack()
    master.objects.link(obj)
    bpy.context.scene.collection.objects.unlink(obj)
    return master


def status(images, *, accepted=False, clothing=False):
    result = dict(max_root_error_metres=0., max_bone_matrix_error=0., external_libraries=0,
                  cloth_bone_checks_not_performed=1 if clothing else 0,
                  entities=[], **images)
    result['map_images_external_accepted'] = accepted
    result['limits'] = []
    return verification_status(result)


def main(output):
    output.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    paths = output / 'textures'
    paths.mkdir()
    char = texture(paths / 'char.png', 'char_tex')
    map_texture = texture(paths / 'map.png', 'map_tex')
    stray = texture(paths / 'stray.png', 'stray_tex')
    extra = texture(paths / 'extra-char.png', 'extra_char_tex')
    collection(CHAR_COLLECTION, char.name, packed=True)
    collection(MAP_COLLECTION, map_texture.name, packed=False)
    # A second, unpacked character texture makes the hard-fail path explicit.
    char_mesh = bpy.data.collections[CHAR_COLLECTION].objects[0]
    char_mesh.data.materials.append(material('second character material', extra))

    declared = {'map_workflow': {'status': 'external_blend_appended'}}
    implicit = classify_scene_images(declared)
    assert implicit['map_unpacked_images'] == sorted([map_texture.name, stray.name])
    assert implicit['unpacked_images'] == [extra.name]
    assert status(implicit) == 'FAIL'

    accepted = classify_scene_images(declared, external_map_images=True)
    assert accepted['unpacked_images'] == [extra.name]
    assert accepted['map_images_external_accepted'] is True
    assert status(accepted, accepted=True) == 'FAIL'

    # Remove the unpacked character texture: map-only limits become PARTIAL,
    # then PASS only with explicit acceptance. Declared cloth still returns
    # PARTIAL and never becomes a full pass.
    char_mesh.data.materials.pop(index=1)
    map_only = classify_scene_images(declared)
    assert map_only['unpacked_images'] == []
    assert map_only['limits'] and 'external by design' in map_only['limits'][0]
    assert status(map_only) == 'PARTIAL'
    assert status(map_only, accepted=True) == 'PASS'
    assert status(map_only, accepted=True, clothing=True) == 'PARTIAL'

    # Without the external-map declaration, images no material reaches remain
    # character-contract failures even though the map collection exists.
    strict = classify_scene_images({})
    assert strict['unpacked_images'] == sorted([extra.name, stray.name])
    assert status(strict) == 'FAIL'

    (output / 'result.json').write_text(json.dumps(dict(
        status='PASS', implicit=implicit, map_only=map_only, strict=strict,
        limits='Synthetic background Blender fixture; image ownership/classification only. '
               'No native Linux, pose comparison or full-map qualification.'), indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    main(args.output)
