"""Real Blender map-only repair, shared-image, packing and save/reopen checks."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agr import blender_map_textures as textures

out = Path(os.environ['RENDERLOCK_TEXTURE_TEST_DIR'])
out.mkdir(parents=True, exist_ok=True)
folder = out/'package'; (folder/'Textures').mkdir(parents=True, exist_ok=True)


def png(color):
    def chunk(name, payload):
        return struct.pack('!I', len(payload)) + name + payload + struct.pack('!I', zlib.crc32(name + payload))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0' + bytes(color))) + chunk(b'IEND', b''))


rows = []
for name, color in [('red.png', (255, 0, 0, 255)), ('normal.png', (128, 128, 255, 255)),
                    ('artist.png', (0, 255, 0, 255))]:
    raw = png(color); (folder/'Textures'/name).write_bytes(raw)
    rows.append(dict(file='Textures/'+name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
manifest = json.dumps(dict(schema_version=1, package_id=textures.PACKAGE_ID,
    library=dict(sha256=textures.LIBRARY_SHA256), textures=rows)).encode()
(folder/'map-package.json').write_bytes(manifest)
textures.MANIFEST_SHA256 = hashlib.sha256(manifest).hexdigest()
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
mapping = bpy.data.collections.new('RenderLockMap'); scene.collection.children.link(mapping)
mapping['renderlock_map_profile'] = json.dumps(dict(package_id=textures.PACKAGE_ID))
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object; obj.name = 'map plane'
for collection in list(obj.users_collection): collection.objects.unlink(obj)
mapping.objects.link(obj)
material = bpy.data.materials.new('map material'); material.use_nodes = True
obj.data.materials.append(material)
tree = material.node_tree; tree.nodes.clear()
output = tree.nodes.new('ShaderNodeOutputMaterial')
emission = tree.nodes.new('ShaderNodeEmission'); tree.links.new(emission.outputs[0], output.inputs[0])
red = bpy.data.images.load(str(folder/'Textures/red.png'))
red.filepath_raw = '//author\\old-machine\\red.png'
red_node = tree.nodes.new('ShaderNodeTexImage'); red_node.image = red
tree.links.new(red_node.outputs['Color'], emission.inputs['Color'])
# Nested group, repeated twice: image traversal must use group identity.
group = bpy.data.node_groups.new('map normal group', 'ShaderNodeTree')
normal_node = group.nodes.new('ShaderNodeTexImage')
normal = bpy.data.images.load(str(folder/'Textures/normal.png')); normal.colorspace_settings.name = 'Non-Color'
normal.filepath_raw = '//author\\old-machine\\normal.png'; normal_node.image = normal
for _ in range(2): tree.nodes.new('ShaderNodeGroup').node_tree = group
# A separate character material shares the original image, so repair must copy it.
bpy.ops.mesh.primitive_cube_add(location=(10, 10, 0)); character = bpy.context.object
character.name = 'character'; character.keyframe_insert('location', frame=1)
character.location.x = 11; character.keyframe_insert('location', frame=3)
char_material = bpy.data.materials.new('character material'); char_material.use_nodes = True
char_node = char_material.node_tree.nodes.new('ShaderNodeTexImage'); char_node.image = red
character.data.materials.append(char_material)
artist = bpy.data.images.load(str(folder/'Textures/artist.png'))
artist_node = tree.nodes.new('ShaderNodeTexImage'); artist_node.image = artist
scene.frame_set(1); bpy.context.view_layer.update()
matrix = [list(row) for row in character.matrix_world]
colorspace = normal.colorspace_settings.name
old_red_path = red.filepath
# Refusal before any mutation if a single required file is missing.
missing = folder/'Textures/normal.png'; raw_normal = missing.read_bytes(); missing.unlink()
try:
    textures.connect_map_textures(scene, mapping, folder, only_missing=True)
    raise AssertionError('missing source must fail')
except ValueError as exc:
    assert 'Cannot read' in str(exc), str(exc)
assert red_node.image is red and red.filepath == old_red_path
missing.write_bytes(raw_normal)
# A shared material must be refused, rather than altering character shading.
character.data.materials.append(material)
try:
    textures.connect_map_textures(scene, mapping, folder, only_missing=True)
    raise AssertionError('shared material must fail')
except ValueError as exc:
    assert 'shared with objects outside' in str(exc)
character.data.materials.pop(index=1)
textures.register()
textures.register()  # Re-running a Text Editor script must not duplicate the command.
# Operator executes without opening a file selector or touching a visible UI.
assert bpy.ops.renderlock.repair_map_textures(filepath=str(folder/'RenderLockMap.blend')) == {'FINISHED'}
result = json.loads(scene['renderlock_map_textures'])
assert result['connected_images'] == 2 and result['copied_shared_images'] == 1
assert result['already_available'] == 1
assert char_node.image is red and red.filepath == old_red_path
assert red_node.image is not red
assert normal.colorspace_settings.name == colorspace and artist_node.image is artist
assert [list(row) for row in character.matrix_world] == matrix
assert not normal.has_data, 'unloaded textures must stay lazy'
repaired_red = red_node.image
repeat = textures.connect_map_textures(scene, mapping, folder, only_missing=True)
assert repeat['connected_images'] == 0 and repeat['already_available'] == 3
# Real shader render: repaired red is compared with a reference load of that PNG.
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.resolution_x = scene.render.resolution_y = 32; scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
camera_data = bpy.data.cameras.new('Camera'); camera = bpy.data.objects.new('Camera', camera_data)
scene.collection.objects.link(camera); camera.location = (0, 0, 3); camera_data.type = 'ORTHO'; camera_data.ortho_scale = 2.5
scene.camera = camera
scene.render.filepath = str(out/'repaired.png'); bpy.ops.render.render(write_still=True)
reference = bpy.data.images.load(str(folder/'Textures/red.png'), check_existing=False)
red_node.image = reference
scene.render.filepath = str(out/'reference.png'); bpy.ops.render.render(write_still=True)
def pixels(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    values = tuple(image.pixels); bpy.data.images.remove(image); return values
assert pixels(out/'repaired.png') == pixels(out/'reference.png')
red_node.image = repaired_red
# Packing only map resources must retain exact compressed bytes and colorspace.
packed = textures.connect_map_textures(scene, mapping, folder, only_missing=True, pack=True)
assert packed['packed_images'] == 3 and not red.packed_file
for image in (repaired_red, normal, artist):
    assert image.packed_file
    name = Path(image.filepath).name
    assert bytes(image.packed_file.data) == (folder/'Textures'/name).read_bytes()
scene['fixture_char_path'] = old_red_path
scene['fixture_result'] = json.dumps(result)
char_node_name = char_node.name
output_path = out/'repaired-packed.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(output_path), relative_remap=False)
# Remove the whole extracted folder after packing: saved map still has resources.
for row in rows: (folder/row['file']).unlink()
bpy.ops.wm.open_mainfile(filepath=str(output_path), load_ui=False)
assert bpy.data.materials['character material'].node_tree.nodes.get(char_node_name).image.filepath == old_red_path
for image in bpy.data.images:
    if image.packed_file:
        assert image.packed_file.size > 0
assert bpy.data.images['normal.png'].colorspace_settings.name == colorspace
assert len(bpy.data.objects['character'].animation_data.action.slots) == 1
(out/'result.json').write_text(json.dumps(dict(status='PASS', repair=result, packing=packed,
    reference_render_pixels_equal=True, scene_reopened_without_texture_folder=True), indent=2)+'\n')
print('PASS: map texture repair, shared image isolation, render, packing and reopen')
