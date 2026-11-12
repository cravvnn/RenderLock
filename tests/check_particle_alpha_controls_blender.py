"""Actual renderer/pixel/rollback regression for captured alpha controls.

Run background Blender with --python this_file -- <fresh-output-dir>, then reopen
alpha-controls.blend and run with -- <same-output-dir> --reopen. Synthetic assets
only; the normal installed-effect path is qualified separately.
"""
import sys,json,math,copy
from pathlib import Path
import bpy,numpy as np
from bpy_extras.object_utils import world_to_camera_view
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.simulated_effect_package import scene_identity,build_package_effects
from agr.particle_render_controls import SNAPSHOT_POLICY

args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);reopen='--reopen'in args
clamped='--clamp'in args
groups=('objects','collections','meshes','shape_keys','materials','lights','images','actions','node_groups','texts')
def inventory():return {key:set(getattr(bpy.data,key))for key in groups}
if not reopen:
    out.mkdir(parents=True,exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene;scene.render.fps=64
    camera=bpy.data.objects.new('Alpha camera',bpy.data.cameras.new('Alpha camera'))
    scene.collection.objects.link(camera);scene.camera=camera;camera.location=(0,0,10);camera.data.type='ORTHO';camera.data.ortho_scale=5
    image=bpy.data.images.new('Explicit white texture',width=2,height=2);image.pixels.foreach_set([1.]*16)
    image.filepath_raw=str(out/'white.png');image.file_format='PNG';image.save();bpy.data.images.remove(image)
    image=bpy.data.images.new('UV curve pattern',width=8,height=8,alpha=True)
    pixels=np.ones((8,8,4),dtype=np.float32);pixels[:,:,3]=np.arange(8,dtype=np.float32)[:,None]*32/255;image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw=str(out/'pattern.png');image.file_format='PNG';image.save();bpy.data.images.remove(image)
    identity={}
    for field in ('model_file','pose_file','skeleton_file'):
        path=out/field;path.write_text('synthetic identity '+field);identity[field]=str(path)
    data=dict(start_tick=100,end_tick=102,tick_rate=64,entities=[dict(id='actor',samples=[],**identity)])
    cp=dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT',m_nControlPoint=5,m_nVectorComponent=0)
    definitions={}
    for name,inverse in [('direct.vpcf',False),('inverse.vpcf',True)]:
        provider=dict(cp)
        if inverse:provider.update(m_nMapType='PF_MAP_TYPE_REMAP',m_nInputMode='PF_INPUT_MODE_CLAMPED',m_flInput0=0,m_flInput1=1,m_flOutput0=1,m_flOutput1=0)
        definitions[name]=dict(m_Renderers=[dict(_class='C_OP_RenderSprites',m_flAlphaScale=provider,
            m_nOutputBlendMode='PARTICLE_OUTPUT_BLEND_MODE_ALPHA',m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',
            m_vecTexturesInput=[dict(m_hTexture='materials/white.vtex')])])
    definitions['curve.vpcf']=dict(m_Renderers=[dict(_class='C_OP_RenderSprites',
        m_nOutputBlendMode='PARTICLE_OUTPUT_BLEND_MODE_ALPHA',m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',
        m_vecTexturesInput=[dict(m_hTexture='materials/pattern.vtex',m_TextureControls=dict(m_flFinalTextureOffsetV=dict(
            m_nType='PF_TYPE_COLLECTION_AGE',m_nMapType='PF_MAP_TYPE_CURVE',m_Curve=dict(m_spline=[
            dict(x=0,y=0,m_flSlopeOutgoing=.5),dict(x=1,y=.5,m_flSlopeIncoming=.5)]))))])])
    definitions['curve.vpcf']['m_Renderers'][0]['m_vecTexturesInput'][0]['m_TextureControls']['m_bClampUVs']=clamped
    scene['alpha_fixture_clamped']=clamped
    camera.data.ortho_scale=8
    frames=[]
    for tick,values in [(100,(1.,1.)),(101,(.25,.75)),(102,(.75,.25))]:
        systems=[]
        for name,path,x,value in [('direct.vpcf','root',-60,values[0]),('inverse.vpcf','root/0',60,values[1])]:
            particle=dict(id=0,position=[x,0,0],previous=[x,0,0],radius=20,alpha=1,alpha_alternate=1,
                color=[1,1,1],sequence=0,normal=[0,0,1],rotation=[0,0,0],age=(tick-100)/64,trail=1)
            systems.append(dict(name=name+'_c',system_path=path,step=1/64,system_age=(tick-100)/64,
                control_points=[dict(index=0,position=[0,0,0]),dict(index=5,position=[value,0,0])],particles=[particle]))
        curve=copy.deepcopy(systems[0]);curve.update(name='curve.vpcf_c',system_path='root/1',system_age=(tick-100)*.5)
        curve['particles'][0]['position']=[0,50,0];curve['particles'][0]['previous']=[0,50,0];systems.append(curve)
        frames.append(dict(tick=tick,systems=systems))
    simulation=dict(schema_version=1,backend='synthetic alpha plumbing',warnings=[],start_tick=100,end_tick=102,tick_rate=64,
        control_point_snapshot_policy=SNAPSHOT_POLICY,instances=[dict(id='fixture',entity_id='actor',birth=100,
        control_point_indices=[0,5],materialized_control_point_indices=[0,5],frames=frames)])
    textures={'materials/white.vtex':dict(status='supported',directory=str(out),is_sprite_sheet=False,
        sequences={'0':dict(status='supported',frames=[dict(image='white.png')])})}
    textures['materials/pattern.vtex']=dict(status='supported',directory=str(out),is_sprite_sheet=False,
        sequences={'0':dict(status='supported',frames=[dict(image='pattern.png')])})
    package=dict(schema_version=1,backend='simulated_states_v1',entity_ids=['actor'],scene_identity=scene_identity(data,['actor']),
        coverage_scope='Synthetic alpha-input and shader plumbing only',simulation=simulation,assets=dict(definitions=definitions,textures=textures))
    before=inventory();bad=copy.deepcopy(package)
    bad['simulation']['instances'][0]['frames'][-1]['systems'][1]['control_points'].pop()
    try:build_package_effects(bad,data,out/'package.json',scene,camera)
    except ValueError as error:
        assert 'snapshot'in str(error),str(error)
    else:raise AssertionError('Missing child snapshot was accepted')
    assert inventory()==before,'Late child alpha failure leaked IDs'
    bad=copy.deepcopy(package);bad['simulation'].pop('control_point_snapshot_policy')
    for name in ('direct.vpcf','inverse.vpcf'):bad['assets']['definitions'][name]['m_Renderers'][0]['m_flAlphaScale']=1
    try:build_package_effects(bad,data,out/'package.json',scene,camera)
    except ValueError as error:assert 'per-system age'in str(error),str(error)
    else:raise AssertionError('Curve UV mapping guessed collection age without snapshots')
    assert inventory()==before,'Missing UV age snapshot leaked IDs'
    report=build_package_effects(package,data,out/'package.json',scene,camera)
    assert report['objects']==3
    curve=next(o for o in scene.objects if o.get('agr_particle_system_path')=='root/1')
    for uv in curve.data.uv_layers.active.data:uv.uv=(.5,.125)
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
    scene.render.threads_mode='FIXED';scene.render.threads=2
    scene.render.resolution_x=32;scene.render.resolution_y=16;scene.render.resolution_percentage=100;scene.render.film_transparent=True
    scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='32'
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'alpha-controls.blend'))
else:
    scene=bpy.context.scene;clamped=bool(scene['alpha_fixture_clamped'])
