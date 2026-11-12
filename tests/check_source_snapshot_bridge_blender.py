"""Asset-free Source-rest pose bridge; no native selection/effect admission.

Blender --background --python this_file -- <fresh-directory> [--reopen].
The synthetic authored rest deliberately differs from Blender's imported rest.
"""
from pathlib import Path
import sys,json,copy,math
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_effects import _linear
from agr.blender_particle_skinning import capture_source_snapshot_provider,validate_source_snapshot_rig,rigid_snapshot_matrix
from agr.blender_particle_model import capture_model_bones
from agr.particle_snapshot_provider import RESOURCE
args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);reopen='--reopen'in args;unit=.0254
if not reopen:
 out.mkdir(parents=True,exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
 root=bpy.data.objects.new('replay root',None);scene.collection.objects.link(root);root['agr_entity_id']='3:1'
 rig=bpy.data.objects.new('owner rig',bpy.data.armatures.new('owner rig'));scene.collection.objects.link(rig);rig.parent=root;rig['agr_entity_id']='3:1'
 bpy.context.view_layer.objects.active=rig;rig.select_set(True);bpy.ops.object.mode_set(mode='EDIT')
 for name,head,tail in [('root',(0,0,0),(0,unit,0)),('crossbow',(10*unit,0,0),(10*unit,unit,0)),('cloth',(0,2*unit,0),(0,3*unit,0))]:
  bone=rig.data.edit_bones.new(name);bone.head=head;bone.tail=tail
  if name!='root':bone.parent=rig.data.edit_bones['root']
 bpy.ops.object.mode_set(mode='OBJECT')
 for name in('model.bin','skeleton.bin'):(out/name).write_bytes(name.encode())
 np.savez(out/'poses.npz',ticks=np.array([10,11,12]))
 model=dict(schema_version=1,resource='models/heroes_wip/priest/priest.vmdl_c',resource_sha256='a'*64,
  names=['root','crossbow','cloth'],parents=[-1,0,0],definitions=[])
 entity=dict(id='3:1',model_constraints=model,model_file=str(out/'model.bin'),skeleton_file=str(out/'skeleton.bin'),pose_file=str(out/'poses.npz'),
  samples=[dict(tick=t,position=[100+3*(t-10),0,0],body_angles=[0,0,0])for t in(10,11,12)])
 instance=dict(id='8:1',index=8,generation=1,entity_id='3:1',entity_handle=16387,resource=RESOURCE,created_tick=10,events=[
  dict(tick=10,type='CREATE',data=dict(create_particle=dict(entity_handle=16387,attach_type=3))),
  dict(tick=10,type='UPDATE_ENT',data=dict(update_particle_ent=dict(control_point=0,entity_handle=16387,attach_type=1,attachment=0)))])
 data=dict(start_tick=10,end_tick=12,tick_rate=64,entities=[entity],particle_instances=[instance],metadata=dict(demo_sha256='b'*64))
 inventory=dict(schema_version=1,policy='agr_authored_model_rest_v1',model=model['resource'].removesuffix('_c'),source_sha256='a'*64,
  names=model['names'],parents=model['parents'],parent_transforms=[[0,0,0,1,0,0,0,1],[5,0,0,1,0,0,0,1],[0,2,0,1,0,0,0,1]])
else:
 bpy.ops.wm.open_mainfile(filepath=str(out/'bridge.blend'),load_ui=False,use_scripts=False);scene=bpy.context.scene;root=bpy.data.objects['replay root'];rig=bpy.data.objects['owner rig']
 saved=json.loads((out/'inputs.json').read_text());data,inventory=saved['data'],saved['inventory'];entity=data['entities'][0];instance=data['particle_instances'][0];model=entity['model_constraints']
rows=[]
for fps in(24,30,60):
 scene.render.fps=fps;scene.render.fps_base=1;root.animation_data_clear();rig.animation_data_clear()
 rig.pose.bones['cloth'].scale=(1,1,1)
 for d in(0,1,2):
  frame=1+d*fps/64;root.location=(unit*(100+3*d),0,0);root.keyframe_insert('location',frame=frame)
  rig.pose.bones['crossbow'].location=(0,2*d*unit,0);rig.pose.bones['crossbow'].keyframe_insert('location',frame=frame)
 _linear(root);_linear(rig);scene.frame_set(12,subframe=.25);original=(scene.frame_current,scene.frame_subframe)
 kwargs=dict(entity_id='3:1',start_tick=10,end_tick=12,scene_start_tick=10,tick_rate=64,available_ticks={10,11,12})
 full=capture_model_bones(rig,model,scene,**kwargs);selected=capture_model_bones(rig,model,scene,required_names=['crossbow'],**kwargs)
 assert np.array_equal(full['transforms'][:,1],selected['transforms'][:,1])and np.isnan(selected['transforms'][:,[0,2]]).all()
 assert (scene.frame_current,scene.frame_subframe)==original
 # Unselected nonuniform cloth does not become a fabricated rigid transform.
 rig.pose.bones['cloth'].scale=(2,1,1)
 try:capture_model_bones(rig,model,scene,**kwargs)
 except ValueError as error:assert 'uniform'in str(error)
 else:raise AssertionError('All-bone capture accepted nonuniform cloth')
 assert(scene.frame_current,scene.frame_subframe)==original
 provider=capture_source_snapshot_provider(data,entity,dict(rig=rig,root=root),scene,instance,out,inventory)
 assert(scene.frame_current,scene.frame_subframe)==original
 for d,frame in enumerate(provider['frames']):
  actual=np.array(frame['snapshot_model_bones'][0]['matrix']).reshape(3,4);expected=np.column_stack((np.eye(3),[105+3*d,2*d,0]))
  assert np.allclose(actual,expected,atol=3e-5),actual
 scene.frame_set(1);legacy=np.array(rigid_snapshot_matrix(rig,'crossbow'));assert abs(legacy[0,3]-100)<3e-5
 assert abs(provider['frames'][0]['snapshot_model_bones'][0]['matrix'][3]-legacy[0,3])>4.9
 scene.frame_set(*original[:1],subframe=original[1]);validate_source_snapshot_rig(provider,data,scene,out)
 delta=rig.delta_location.copy();rig.delta_location.x+=unit
 try:
  try:validate_source_snapshot_rig(provider,data,scene,out)
  except ValueError as error:assert 'motion changed'in str(error)
  else:raise AssertionError('Edited rig motion accepted')
 finally:rig.delta_location=delta
 assert(scene.frame_current,scene.frame_subframe)==original
 missing=copy.deepcopy(data);missing['entities'][0]['samples'].pop(1)
 try:capture_source_snapshot_provider(missing,missing['entities'][0],dict(rig=rig,root=root),scene,instance,out,inventory)
 except ValueError as error:assert 'history'in str(error)
 else:raise AssertionError('Missing owner pose history accepted')
 assert(scene.frame_current,scene.frame_subframe)==original
 rows.append(dict(fps=fps,source_matrices=3,changed_motion_refused=True,missing_history_refused=True,frame_restoration=True,legacy_full_capture_strict=True))
if not reopen:
 (out/'inputs.json').write_text(json.dumps(dict(data=data,inventory=inventory),indent=2));bpy.ops.wm.save_as_mainfile(filepath=str(out/'bridge.blend'))
result=dict(status='PASS_SOURCE_REST_BRIDGE_COMPONENT',phase='reopen'if reopen else'build',rows=rows,scope=__doc__)
(out/(result['phase']+'-result.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result))
