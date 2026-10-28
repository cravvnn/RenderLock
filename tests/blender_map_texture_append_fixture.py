"""Exercise the production append and completion path with a small Blender library."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agr import external_map as maps
from agr import blender_map_textures as textures
from agr.blender_external_map import append_external_map, group_renderlock_content

out = Path(os.environ['RENDERLOCK_TEXTURE_TEST_DIR'])
out.mkdir(parents=True, exist_ok=True)
folder = out/'package'; (folder/'Textures').mkdir(parents=True)
def chunk(name, payload):
    return struct.pack('!I',len(payload))+name+payload+struct.pack('!I',zlib.crc32(name+payload))
raw = (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,6,0,0,0))
       +chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\xff'))+chunk(b'IEND',b''))
image_path = folder/'Textures/red.png'; image_path.write_bytes(raw)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
collection = bpy.data.collections.new('RenderLockMap'); scene.collection.children.link(collection)
collection['renderlock_map_profile'] = json.dumps(dict(package_id=maps.MAP_PACKAGE_ID, source_frame='vrf_blender_metres'))
bpy.ops.mesh.primitive_plane_add()
obj = bpy.context.object
for previous in list(obj.users_collection): previous.objects.unlink(obj)
collection.objects.link(obj)
material = bpy.data.materials.new('map material'); material.use_nodes=True
obj.data.materials.append(material)
for index in range(1556):
    image = bpy.data.images.new('map-image-'+str(index), width=1, height=1)
    image.source='FILE'; image.filepath_raw='//old-author-folder\\red.png'
    material.node_tree.nodes.new('ShaderNodeTexImage').image=image
library_path = folder/'RenderLockMap.blend'
bpy.data.libraries.write(str(library_path), {collection}, path_remap='NONE', fake_user=False, compress=True)
library_hash = hashlib.sha256(library_path.read_bytes()).hexdigest()
maps.MAP_BLEND_SHA256 = textures.LIBRARY_SHA256 = library_hash
maps.MAP_BLEND_BYTES = library_path.stat().st_size
maps.MAP_OBJECTS = maps.MAP_MESHES = 1; maps.MAP_VERTICES = 4
doc = dict(schema_version=1, package_id=maps.MAP_PACKAGE_ID, library=dict(sha256=library_hash),
           textures=[dict(file='Textures/red.png', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())])
manifest = json.dumps(doc).encode(); (folder/'map-package.json').write_bytes(manifest)
textures.MANIFEST_SHA256 = hashlib.sha256(manifest).hexdigest()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
bpy.ops.mesh.primitive_cube_add(location=(3,4,5)); character=bpy.context.object; character.name='character'
group_renderlock_content(scene)
result=append_external_map(scene, library_path, None)
library=maps.validate_map_library(library_path)
maps.validate_map_completion(result, library)
assert result['texture_relink']=='automatic' and result['textures']['connected_images']==1556
assert tuple(character.location)==(3,4,5)
assert not any(block.library for block in bpy.data.user_map())
assert not any(image.has_data for image in bpy.data.images)
path=out/'automatic.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=False)
bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False)
assert all(Path(bpy.path.abspath(image.filepath)).resolve()==image_path.resolve() for image in bpy.data.images)
maps.validate_map_completion(json.loads(bpy.context.scene['renderlock_external_map']),library)
(out/'result.json').write_text(json.dumps(dict(status='PASS', map=result,
    production_append_and_completion=True, saved_and_reopened=True),indent=2)+'\n')
print('PASS: production map append connects all requested textures and completion survives reopen')
