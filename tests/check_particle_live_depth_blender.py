"""Actual Blender animated-camera geometry and shader-UV fixture."""
from pathlib import Path
import sys,json,math,time
import bpy,numpy as np
from mathutils import Vector,Matrix
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--source-root',required=True);parser.add_argument('--output',required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);HERE=Path(args.output).resolve();HERE.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(Path(args.source_root).resolve()))
from agr.blender_particle_live_depth import install_depth_card
from agr.blender_particle_camera import retarget_simulated_effects
t=time.monotonic();s=bpy.context.scene;s.world.use_nodes=True;s.world.node_tree.nodes['Background'].inputs['Color'].default_value=(0,0,0,1);s.world.node_tree.nodes['Background'].inputs['Strength'].default_value=0
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
mesh=bpy.data.meshes.new('Depth fixture');mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],[],[(0,1,2,3)])
uv=mesh.uv_layers.new()
for point,co in zip(uv.data,((0,0),(1,0),(1,1),(0,1))):point.uv=co
obj=bpy.data.objects.new('Depth fixture',mesh);s.collection.objects.link(obj)
mat=bpy.data.materials.new('Depth fixture');mat.use_nodes=True;mesh.materials.append(mat)
nodes=mat.node_tree.nodes;links=mat.node_tree.links;nodes.clear()
coord=nodes.new('ShaderNodeTexCoord');image=nodes.new('ShaderNodeTexImage')
im=bpy.data.images.new('Depth UV gradient',width=64,height=64,float_buffer=True);im.colorspace_settings.name='Non-Color'
pixels=np.asarray([((x+.5)/64,(y+.5)/64,.25,1)for y in range(64)for x in range(64)],dtype=np.float32)
im.pixels.foreach_set(pixels.reshape(-1));im.pack();image.image=im;image.extension='EXTEND'
links.new(coord.outputs['UV'],image.inputs['Vector']);emission=nodes.new('ShaderNodeEmission');links.new(image.outputs['Color'],emission.inputs['Color'])
output=nodes.new('ShaderNodeOutputMaterial');links.new(emission.outputs[0],output.inputs['Surface'])
camera=bpy.data.objects.new('Animated depth fixture camera',bpy.data.cameras.new('Animated depth fixture camera'));s.collection.objects.link(camera);s.camera=camera
camera.data.lens=45;camera.data.clip_start=.01;camera.data.clip_end=1000
for frame,pos in((1,(1.8,-4,3.2)),(2,(-1.3,-4.5,2.8)),(3,(-2,-3.8,4))):
 camera.location=pos;camera.rotation_euler=(-Vector(pos)).to_track_quat('-Z','Y').to_euler()
 camera.keyframe_insert('location',frame=frame);camera.keyframe_insert('rotation_euler',frame=frame)
for at,scale in((1,.001),(2,.2),(3,1)):
 obj.scale=(scale,)*3;obj.keyframe_insert('scale',frame=at)
from agr.blender_effects import _linear
_linear(obj)
s.render.engine='CYCLES';s.cycles.device='CPU';s.cycles.samples=8;s.cycles.use_denoising=False
s.render.threads_mode='FIXED';s.render.threads=4;s.render.resolution_x=480;s.render.resolution_y=320;s.render.resolution_percentage=100
s.render.image_settings.media_type='IMAGE';s.render.image_settings.file_format='PNG';s.render.image_settings.color_mode='RGBA'
out=HERE/'live-depth01';out.mkdir(exist_ok=False);references={};positions={};matrices={};expected_z={}
def render(label,frame):
 s.frame_set(math.floor(frame),subframe=frame-math.floor(frame));s.render.filepath=str(out/(label+'.png'));bpy.ops.render.render(write_still=True)
 img=bpy.data.images.load(s.render.filepath,check_existing=False)
 a=np.asarray(img.pixels[:],dtype=np.float32).reshape(320,480,4);bpy.data.images.remove(img);return a
for frame in(2,2.25,2.5,2.75,3):
 s.frame_set(math.floor(frame),subframe=frame-math.floor(frame));graph=bpy.context.evaluated_depsgraph_get();view=camera.evaluated_get(graph)
 matrix=view.calc_matrix_camera(graph,x=480,y=320)@view.matrix_world.inverted()
 points=np.asarray([list(obj.matrix_world@v.co)+[1]for v in mesh.vertices]);clip=points@np.asarray(matrix).T
 positions[frame]=clip[:,:2]/clip[:,3,None];matrices[frame]=np.asarray(matrix)
 forward=np.asarray(view.matrix_world.to_3x3()@Vector((0,0,-1)))
 shifted=points.copy();shifted[:,:3]+=forward*(80*.0254);biased=shifted@np.asarray(matrix).T
 expected_z[frame]=biased[:,2]/biased[:,3]
 references[frame]=render('before-'+str(frame),frame)
