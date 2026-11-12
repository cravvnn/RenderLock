"""Synthetic atlas pixels, replay-sample holds, package rollback and saved reopen.

Background Blender --python this_file -- <fresh-output-dir> [--reopen].
Run build then a separate reopen process. No private assets or game access.
This component check does not establish a real replay's creation providers.
"""
from pathlib import Path
import copy,json,math,sys
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_effects import _channels,_linear
from agr.particle_texture_animation import build_sheet_nodes
from agr.simulated_effect_package import build_package_effects,scene_identity
from agr.asset_converter import sha

args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]).resolve();reopen='--reopen'in args
phases=(.125,.625,1.125);uv=(.2,.7)
sheet=dict(schema_version=1,policy='agr_source_particle_sheet_v1',input_sha256='a'*64,
 sequences=[dict(index=0,source_id=17,clamp=False,total_time=2.,frames=[
 dict(duration=1.,min=[.1,.1],max=[.4,.4]),dict(duration=1.,min=[.5,.5],max=[.9,.9])])])
groups=('objects','collections','meshes','shape_keys','materials','lights','images','actions','node_groups','texts')
def inventory():return {name:set(getattr(bpy.data,name))for name in groups}
def action_points(action):return [(f.data_path,f.array_index,tuple((tuple(p.co),p.interpolation)for p in f.keyframe_points))for f in _channels(action)]

