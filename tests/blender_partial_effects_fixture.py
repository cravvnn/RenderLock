"""Background Blender: explicit warning consent, package identity and saved output."""
import hashlib
import copy
import json
import os
from pathlib import Path
import sys

import bpy

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.particle_diagnostics import CAPABILITY_WARNING
from agr.simulated_effect_package import scene_identity,build_package_effects

out=Path(os.environ['RENDERLOCK_EFFECTS_TEST_DIR']);out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene;scene.render.fps=64
camera=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));scene.collection.objects.link(camera)
camera.location=(0,-10,0);scene.camera=camera
files={}
for field in ('model_file','skeleton_file','pose_file'):
    path=out/field;path.write_bytes(('synthetic '+field).encode());files[field]=str(path)
data=dict(start_tick=100,end_tick=102,tick_rate=64,
          entities=[dict(id='17:3',hero_id=10,samples=[dict(tick=100,position=[0,0,0])],**files)])
particle=dict(id=0,position=[1,2,3],previous=[1,2,3],radius=1,alpha=1,age=0,trail=1,
              alpha_alternate=1,color=[1,1,1],sequence=0,normal=[0,0,1],rotation=[0,0,0])
simulation=dict(schema_version=1,backend='synthetic warning-gate fixture',warnings=['23:2: '+CAPABILITY_WARNING],
    start_tick=100,end_tick=102,tick_rate=64,instances=[dict(id='23:2',entity_id='17:3',birth=100,
        control_point_indices=[0],materialized_control_point_indices=[0],
        frames=[dict(tick=t,systems=[dict(name='fixture.vpcf_c',system_path='root',step=1/64,
          particles=[dict(particle,age=(t-100)/64)])]) for t in range(100,103)])])
package=dict(schema_version=1,backend='simulated_states_v1',entity_ids=['17:3'],
             coverage_scope='Synthetic partial-effects acceptance fixture; not a replay fidelity claim',
             scene_identity=scene_identity(data,['17:3']),simulation=simulation,
             assets=dict(definitions={'fixture.vpcf':dict(m_Renderers=[dict(_class='C_OP_RenderSprites',
                 m_nOutputBlendMode='PARTICLE_OUTPUT_BLEND_MODE_ADD',
                 m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',m_vecTexturesInput=[])])},textures={}))
before=set(bpy.data.objects)
try:
    build_package_effects(package,data,out/'package.json',scene,camera)
    raise AssertionError('strict warning gate did not reject')
except ValueError as exc:
    assert 'unresolved diagnostics' in str(exc)
assert set(bpy.data.objects)==before
simulation['instances'][0]['frames'][1]['systems'][0]['particles'][0]['position']=[float('nan'),2,3]
try:
    build_package_effects(package,data,out/'package.json',scene,camera,allow_partial_effects=True)
    raise AssertionError('partial consent must not render non-finite states')
except ValueError:
    pass
assert set(bpy.data.objects)==before
simulation['instances'][0]['frames'][1]['systems'][0]['particles'][0]['position']=[1,2,3]
result=build_package_effects(package,data,out/'package.json',scene,camera,allow_partial_effects=True)
assert result['status']=='partial' and result['objects']==1
assert result['simulator_diagnostics']['warnings']==simulation['warnings']
assert simulation['warnings']==['23:2: '+CAPABILITY_WARNING], 'original diagnostics were erased'
empty=copy.deepcopy(package)
for frame in empty['simulation']['instances'][0]['frames']:
    frame['systems'][0]['particles']=[]
empty_result=build_package_effects(empty,data,out/'empty-package.json',scene,camera,allow_partial_effects=True)
assert empty_result['objects']==0 and empty_result['particle_tracks']==0
scene.frame_set(2)
objects=[obj for obj in scene.objects if obj.get('agr_particle_instance')=='23:2']
assert len(objects)==1 and not objects[0].hide_render
scene['fixture_effects_result']=json.dumps(result)
target=out/'partial-effects.blend';bpy.ops.wm.save_as_mainfile(filepath=str(target))
bpy.ops.wm.open_mainfile(filepath=str(target),load_ui=False)
saved=json.loads(bpy.context.scene['fixture_effects_result'])
assert saved['simulator_diagnostics']['omitted_operator_classes']==['C_OP_SetControlPointFieldToScalarExpression']
assert len([obj for obj in bpy.context.scene.objects if obj.get('agr_particle_instance')=='23:2'])==1
(out/'result.json').write_text(json.dumps(dict(status='PASS',rendering=saved,
    strict_gate_retained=True,original_diagnostics_retained=True,saved_and_reopened=True,
    limit='Synthetic states and material; no claim about Spooky replay, actual scalar-expression semantics or native Linux.'),indent=2)+'\n')
print('PASS: explicit partial-effects package renders and saves, strict mode rejects it')
