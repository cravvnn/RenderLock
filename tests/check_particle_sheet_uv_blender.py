"""Actual sheet/ordinary UV pixels, saved reopen and whole-package rollback.

Run headless Blender --python this_file -- <fresh-output-dir>, then reopen
sheet-uv.blend with -- <output-dir> --reopen. --legacy-control deliberately
omits the proof and must fail the new sheet pixel expectation. Synthetic only;
installed shader identity and fresh normal export are separate qualifications.
"""
import sys,copy,hashlib,json
from pathlib import Path
import bpy,numpy as np
from bpy_extras.object_utils import world_to_camera_view
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.simulated_effect_package import build_package_effects,scene_identity
from agr.particle_sheet_uv import SHADERS,POLICY
args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);reopen='--reopen'in args;legacy='--legacy-control'in args
groups=('objects','collections','meshes','shape_keys','materials','lights','images','actions','node_groups','texts')
def inventory():return {k:set(getattr(bpy.data,k))for k in groups}
if not reopen:
 out.mkdir(parents=True,exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene;scene.render.fps=64
 camera=bpy.data.objects.new('Pixel camera',bpy.data.cameras.new('Pixel camera'));scene.collection.objects.link(camera)
 camera.location=(0,0,10);camera.data.type='ORTHO';camera.data.ortho_scale=8;scene.camera=camera
 image=bpy.data.images.new('Alpha block',width=16,height=16,alpha=True)
 pixels=np.ones((16,16,4),dtype=np.float32);pixels[:,:,3]=0;pixels[4:12,4:12,3]=1
 image.pixels.foreach_set(pixels.ravel());image.filepath_raw=str(out/'block.png');image.file_format='PNG';image.save();bpy.data.images.remove(image)
 identity={}
 for field in ('model_file','skeleton_file','pose_file'):
  file=out/field;file.write_text('synthetic '+field);identity[field]=str(file)
 data=dict(start_tick=1,end_tick=1,tick_rate=64,entities=[dict(id='actor',samples=[],**identity)])
 # Pick a deterministic fixture identity with a displacement out of the white
 # centre. Constant mesh UVs make each rendered plane a direct material sample.
 for i in range(100):
  iid=f'fixture{i}';digest=hashlib.sha256(f'{iid}/0/0/0'.encode()).digest()
  offset=tuple(int.from_bytes(digest[j:j+4],'little')/2**32 for j in (0,4))
  uv=tuple((.5-v)%1 for v in offset)
  if any(v<.15 or v>.85 for v in uv):break
 else:raise AssertionError('No discriminating fixture UV')
 definitions={name:dict(m_Renderers=[dict(_class='C_OP_RenderSprites',
  m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',
  m_vecTexturesInput=[dict(m_hTexture='materials/'+name+'.vtex',m_TextureControls=dict(m_bRandomizeOffsets=True))])])for name in ('sheet','ordinary')}
 systems=[]
 for name,path,x in [('sheet','root',-60),('ordinary','root/0',60)]:
  particle=dict(id=0,position=[x,0,0],previous=[x,0,0],normal=[0,0,1],rotation=[0,0,0],
   color=[1,1,1],radius=20,alpha=1,alpha_alternate=1,sequence=0,age=0,trail=1)
  systems.append(dict(name=name+'_c',system_path=path,step=1/64,particles=[particle]))
 simulation=dict(schema_version=1,backend='synthetic sheet graph',start_tick=1,end_tick=1,tick_rate=64,
  instances=[dict(id=iid,entity_id='actor',birth=1,frames=[dict(tick=1,systems=systems)])])
 textures={'materials/'+name+'.vtex':dict(schema_version=1,status='supported',is_sprite_sheet=name=='sheet',atlas=name=='sheet',
  directory=str(out),sequences={'0':dict(status='supported',frames=[dict(image='block.png')])})for name in ('sheet','ordinary')}
 provenance={}if legacy else dict(spritecard_sheet_uv=dict(policy=POLICY,sha256=dict(SHADERS)))
 package=dict(schema_version=1,backend='simulated_states_v1',entity_ids=['actor'],scene_identity=scene_identity(data,['actor']),
  coverage_scope='Synthetic UV branch plumbing only',simulation=simulation,assets=dict(definitions=definitions,textures=textures,provenance=provenance))
 before=inventory();bad=copy.deepcopy(package)
 bad['assets']['definitions']['ordinary']['m_Renderers'][0]['m_flAlphaScale']=dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT',m_nControlPoint=5,m_nVectorComponent=0)
 try:build_package_effects(bad,data,out/'package.json',scene,camera)
 except ValueError as error:assert 'snapshot'in str(error),str(error)
 else:raise AssertionError('Missing CP accepted')
 assert inventory()==before,'Late ordinary sibling failure leaked sheet IDs'
 report=build_package_effects(package,data,out/'package.json',scene,camera)
 if not legacy:
  assert report['sheet_uv_policy']==POLICY and report['sheet_uv_textures']==['materials/sheet.vtex']
 assert any('ordinary'in value and 'substitute'in value for value in report['omissions'])
 assert any('sheet'in value and 'substitute'in value for value in report['omissions'])is legacy
 for obj in scene.objects:
  if obj.get('agr_particle_instance')==iid:
   for loop in obj.data.uv_layers.active.data:loop.uv=uv
 scene['sheet_fixture_instance']=iid;scene['sheet_fixture_legacy']=legacy
 scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
 scene.render.threads_mode='FIXED';scene.render.threads=2
 scene.render.resolution_x=32;scene.render.resolution_y=16;scene.render.resolution_percentage=100;scene.render.film_transparent=True
 scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='32'
 scene.frame_set(1);bpy.ops.wm.save_as_mainfile(filepath=str(out/'sheet-uv.blend'))
else:
 scene=bpy.context.scene;iid=scene['sheet_fixture_instance'];legacy=bool(scene['sheet_fixture_legacy'])
assert not bpy.data.libraries and all(i.packed_file for i in bpy.data.images if i.type=='IMAGE')
scene.render.filepath=str(out/('reopen.exr'if reopen else 'pixels.exr'));bpy.ops.render.render(write_still=True)
image=bpy.data.images.load(scene.render.filepath,check_existing=False);pixels=np.asarray(image.pixels[:]).reshape(16,32,4);values=[]
for path in ('root','root/0'):
 obj=next(o for o in scene.objects if o.get('agr_particle_instance')==iid and o.get('agr_particle_system_path')==path)
 point=world_to_camera_view(scene,scene.camera,obj.matrix_world.translation);values.append(float(pixels[int(point.y*16),int(point.x*32),3]))
bpy.data.images.remove(image)
np.testing.assert_allclose(values,[0,1],atol=2e-6,rtol=0)
(out/('reopen.json'if reopen else 'result.json')).write_text(json.dumps(dict(status='PASS_ACTUAL_SHEET_UV_PIXELS',
 sheet_alpha=values[0],ordinary_alpha=values[1],reopened=reopen,late_rollback=not reopen),indent=2))