if not reopen:
 out.mkdir(parents=True,exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
 image=bpy.data.images.new('analytic atlas',width=32,height=16,alpha=True,float_buffer=True);image.colorspace_settings.name='Non-Color'
 pixels=[]
 for y in range(16):
  for x in range(32):
   u,v=(x+.5)/32,(y+.5)/16;pixels.extend((u,v,.2,.25+.5*u))
 image.pixels.foreach_set(pixels);image.pack()
 mesh=bpy.data.meshes.new('pixel plane');mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],[],[(0,1,2,3)])
 obj=bpy.data.objects.new('pixel plane',mesh);scene.collection.objects.link(obj)
 fixture_materials=[]
 for fps in (24,30,60):
  for clamp in (False,True):
   for blend in (False,True):
    mat=bpy.data.materials.new(f'sheet {fps} {int(clamp)} {int(blend)}');mat.use_nodes=True;mat.use_fake_user=True
    nodes=mat.node_tree.nodes;nodes.clear();links=mat.node_tree.links
    coordinate=nodes.new('ShaderNodeCombineXYZ');coordinate.inputs[0].default_value=uv[0];coordinate.inputs[1].default_value=uv[1]
    descriptor=copy.deepcopy(sheet);descriptor['sequences'][0]['clamp']=clamp
    samples=[(100+i,1/64,dict(manual_frame=phase))for i,phase in enumerate(phases)];constant=set()
    animated,_=build_sheet_nodes(nodes,links,image,coordinate.outputs[0],descriptor,0,
       dict(m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES',m_bBlendFramesSeq0=blend),samples,lambda t:1+(t-100)*fps/64,constant)
    rgb=nodes.new('NodeReroute');rgb.name='Sheet RGB';links.new(animated.outputs['Color'],rgb.inputs[0])
    alpha=nodes.new('NodeReroute');alpha.name='Sheet alpha';links.new(animated.outputs['Alpha'],alpha.inputs[0])
    emission=nodes.new('ShaderNodeEmission');emission.name='Pixel probe';emission.inputs['Strength'].default_value=1;links.new(rgb.outputs[0],emission.inputs['Color'])
    output=nodes.new('ShaderNodeOutputMaterial');links.new(emission.outputs[0],output.inputs['Surface']);_linear(mat.node_tree)
    for curve in _channels(mat.node_tree.animation_data.action):
     if curve.data_path in constant:
      for point in curve.keyframe_points:point.interpolation='CONSTANT'
    mat['constant_paths']=json.dumps(sorted(constant));mat['fps']=fps;mat['clamp']=clamp;mat['blend']=blend
    mat['first_sampler']=animated.current_sampler.name
    if blend:
     mat['second_sampler']=animated.next_sampler.name;mat['blend_node']=animated.blend_fraction.node.name
    fixture_materials.append(mat)
 camera=bpy.data.objects.new('pixel camera',bpy.data.cameras.new('pixel camera'));scene.collection.objects.link(camera);scene.camera=camera
 camera.location=(0,0,5);camera.data.type='ORTHO';camera.data.ortho_scale=.1
 scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
 scene.render.threads_mode='FIXED';scene.render.threads=2
 scene.render.resolution_x=1;scene.render.resolution_y=1;scene.render.resolution_percentage=100
 scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='32'
 # Exercise the real package transaction, including a failure after atlas keys.
 input_image=bpy.data.images.new('package atlas input',width=4,height=4,alpha=True);input_image.pixels.foreach_set([1.]*64)
 input_image.filepath_raw=str(out/'package.png');input_image.file_format='PNG';input_image.save();bpy.data.images.remove(input_image)
 identity={}
 for field in('model_file','skeleton_file','pose_file'):
  p=out/field;p.write_text('synthetic identity '+field);identity[field]=str(p)
 data=dict(start_tick=100,end_tick=102,tick_rate=64,entities=[dict(id='actor',samples=[],**identity)])
 frames=[]
 for i,phase in enumerate(phases):
  p=dict(id=0,position=[0,0,0],previous=[0,0,0],normal=[0,0,1],rotation=[0,0,0],color=[1,1,1],radius=20.,alpha=1.,alpha_alternate=1.,sequence=0,age=i/64,life=1.,trail=1.,manual_frame=phase)
  frames.append(dict(tick=100+i,systems=[dict(name='particles/fixture.vpcf_c',system_path='root',step=1/64,system_age=i/64,control_points=[dict(index=0,position=[0,0,0])],particles=[p])]))
 descriptor=copy.deepcopy(sheet);descriptor.update(directory=str(out),image='package.png',image_sha256=sha(out/'package.png'),width=4,height=4)
 texture=dict(schema_version=1,resource='materials/fixture.vtex_c',directory=str(out),is_sprite_sheet=True,atlas=True,sequences={'0':dict(status='unsupported',frames=[dict(image='package.png'),dict(image='package.png')])},source_sheet=descriptor)
 renderer=dict(_class='C_OP_RenderSprites',m_nAnimationType='ANIMATION_TYPE_MANUAL_FRAMES',m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',m_vecTexturesInput=[dict(m_hTexture='materials/fixture.vtex')])
 simulation=dict(schema_version=1,backend='synthetic atlas plumbing',control_point_snapshot_policy='agr_visible_control_points_v1',start_tick=100,end_tick=102,tick_rate=64,instances=[dict(id='fixture',entity_id='actor',birth=100,control_point_indices=[0],materialized_control_point_indices=[0],frames=frames)])
 package=dict(schema_version=1,backend='simulated_states_v1',entity_ids=['actor'],scene_identity=scene_identity(data,['actor']),coverage_scope='Synthetic atlas transaction only',simulation=simulation,assets=dict(definitions={'particles/fixture.vpcf':dict(m_nBehaviorVersion=12,m_Renderers=[renderer])},textures={'materials/fixture.vtex':texture},provenance=dict(input_sha256={'materials/fixture.vtex':'a'*64})))
 before=inventory();actions={a.name:action_points(a)for a in bpy.data.actions};bad=copy.deepcopy(package);bad['simulation']['instances'][0]['frames'][1]['systems'][0]['particles'][0]['manual_frame']=None
 import agr.particle_texture_animation as animation
 real=animation.sheet_frame;late=[]
 def observed_failure(sheet,sequence,particle,renderer):
  if particle.get('manual_frame')is None:
   assert set(bpy.data.actions)-before['actions'];late.append(True)
  return real(sheet,sequence,particle,renderer)
 animation.sheet_frame=observed_failure
 try:
  try:build_package_effects(bad,data,out/'package.json',scene,camera)
  except ValueError as error:
   assert late,('Failure preceded intended atlas key allocation',str(error))
  else:raise AssertionError('Invalid manual frame accepted')
 finally:animation.sheet_frame=real
 assert late and inventory()==before and actions=={a.name:action_points(a)for a in bpy.data.actions},'Atlas rollback changed existing IDs/Actions'
 report=build_package_effects(package,data,out/'package.json',scene,camera)
 assert report['objects']==1 and report['texture_animation_policy']=='agr_source_particle_sheet_v1'
 for o in set(bpy.data.objects)-before['objects']:o.hide_render=True
 scene['atlas_package_rollback_verified']=True;scene['atlas_fixture_scope']=__doc__
 for im in bpy.data.images:
  if im.source=='FILE':im.pack()
 bpy.ops.wm.save_as_mainfile(filepath=str(out/'sheet-animation.blend'))
else:
 bpy.ops.wm.open_mainfile(filepath=str(out/'sheet-animation.blend'),load_ui=False,use_scripts=False)
 scene=bpy.context.scene;obj=bpy.data.objects['pixel plane']

assert scene['atlas_package_rollback_verified'] and not bpy.data.libraries
assert all(im.packed_file for im in bpy.data.images if im.source=='FILE')
results=[];holds=0
for mat in [m for m in bpy.data.materials if 'constant_paths'in m]:
 fps=int(mat['fps']);scene.render.fps=fps;scene.render.fps_base=1.
 # An unused fake-user material is outside the scene's evaluated dependency
 # graph. Bind it before querying animated RNA, including the non-rendered FPSs.
 obj.data.materials.clear();obj.data.materials.append(mat)
 paths=set(json.loads(mat['constant_paths']))
 for curve in _channels(mat.node_tree.animation_data.action):
  if curve.data_path in paths:assert all(p.interpolation=='CONSTANT'for p in curve.keyframe_points)
 for i,phase in enumerate(phases):
  frame=1+(i+.5)*fps/64;scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame));scene.view_layers[0].update()
  clamp=bool(mat['clamp']);blend=bool(mat['blend'])
  position=2*(max(0.,min(1.,phase))if clamp else phase-math.floor(phase))
  first,last=(0,1)if position<1 else(1,1)if clamp else(1,0)
  fraction=position if position<1 else 0. if clamp else position-1
  # Independent analytical gradient texture and authored rectangles. No calls
  # to sheet_frame/blender_rect are used to construct the pixel expectation.
  def sample(index):
   low,high=(([.1,.1],[.4,.4])if index==0 else([.5,.5],[.9,.9]))
   u=low[0]+(high[0]-low[0])*uv[0];v=1-high[1]+(high[1]-low[1])*uv[1]
   return np.array([u,v,.2]),.25+.5*u
  # Check evaluated RNA values at every FPS, including the fractional hold.
  for key,index in [('first_sampler',first)]+([('second_sampler',last)]if blend else[]):
   low,high=(([.1,.1],[.4,.4])if index==0 else([.5,.5],[.9,.9]))
   sampler=mat.node_tree.nodes[mat[key]];offset=sampler.inputs['Vector'].links[0].from_node
   scale=offset.inputs[0].links[0].from_node
   assert np.allclose(offset.inputs[1].default_value,[low[0],1-high[1],0.],atol=2e-6,rtol=0),(mat.name,i,key,tuple(offset.inputs[1].default_value),low,high)
   assert np.allclose(scale.inputs[1].default_value,[high[0]-low[0],high[1]-low[1],1.],atol=2e-6,rtol=0),(mat.name,i,key,tuple(scale.inputs[1].default_value),low,high)
  if blend:assert abs(mat.node_tree.nodes[mat['blend_node']].outputs[0].default_value-fraction)<2e-6
  holds+=1
  if fps!=60:continue
  a,aa=sample(first);b,ba=sample(last);t=fraction if blend else 0.
  rgb=(1-t)*a+t*b;alpha=(1-t)*aa+t*ba
  assert np.max(abs(rgb-np.array([*uv,.2])))>.05,'Fixture would not detect an unanimated full-atlas fallback'
  obj.data.materials.clear();obj.data.materials.append(mat);nodes=mat.node_tree.nodes;links=mat.node_tree.links
  for mode,expected in(('color',rgb),('alpha',np.repeat(alpha,3))):
   links.new(nodes['Sheet RGB'if mode=='color'else'Sheet alpha'].outputs[0],nodes['Pixel probe'].inputs['Color'])
   path=out/f'{"reopen"if reopen else"build"}-{int(clamp)}-{int(blend)}-{i}-{mode}.exr';scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
   image=bpy.data.images.load(str(path),check_existing=False);actual=np.asarray(image.pixels[:3]);bpy.data.images.remove(image)
   error=float(np.max(abs(actual-expected)));assert error<4e-6,(mat.name,i,mode,actual,expected,error)
   results.append(dict(material=mat.name,phase=phase,mode=mode,expected=expected.tolist(),actual=actual.tolist(),error=error))
assert len(results)==24 and holds==36
result=dict(status='PASS',phase='reopen'if reopen else'build',pixel_checks=results,held_sample_checks=holds,package_late_key_rollback=True,packed_images=True,scope=__doc__)
(out/f'{"reopen"if reopen else"build"}-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
