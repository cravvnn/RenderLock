"""Asset-free actual material UV/color/opacity and packed saved-reopen checks.

Blender --background --python this_file -- <fresh-dir> [--reopen].
Emission probes measure actual graph arithmetic; they do not claim Source PBR
or final lit-sphere parity. Animated color is checked at 24/30/60 output FPS.
"""
from pathlib import Path
import sys,json,math
import bpy,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_flashbang_sphere import build_material
from agr.blender_effects import _linear
args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);reopen='--reopen'in args
uv=(.42,.61)
if not reopen:
    out.mkdir(parents=True,exist_ok=False);bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    images={}
    for key in ('color','alt','white','black'):
        im=bpy.data.images.new(key,width=32,height=32,alpha=True,float_buffer=True);im.colorspace_settings.name='Non-Color'
        pixels=[]
        for y in range(32):
            for x in range(32):
                u,v=(x+.5)/32,(y+.5)/32
                pixels.extend((u,v,.25,.2+.3*u)if key=='color'else(.1+.5*u,0,0,1)if key=='alt'else(1,1,1,1)if key=='white'else(0,0,0,1))
        im.pixels.foreach_set(pixels);im.pack();images[key]=im
    texnames=dict(g_tColor='color',g_tAltTranslucency='alt',g_tTintMask='white',g_tAmbientOcclusion='white',g_tMetalness='black')
    floats=dict(g_flAlphaAnglePower1=-3.031,g_flOpacityScale1=1.,g_flTranslucentTexcoordRotation0_1=0.,g_flTranslucentTexcoordRotation1_1=90.)
    vectors=dict(g_vAlbedoTexcoordScale1=[2,1,0,0],g_vTranslucentTexcoordScale0_1=[3,3,0,0],g_vTranslucentTexcoordOffset0_1=[0,0,0,0],g_vTranslucentTexcoordScale1_1=[1,1,0,0],g_vTranslucentTexcoordOffset1_1=[0,0,0,0])
    mat=build_material('analytical sphere graph',texnames,floats,vectors,lambda name,sequence:images[name])
    nodes,links=mat.node_tree.nodes,mat.node_tree.links
    coordinate=next(n for n in nodes if n.bl_idname=='ShaderNodeTexCoord');fixed=nodes.new('ShaderNodeCombineXYZ')
    fixed.inputs[0].default_value=uv[0];fixed.inputs[1].default_value=uv[1]
    for link in list(coordinate.outputs['UV'].links):links.new(fixed.outputs[0],link.to_socket)
    mesh=bpy.data.meshes.new('probe plane');mesh.from_pydata([(-5,-5,0),(5,-5,0),(5,5,0),(-5,5,0)],[],[(0,1,2,3)])
    obj=bpy.data.objects.new('probe plane',mesh);scene.collection.objects.link(obj);mesh.materials.append(mat)
    camera=bpy.data.objects.new('probe camera',bpy.data.cameras.new('probe camera'));scene.collection.objects.link(camera);scene.camera=camera
    camera.data.type='ORTHO';camera.data.ortho_scale=.001
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
    scene.render.threads_mode='FIXED';scene.render.threads=2;scene.render.resolution_x=1;scene.render.resolution_y=1;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='32'
    scene['sphere_fixture_scope']=__doc__;bpy.ops.wm.save_as_mainfile(filepath=str(out/'sphere-material.blend'))
else:
    bpy.ops.wm.open_mainfile(filepath=str(out/'sphere-material.blend'),load_ui=False,use_scripts=False);scene=bpy.context.scene;obj=bpy.data.objects['probe plane'];camera=scene.camera;mat=obj.data.materials[0]
assert all(im.packed_file for im in bpy.data.images) and not bpy.data.libraries
nodes,links=mat.node_tree.nodes,mat.node_tree.links;emission=nodes.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=1
output=next(n for n in nodes if n.bl_idname=='ShaderNodeOutputMaterial');links.new(emission.outputs[0],output.inputs['Surface'])
rows=[];rna=0
for fps in (24,30,60):
    scene.render.fps=fps;scene.render.fps_base=1
    obj.animation_data_clear()
    for t,c in ((0,(.2,.4,.6,.5)),(1,(.6,.2,.4,.9))):obj.color=c;obj.keyframe_insert('color',frame=1+t*fps/64)
    _linear(obj)
    for t in (.25,.75):
        frame=1+t*fps/64;scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame));color=np.array((.2,.4,.6,.5))*(1-t)+np.array((.6,.2,.4,.9))*t
        assert np.max(abs(np.asarray(obj.color)-color))<2e-6;rna+=1
        # Independent closed-form UVs: source=(.42,.39), centered scale, then
        # flip back to Blender. The alt mask uses a 90 degree source rotation.
        expected_color=np.array([.34,.61,.25])*color[:3]
        for angle in (0,40,60):
            a=math.radians(angle);camera.location=(5*math.sin(a),0,5*math.cos(a));camera.rotation_euler=(0,a,0)
            expected_alpha=(.2+.3*.26)*(.1+.5*.61)*color[3]*max(.001,1-abs(math.cos(a)))**3.031
            for mode,expected in (('color',expected_color),('alpha',np.repeat(expected_alpha,3))):
                links.new(nodes['AGR sphere diffuse'if mode=='color'else'AGR sphere opacity'].outputs[0],emission.inputs['Color'])
                p=out/f'{"reopen"if reopen else"build"}-{fps}-{t}-{angle}-{mode}.exr';scene.render.filepath=str(p);bpy.ops.render.render(write_still=True)
                im=bpy.data.images.load(str(p),check_existing=False);actual=np.asarray(im.pixels[:3]);bpy.data.images.remove(im)
                error=float(np.max(abs(actual-expected)));assert error<4e-6,(fps,t,angle,mode,actual,expected)
                rows.append(dict(fps=fps,phase=t,angle=angle,mode=mode,error=error))
assert len(rows)==36 and rna==6
result=dict(status='PASS',phase='reopen'if reopen else'build',pixel_checks=36,evaluated_color_checks=rna,maximum_pixel_error=max(r['error']for r in rows),rows=rows,scope=__doc__)
(out/f'{"reopen"if reopen else"build"}-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