objects={o['agr_particle_system_path']:o for o in scene.objects if o.get('agr_particle_instance')=='fixture'}
assert set(objects)=={'root','root/0','root/1'}
assert not bpy.data.libraries and all(i.packed_file for i in bpy.data.images if i.type=='IMAGE')
results=[]
for frame,wanted in [(1,(1.,0.)),(2,(.25,.25)),(2.25,(.375,.375)),(2.5,(.5,.5)),(3,(.75,.75))]:
    scene.frame_set(math.floor(frame),subframe=frame%1)
    actual=tuple(objects[path].color[3]for path in ('root','root/0'))
    np.testing.assert_allclose(actual,wanted,atol=2e-6,rtol=0)
    scene.render.filepath=str(out/(('reopen-'if reopen else '')+str(frame)+'.exr'));bpy.ops.render.render(write_still=True)
    image=bpy.data.images.load(scene.render.filepath,check_existing=False)
    pixels=np.asarray(image.pixels[:]).reshape((16,32,4));values=[]
    for path in ('root','root/0','root/1'):
        ndc=world_to_camera_view(scene,scene.camera,objects[path].matrix_world.translation)
        values.append(float(pixels[int(ndc.y*16),int(ndc.x*32),3]))
    bpy.data.images.remove(image)
    curve_expected=({1:16/255,2:72/255,2.25:86/255,2.5:100/255,3:0.}if clamped else {1:16/255,2:80/255,2.25:96/255,2.5:112/255,3:144/255})[frame]
    np.testing.assert_allclose(values,(*wanted,curve_expected),atol=2e-6,rtol=0)
    results.append(dict(frame=frame,expected=(*wanted,curve_expected),object_alpha=actual,pixel_alpha=values))
(out/('reopen.json'if reopen else 'result.json')).write_text(json.dumps(dict(status='PASS_ACTUAL_ALPHA_CONTEXT_PIXELS',
    reopened=reopen,clamped=clamped,cases=results,late_missing_child_rollback=not reopen),indent=2))