s.frame_set(2);result=install_depth_card(obj,camera,80)
assert len(mesh.uv_layers)==1 and len(mesh.polygons)==2 and len(obj.modifiers)==1
assert len(mesh.materials)==1 and mesh.materials[0]==mat
try:retarget_simulated_effects(s,camera)
except ValueError as exc:assert 'requalification' in str(exc)
else:raise AssertionError('Missing depth-camera guard')
errors=[]
for frame in(2,2.25,2.5,2.75,3):
 s.frame_set(math.floor(frame),subframe=frame-math.floor(frame));graph=bpy.context.evaluated_depsgraph_get();evaluated=obj.evaluated_get(graph)
 points=np.asarray([list(evaluated.matrix_world@v.co)+[1]for v in evaluated.data.vertices]);clip=points@matrices[frame].T
 xy=np.max(np.abs(clip[:,:2]/clip[:,3,None]-positions[frame]));z=np.max(np.abs(clip[:,2]/clip[:,3]-expected_z[frame]))
 assert xy<2e-6 and z<2e-6,(xy,z)
 actual=render('after-'+str(frame),frame);before=references[frame]
 # Exclude the subpixel coverage boundary; the interior tests the real shader UV graph.
 interior=(before[:,:,0]>.1)&(before[:,:,0]<.9)&(before[:,:,1]>.1)&(before[:,:,1]<.9)&(before[:,:,2]>.1)
 occupancy=interior.copy()
 for dy in range(-2,3):
  for dx in range(-2,3):interior &= np.roll(np.roll(occupancy,dy,axis=0),dx,axis=1)
 error=float(np.max(np.abs(actual[interior,:3]-before[interior,:3])))
 assert interior.sum()>100 and error<.0041,(int(interior.sum()),error)
 errors.append(dict(frame=frame,screen_xy_error=float(xy),depth_error=float(z),interior_pixels=int(interior.sum()),png_rgb_error=error))
fixture=HERE/'live-depth01.blend';bpy.ops.wm.save_as_mainfile(filepath=str(fixture))
links.new(coord.outputs['UV'],image.inputs['Vector'])
mutant=render('mutant-without-uv-correction',3)
mutant_error=float(np.max(np.abs(mutant[interior,:3]-references[3][interior,:3])))
assert mutant_error>.01,mutant_error
# Entirely behind camera: preserve original clip W even if bias crosses it.
backmesh=bpy.data.meshes.new('Behind camera');backmesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],[],[(0,1,2,3)])
backuv=backmesh.uv_layers.new()
for p,uvco in zip(backuv.data,((0,0),(1,0),(1,1),(0,1))):p.uv=uvco
backmesh.materials.append(mat.copy());back=bpy.data.objects.new('Behind camera',backmesh);s.collection.objects.link(back)
forward=camera.matrix_world.to_3x3()@Vector((0,0,-1));back.location=camera.matrix_world.translation-forward*.25;back.scale=(.025,)*3
bpy.context.view_layer.update();original=np.asarray([list(back.matrix_world@v.co)for v in backmesh.vertices])
install_depth_card(back,camera,80);bpy.context.view_layer.update();graph=bpy.context.evaluated_depsgraph_get();e=back.evaluated_get(graph)
actual=np.asarray([list(e.matrix_world@v.co)for v in e.data.vertices]);assert np.max(np.abs(actual-original))<2e-6
assert all(abs(v.value-1)<1e-6 for v in e.data.attributes['AGR live depth ratio'].data)
# Remove the vector guard in the private graph: the old ghost crosses in front.
group=back.modifiers[0].node_group;switch=next(n for n in group.nodes if n.bl_idname=='GeometryNodeSwitch'and n.input_type=='VECTOR')
targets=[l.to_socket for l in list(group.links)if l.from_socket==switch.outputs[0]];biased=switch.inputs['True'].links[0].from_socket
for target in targets:group.links.new(biased,target)
bpy.context.view_layer.update();e=back.evaluated_get(bpy.context.evaluated_depsgraph_get());points=np.asarray([list(e.matrix_world@v.co)+[1]for v in e.data.vertices]);clip=points@matrices[3].T
assert np.all(clip[:,3]>0),'Behind-camera negative control did not reproduce ghost'
for target in targets:group.links.new(switch.outputs[0],target)
proof=dict(behind_camera_identity_and_unit_ratio=True,behind_camera_guard_negative_control=True,camera_retarget_guard=True,status='PASS_ACTUAL_ANIMATED_CAMERA_DEPTH_CARD_AND_SHADER_UV',seconds=time.monotonic()-t,
 blender=bpy.app.version_string,bake=result,fixture=str(fixture),mutant_no_uv_error=mutant_error,errors=errors,scope='Live geometry, explicit triangulation, animated camera and shader-node perspective UV correction at integer and fractional samples plus behind-camera preservation. PNG quantization/filtering included; not Source GPU or clipping-floor/lighting parity.')
with(HERE/'live-depth01.json').open('x')as f:json.dump(proof,f,indent=2)
print(proof['status'],errors,flush=True)
