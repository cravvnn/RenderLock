"""Run in background Blender with RENDERLOCK_PROXY_TEST_DIR set to a scratch directory."""
import json
import math
import os
import sys
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agr import external_map as maps
from agr.blender_map_proxy import build_map_proxy

out = Path(os.environ['RENDERLOCK_PROXY_TEST_DIR'])
out.mkdir(parents=True, exist_ok=True)
maps.MAP_MESHES, maps.MAP_VERTICES = 2, 16
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 16
scene.cycles.seed = 7
scene.render.resolution_x = scene.render.resolution_y = 128
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.world.color = (0.01, 0.01, 0.01)
mapping = bpy.data.collections.new('RenderLockMap')
scene.collection.children.link(mapping)
bpy.ops.mesh.primitive_plane_add(size=5)
receiver = bpy.context.object
receiver.name = 'character receiver'
alignment = bpy.data.objects.new('Map alignment', None)
mapping.objects.link(alignment)
alignment.location = (.25, 0, 0)
alignment.rotation_euler.z = math.pi/2

def map_cube(name, position, size):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    mapping.objects.link(obj)
    obj.name = name
    obj.parent = alignment
    obj.location = position
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    return obj

caster = map_cube('cell_0_0__offscreen_caster', (2.5, 0, 3), 1)
building = map_cube('cell_1_0__building', (.7, -.8, .3), .6)
camera_data = bpy.data.cameras.new('Camera')
camera = bpy.data.objects.new('Camera', camera_data)
scene.collection.objects.link(camera)
camera.location = (.25, 0, 10)
camera_data.type = 'ORTHO'
camera_data.ortho_scale = 3.2
scene.camera = camera
light_data = bpy.data.lights.new('Light', 'POINT')
light_data.energy = 1000
light_data.shadow_soft_size = .01
light = bpy.data.objects.new('Light', light_data)
scene.collection.objects.link(light)
light.location = (.25, 5, 6)
character_layer = scene.view_layers.new('RenderLockChar')
next(child for child in character_layer.layer_collection.children if child.collection is mapping).exclude = True

def render(name):
    path = out / (name + '.png')
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    return pixels

baseline = render('baseline')
alignment.location = (.25, 0, 0)
alignment.rotation_euler.z = math.pi/2
map_result = dict(status='appended',library_sha256=maps.MAP_BLEND_SHA256)
report = build_map_proxy(scene, map_result)
proxy = bpy.data.collections['RenderLockProxy']
maps.validate_map_proxy_completion(report, map_result)
assert mapping.hide_viewport and not mapping.hide_render and proxy.hide_render
assert len(proxy.objects) == 2 and all(o.hide_render for o in proxy.objects)
assert not building.visible_get() and all(o.visible_get() for o in proxy.objects)
assert next(child for child in character_layer.layer_collection.children if child.collection is proxy).exclude
assert not next(child for child in bpy.context.view_layer.layer_collection.children if child.collection is proxy).exclude

# The alignment transform was set immediately before building. Every original
# cube vertex should be at its expected rounded world coordinate in the proxy.
parent = Matrix.Translation(Vector((.25, 0, 0))) @ Matrix.Rotation(math.pi/2,4,'Z')
for source in (caster, building):
    cell = source.name.split('__')[0]
    approx = bpy.data.objects['RenderLock proxy | ' + cell]
    proxy_vertices = {tuple(round(float(x), 5) for x in vertex.co) for vertex in approx.data.vertices}
    local = Matrix.Translation(source.location)
    for vertex in source.data.vertices:
        point = parent @ local @ vertex.co
        rounded = tuple(round(round(float(x)/.25)*.25, 5) for x in point)
        assert rounded in proxy_vertices, (source.name, rounded)

after = render('proxy-switch')
assert np.array_equal(baseline, after)
caster.hide_render = True
without_caster = render('without-offscreen-caster')
caster.hide_render = False
assert np.max(np.abs(without_caster-baseline)) > .01
proxy.hide_render = False
for obj in proxy.objects:
    obj.hide_render = False
with_proxy = render('proxy-render-control')
assert np.max(np.abs(with_proxy-baseline)) > .01
proxy.hide_render = True
for obj in proxy.objects:
    obj.hide_render = True

file = out / 'production-proxy-fixture.blend'
assert not file.exists()
bpy.ops.wm.save_as_mainfile(filepath=str(file))
bpy.ops.wm.open_mainfile(filepath=str(file), load_ui=False)
scene = bpy.context.scene
mapping = bpy.data.collections['RenderLockMap']
proxy = bpy.data.collections['RenderLockProxy']
assert mapping.hide_viewport and not mapping.hide_render and proxy.hide_render
assert all(obj.hide_render for obj in proxy.objects)
layer = scene.view_layers['RenderLockChar']
assert next(child for child in layer.layer_collection.children if child.collection is proxy).exclude
maps.validate_map_proxy_completion(json.loads(scene['renderlock_map_proxy']), map_result)
reopened = render('reopened')
assert np.array_equal(baseline, reopened)
result = {'status':'PASS','proxy':report, 'render_delta':float(np.max(np.abs(after-baseline))),
          'reopened_delta':float(np.max(np.abs(reopened-baseline))),
          'caster_control_delta':float(np.max(np.abs(without_caster-baseline))),
          'proxy_control_delta':float(np.max(np.abs(with_proxy-baseline)))}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result),flush=True)
