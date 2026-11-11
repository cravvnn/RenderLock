import sys,json,copy,math,argparse
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
    definitions={'particles/owned.vpcf':dict(m_Renderers=[dict(_class='C_OP_RenderSprites',m_flDepthBias=80.,m_vecTexturesInput=[dict(m_hTexture='materials/owned.vtex')])])})
package=dict(schema_version=1,backend='simulated_states_v1',entity_ids=['actor'],scene_identity=scene_identity(data,['actor']),coverage_scope='Owned synthetic sprite only',simulation=simulation,assets=assets)
groups=('objects','collections','meshes','materials','lights','images','actions','node_groups','texts')
def snapshot():return {name:set(getattr(bpy.data,name))for name in groups}
def remove_created(before):
    bpy.data.batch_remove([block for name in groups for block in set(getattr(bpy.data,name))-before[name]])
scene.frame_set(7,subframe=.25);cases=[]
for label,bias,scaled in [('dynamic',dict(m_nType='PF_TYPE_COLLECTION_AGE'),False),('camera-plane',-100000.,False),('scaled-camera',80.,True)]:
    trial=copy.deepcopy(package);trial['assets']['definitions']['particles/owned.vpcf']['m_Renderers'][0]['m_flDepthBias']=bias
    before=snapshot();camera.scale=(2,2,2)if scaled else(1,1,1)
    try:build_package_effects(trial,data,OUT/'manifest.json',scene,camera)
    except UnsupportedEffect as error:cases.append(dict(case=label,refused=str(error)))
    else:raise AssertionError('Unsupported depth admitted: '+label)
    assert snapshot()==before,label
    assert scene.frame_current==7 and abs(scene.frame_subframe-.25)<1e-6
camera.scale=(1,1,1)
for label,bias in [('zero',0.),('literal',80.),('wrapped-literal',dict(m_nType='PF_TYPE_LITERAL',m_flLiteralValue=80.))]:
    trial=copy.deepcopy(package);trial['assets']['definitions']['particles/owned.vpcf']['m_Renderers'][0]['m_flDepthBias']=bias
    before=snapshot();report=build_package_effects(trial,data,OUT/'manifest.json',scene,camera)
    if label=='zero':assert not report.get('live_depth_cards')
    else:
        assert report['live_depth_cards']['objects']==1 and report['status']=='partial'
        assert report['camera_binding']=='live_constraints_with_qualified_projective_depth'
        card=next(o for o in scene.objects if o.get('agr_particle_live_depth_policy'))
        assert len(card.modifiers)==1 and len(card.data.polygons)==2
    assert scene.frame_current==7 and abs(scene.frame_subframe-.25)<1e-6
    cases.append(dict(case=label,status=report['status'],depth_cards=report.get('live_depth_cards',{}).get('objects',0)))
    remove_created(before);assert snapshot()==before
(OUT/'qualification.json').write_text(json.dumps(dict(status='PASS',cases=cases,source_root=args.source_root,
    scope='Real merged public renderer and normal package transaction; owned synthetic bound sprite, no native simulator/game assets'),indent=2))
print('NORMAL_PACKAGE_DEPTH_AND_ROLLBACK_PASS',flush=True)
