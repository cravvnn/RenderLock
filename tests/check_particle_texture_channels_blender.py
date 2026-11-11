"""Actual CPU Cycles package regression; run with Blender in background mode.

Pass --source-root and a new --output directory after Blender's -- separator.
Uses synthetic assets only; does not require game files or a GPU.
"""
import sys,json,copy,argparse
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--source-root',required=True);parser.add_argument('--output',required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);sys.path.insert(0,args.source_root)
OUT=Path(args.output);OUT.mkdir(parents=True,exist_ok=False)
import bpy,numpy as np
from mathutils import Vector
from agr.simulated_effect_package import scene_identity,build_package_effects
from agr.installed_effects import UnsupportedEffect
bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene;scene.render.fps=64
camera=bpy.data.objects.new('Owned camera',bpy.data.cameras.new('Owned camera'));scene.collection.objects.link(camera);scene.camera=camera
camera.location=(0,-5,3);camera.rotation_euler=(-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.clip_start=.01;camera.data.clip_end=1000
im=bpy.data.images.new('Owned texture',width=8,height=8);im.pixels.foreach_set(np.ones(8*8*4,dtype='f4'));im.filepath_raw=str(OUT/'texture.png');im.file_format='PNG';im.save();bpy.data.images.remove(im)
files={}
for name in ('model_file','skeleton_file','pose_file'):
    p=OUT/name;p.write_bytes(('Owned synthetic identity: '+name).encode());files[name]=str(p)
data=dict(start_tick=64,end_tick=66,tick_rate=64,entities=[dict(id='actor',hero_id=1,samples=[dict(tick=64,position=[0,0,0])],**files)])
particle=dict(id=0,position=[0,0,0],previous=[0,0,0],normal=[0,0,1],rotation=[0,0,0],color=[1,1,1],radius=8.,alpha=1.,alpha_alternate=1.,age=.1,trail=1.,sequence=0)
simulation=dict(schema_version=1,backend='owned synthetic regression',warnings=[],start_tick=64,end_tick=66,tick_rate=64,
    instances=[dict(id='one',entity_id='actor',birth=64,frames=[dict(tick=t,systems=[dict(name='particles/owned.vpcf_c',system_path='root',step=1/64,particles=[copy.deepcopy(particle)])])for t in range(64,67)])])
assets=dict(textures={'materials/owned.vtex':dict(status='supported',directory=str(OUT),is_sprite_sheet=False,sequences={'0':dict(status='supported',frames=[dict(image='texture.png')])})},
    definitions={'particles/owned.vpcf':dict(m_Renderers=[dict(_class='C_OP_RenderSprites',m_flDepthBias=0.,m_vecTexturesInput=[dict(m_hTexture='materials/owned.vtex')])])})
package=dict(schema_version=1,backend='simulated_states_v1',entity_ids=['actor'],scene_identity=scene_identity(data,['actor']),coverage_scope='Owned synthetic sprite only',simulation=simulation,assets=assets)

scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=8;scene.cycles.use_denoising=False
scene.render.resolution_x=320;scene.render.resolution_y=180;scene.render.resolution_percentage=100;scene.render.film_transparent=True
scene.render.threads_mode='FIXED';scene.render.threads=4
im=bpy.data.images.new('Opaque black sample',width=8,height=8);pixels=np.zeros((8*8,4),dtype=np.float32);pixels[:,3]=1
im.pixels.foreach_set(pixels.reshape(-1));im.filepath_raw=str(OUT/'black.png');im.file_format='PNG';im.save();bpy.data.images.remove(im)
package['assets']['textures']['materials/owned.vtex']['sequences']['0']['frames'][0]['image']='black.png'
for f in package['simulation']['instances'][0]['frames']:f['systems'][0]['particles'][0]['radius']=60
initial=set(bpy.data.objects);rows=[]
for label,mode in(('rgba','SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA'),('rgb-alpha','SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA_RGBALPHA')):
 trial=copy.deepcopy(package);trial['assets']['definitions']['particles/owned.vpcf']['m_Renderers'][0]['m_vecTexturesInput'][0]['m_nTextureChannels']=mode
 before={k:set(getattr(bpy.data,k))for k in('objects','collections','meshes','materials','lights','images','actions','node_groups','texts')}
 report=build_package_effects(trial,data,OUT/'manifest.json',scene,camera);assert report['objects']==1
 scene.frame_set(2);scene.render.filepath=str(OUT/(label+'.png'));scene.render.image_settings.media_type='IMAGE';scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA';bpy.ops.render.render(write_still=True)
 image=bpy.data.images.load(scene.render.filepath,check_existing=False);a=np.asarray(image.pixels[:],dtype=np.float32).reshape(180,320,4);bpy.data.images.remove(image)
 alpha=float(a[90,160,3]);rows.append(dict(mode=mode,center_alpha=alpha))
 assert (alpha>.99 if label=='rgba'else alpha<.01),(label,alpha)
 bpy.data.batch_remove([v for k in before for v in set(getattr(bpy.data,k))-before[k]])
assert set(bpy.data.objects)==initial
trial=copy.deepcopy(package);trial['assets']['definitions']['particles/owned.vpcf']['m_Renderers'][0]['m_vecTexturesInput'][0]['m_nTextureChannels']='FUTURE_UNQUALIFIED'
before={k:set(getattr(bpy.data,k))for k in('objects','collections','meshes','materials','lights','images','actions','node_groups','texts')}
try:build_package_effects(trial,data,OUT/'manifest.json',scene,camera)
except UnsupportedEffect:pass
else:raise AssertionError('Unknown channel mode was silently accepted')
assert all(set(getattr(bpy.data,k))==v for k,v in before.items())
(OUT/'qualification.json').write_text(json.dumps(dict(status='PASS_ACTUAL_CYCLES_TEXTURE_CHANNELS_AND_ROLLBACK',cases=rows,unknown_refused=True,rollback_exact=True),indent=2));print('PASS_ACTUAL_CYCLES_TEXTURE_CHANNELS_AND_ROLLBACK',rows,flush=True)
