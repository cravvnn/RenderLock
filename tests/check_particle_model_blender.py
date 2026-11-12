"""Actual bpy morph-key regression, including a failing ordinary-insertion control.

Run Blender background/factory with --python this_file -- <fresh-output-dir>.
Reopen its .blend and run again with -- <same-output-dir> --reopen.
No game resources, mocks, native UI or render device are needed.
"""
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_particle_models import build_morph_curves
from agr.blender_effects import _linear

args=sys.argv[sys.argv.index('--')+1:]
output=Path(args[0]).resolve()
reopen='--reopen' in args
schedule=[(18044.,np.array([0.])),(18044.997314,np.array([.997314])),
          (18045.,np.array([1.])),(18046.,np.array([0.]))]
start=17920
if not reopen:
    output.mkdir(parents=True,exist_ok=False)
    for fps in (24,30,60):
        for ordinary in (False,True):
            mesh=bpy.data.meshes.new('Morph regression')
            mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
            obj=bpy.data.objects.new(f'Morph {fps} ordinary={ordinary}',mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj['fps']=fps;obj['ordinary_insertion']=ordinary
            obj.shape_key_add(name='Basis');key=obj.shape_key_add(name='Wing')
            key.data[1].co.z=1
            if ordinary:
                for tick,weights in schedule:
                    key.value=float(weights[0]);key.keyframe_insert('value',frame=1+(tick-start)*fps/64)
                _linear(mesh.shape_keys)
            else:
                build_morph_curves(mesh.shape_keys,[key],schedule,start,fps/64)

results=[]
for obj in [o for o in bpy.context.scene.objects if 'ordinary_insertion' in o]:
    fps=obj['fps'];ordinary=obj['ordinary_insertion'];worst=0.
    for tick in list(np.linspace(18044,18046,129))+[18044.997314,18045.]:
        frame=1+(float(tick)-start)*fps/64
        bpy.context.scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
        value=obj.data.shape_keys.key_blocks['Wing'].value
        expected=float(np.interp(tick,[t for t,_ in schedule],[w[0] for _,w in schedule]))
        worst=max(worst,abs(value-expected))
    if ordinary:assert worst>1e-4,(fps,worst)
    else:assert worst<1e-5,(fps,worst)
    results.append(dict(fps=fps,ordinary_insertion=bool(ordinary),max_weight_error=worst))
if not reopen:
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'morph-key-regression.blend'))
(output/('reopen.json' if reopen else 'result.json')).write_text(json.dumps(
    dict(status='PASS',actual_bpy=True,reopened=reopen,checks=results),indent=2))
