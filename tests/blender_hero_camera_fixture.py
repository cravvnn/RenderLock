"""Background Blender regression: hero framing handles blocked visibility results.

blender -b --factory-startup --disable-autoexec --python-exit-code 1 \
    --python tests/blender_hero_camera_fixture.py -- --output /path/to/result.json
"""
import argparse
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_shot import frame_actor

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=3
root=bpy.data.objects.new('Actor root',None);scene.collection.objects.link(root)
bpy.ops.mesh.primitive_cube_add(size=1)
body=bpy.context.object;body.name='Actor body';body.parent=root
bpy.ops.mesh.primitive_cube_add(size=1.6)
bpy.context.object.name='Closed surrounding scenery'
camera=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'))
scene.collection.objects.link(camera);scene.camera=camera
scene.render.resolution_x=160;scene.render.resolution_y=100
bpy.context.view_layer.update()
shot=frame_actor(scene,root)
assert shot['frames']==3 and shot['minimum_sampled_visibility']==0,shot
assert camera.animation_data and camera.animation_data.action
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(dict(status='PASS',shot=shot,
    blender_version=list(bpy.app.version)),indent=2)+'\n',encoding='utf-8')
print('PASS hero camera: blocked rays are handled without losing animation keys')
