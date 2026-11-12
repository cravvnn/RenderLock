"""Actual rope depth-weighted UV/color, distortion, scroll and blend pixels.

Blender --background --python this_file -- <fresh-dir> [--reopen].
Synthetic immutable proof packet; installed native/shader identity is qualified
separately. Tests graph arithmetic and saved packing, not game GPU parity.
"""
from pathlib import Path
import sys,json
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_rope_material import prepare_rope_shader_colors,build_rope_material
from agr.particle_rope_shader_inputs import POLICY,PARTICLES_SHA
from agr.particle_sheet_uv import SHADERS
args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);reopen='--reopen'in args
proof=dict(policy=POLICY,particles_sha256=PARTICLES_SHA,shader_sha256=SHADERS,segment_seed=0,
    blend_alpha_parameters={'ALPHA':1.,'ADD':0.,'BLEND_ADD':-1.})
background=np.array([.05,.1,.15]);vertex_color=np.array([.2,.4,.6])
if not reopen:
    out.mkdir(parents=True,exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
    scene.render.threads_mode='FIXED';scene.render.threads=2;scene.render.resolution_x=1152;scene.render.resolution_y=64;scene.render.resolution_percentage=100
    camera=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));scene.collection.objects.link(camera);scene.camera=camera
    camera.location=(9,.5,5);camera.data.type='ORTHO';camera.data.ortho_scale=18
    world=bpy.data.worlds.new('analytic background');world.use_nodes=True;scene.world=world
    world.node_tree.nodes['Background'].inputs['Color'].default_value=(*background,1);world.node_tree.nodes['Background'].inputs['Strength'].default_value=1
    images={}
    for key in ('base','noise'):
        image=bpy.data.images.new(key,width=32,height=32,alpha=True,float_buffer=True);image.colorspace_settings.name='Non-Color';image.alpha_mode='STRAIGHT'
        pixels=[]
        for y in range(32):
            for x in range(32):
                u,v=(x+.5)/32,(y+.5)/32;pixels.extend((u,v,.25,.8)if key=='base'else(.1+.4*u,.2+.3*v,.5,.5))
        image.pixels.foreach_set(pixels);image.pack();images[key]=image
    expected=[]
    for age in (.25,.75):
        for mode in ('ALPHA','ADD','BLEND_ADD'):
            for alpha in (0.,.5,1.):
                cell=len(expected);mesh=bpy.data.meshes.new('weighted rope '+str(cell))
                mesh.from_pydata([(cell,0,0),(cell+1,0,0),(cell+1,1,0),(cell,1,0)],[],[(0,1,2),(0,2,3)])
                uv=mesh.uv_layers.new(name='UVMap');color=mesh.color_attributes.new(name='AGR Rope Color',type='FLOAT_COLOR',domain='CORNER')
                ratio=mesh.attributes.new(name='AGR Rope Depth Ratio',type='FLOAT',domain='POINT')
                for value,factor in zip(ratio.data,(1.,2.,3.,4.)):value.value=factor
                for loop in mesh.loops:
                    factor=ratio.data[loop.vertex_index].value;uv.data[loop.index].uv=(.3*factor,.6*factor);color.data[loop.index].color=(*vertex_color,alpha)
                renderer=dict(m_flDepthBias=5,m_bGammaCorrectVertexColors=False,m_flSelfIllumAmount=1,m_flDiffuseAmount=0,
                    m_bSaturateColorPreAlphaBlend=False,m_nOutputBlendMode='PARTICLE_OUTPUT_BLEND_MODE_'+mode,
                    m_vecTexturesInput=[dict(m_hTexture='base'),dict(m_hTexture='noise',m_nTextureType='SPRITECARD_TEXTURE_UVDISTORTION',
                    m_nTextureBlendMode='SPRITECARD_TEXTURE_BLEND_REPLACE',m_TextureControls=dict(m_bRandomizeOffsets=True,
                        m_flDistortion=.8,m_flFinalTextureScaleU=2.,m_flFinalTextureScaleV=.25,
                        m_flFinalTextureOffsetU=dict(m_nType='PF_TYPE_COLLECTION_AGE',m_nMapType='PF_MAP_TYPE_MULT',m_flMultFactor=-.4)))])
                prepare_rope_shader_colors(mesh,renderer)
                material,_=build_rope_material(renderer,images,scene_light=(1,1,1),age=age,mesh=mesh,depth_compensated=True,native_inputs=proof)
                mesh.materials.append(material);obj=bpy.data.objects.new(mesh.name,mesh);scene.collection.objects.link(obj)
                # Independent scalar equations, not the production graph/helper.
                noise_u=.4-.4*age;noise_v=.9
                r,g=.1+.4*noise_u,.2+.3*noise_v
                warped=np.array([.3-(r-.5)*.8*.125*.5*2,.6-(g-.5)*.8*.125*.5*2,.25])
                emitted=warped*vertex_color;a=(.8*.8*(3-2*.8))*alpha
                rgb=(emitted if mode=='BLEND_ADD'else emitted*a)+(background if mode=='ADD'else background*(1-a))
                expected.append(dict(cell=cell,age=age,mode=mode,alpha=alpha,rgb=rgb.tolist()))
    scene['rope_expected']=json.dumps(expected)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'rope-shader.blend'))
else:
    bpy.ops.wm.open_mainfile(filepath=str(out/'rope-shader.blend'),load_ui=False,use_scripts=False);scene=bpy.context.scene
assert not bpy.data.libraries and all(im.packed_file for im in bpy.data.images)
scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32';scene.render.image_settings.color_mode='RGBA'
phase='reopen'if reopen else'build';scene.render.filepath=str(out/(phase+'.exr'));bpy.ops.render.render(write_still=True)
image=bpy.data.images.load(scene.render.filepath,check_existing=False);pixels=np.asarray(image.pixels[:]).reshape(64,1152,4)
errors=[]
for row in json.loads(scene['rope_expected']):
    actual=pixels[32,32+row['cell']*64,:3];error=float(np.max(abs(actual-row['rgb'])))
    assert error<3e-5,(row,actual,error);errors.append(error)
assert len(errors)==18
result=dict(status='PASS',phase=phase,pixels=18,maximum_error=max(errors),scope=__doc__)
(out/(phase+'-result.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result))
