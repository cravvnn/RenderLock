"""Actual Blender node samples for modes1/4; fixed UV, no native filtering claim."""
from pathlib import Path
import sys,json,math,argparse
import bpy
parser=argparse.ArgumentParser();parser.add_argument('--source-root',required=True);parser.add_argument('--output',required=True);parser.add_argument('--reopen',action='store_true')
def reopen_fixture():
    from pathlib import Path
    import json,bpy
    bpy.ops.wm.open_mainfile(filepath=str(h/'zoom-rgba-v2-pixels.blend'));s=bpy.context.scene
    assert not bpy.data.libraries
    assert all(image.packed_file is not None for image in bpy.data.images if image.source=='FILE')
    rows=[]
    for frame,expected in ((1,[2/3,2/3,2/3,1.]),(2,[2/3,.796875,.796875,1.])):
     s.frame_set(frame);s.render.filepath=str(h/f'zoom-v2-reopen-{frame}.exr');bpy.ops.render.render(write_still=True)
     image=bpy.data.images.load(s.render.filepath,check_existing=False);value=list(image.pixels[:4]);bpy.data.images.remove(image)
     assert max(abs(a-b) for a,b in zip(value,expected))<2e-5,(frame,value,expected)
     rows.append(dict(frame=frame,actual=value,expected=expected))
    (h/'zoom-v2-pixels-reopen.json').write_text(json.dumps(dict(status='PASS_REOPENED_ANIMATED_ZOOM_RGBA',frames=rows,libraries=0),indent=2),encoding='utf-8')
    print('REOPEN_ZOOM_RGBA_PASS',len(rows))

args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);h=Path(args.output).resolve();sys.path.insert(0,str(Path(args.source_root).resolve()))
if args.reopen:
    reopen_fixture()
    raise SystemExit(0)
h.mkdir(parents=True,exist_ok=False)
from agr.particle_texture_layers import build_layer_stack,LayerNodes,particle_texture_value,gradient_pixels
bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
scene.render.resolution_x=1;scene.render.resolution_y=1;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32';scene.render.image_settings.color_mode='RGBA'
scene.view_settings.view_transform='Raw';scene.render.film_transparent=False
mesh=bpy.data.meshes.new('UniformUVquad');mesh.from_pydata([(-2,-2,0),(2,-2,0),(2,2,0),(-2,2,0)],[],[(0,1,2,3)])
obj=bpy.data.objects.new('UniformUVquad',mesh);scene.collection.objects.link(obj)
cd=bpy.data.cameras.new('Pixel camera');cam=bpy.data.objects.new('Pixel camera',cd);scene.collection.objects.link(cam);cam.location=(0,0,2);cd.type='ORTHO';cd.ortho_scale=1;scene.camera=cam
image=bpy.data.images.new('Known4x4',width=4,height=4,float_buffer=True);image.colorspace_settings.name='Non-Color';image.pixels.foreach_set([v for y in range(4) for x in range(4) for v in(x/3,y/3,.25,y/3)]);image.alpha_mode='STRAIGHT';image.save_render(str(h/'zoom-rgba-source.exr'),scene=scene);bpy.data.images.remove(image);image=bpy.data.images.load(str(h/'zoom-rgba-source.exr'));image.colorspace_settings.name='Non-Color';image.alpha_mode='STRAIGHT';assert abs(image.pixels[43]-2/3)<1e-6;image.pack()
rows=[];animated_material=None
cases=[('zoom_half',.5,False,None,2/3,.796875),('zoom_negative',-.5,False,None,2/3,.796875),('zoom_default',None,False,None,2/3,2/3),('zoom_particle',.25,False,'SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1',2/3,.796875),('distort_zoom',.5,True,None,2/3,1/3),('zoom_default_particle',None,False,'SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1',2/3,.796875),('zoom_animated','animated',False,None,2/3,.796875),('distort_default_multiply',None,True,None,2/9,2/9),('gradient_rgb_alpha',None,False,None,((128/255+.055)/1.055)**2.4,((128/255+.055)/1.055)**2.4)]
for name,rate,distort,pp,expected_r,expected_g in cases:
 material=bpy.data.materials.new(name);material.use_nodes=True;nodes=material.node_tree.nodes;links=material.node_tree.links;nodes.clear();graph=LayerNodes(nodes,links)
 controls={}
 if rate is not None:controls['m_flZoomScale']=rate
 if pp:controls['m_nPerParticleZoom']=pp
 if distort and name!='distort_default_multiply':controls['m_flDistortion']=2.
 layers=[dict(m_nTextureType='SPRITECARD_TEXTURE_ZOOM' if not distort else 'SPRITECARD_TEXTURE_UVDISTORTION_ZOOM',m_nTextureBlendMode='SPRITECARD_TEXTURE_BLEND_REPLACE',m_TextureControls=controls)]
 if distort:layers.insert(0,dict(m_nTextureBlendMode='SPRITECARD_TEXTURE_BLEND_REPLACE'))
 if name=='distort_default_multiply':
  layers[-1]['m_nTextureType']='SPRITECARD_TEXTURE_UVDISTORTION';layers[-1]['m_nTextureBlendMode']='SPRITECARD_TEXTURE_BLEND_MULTIPLY'
 if name=='gradient_rgb_alpha':layers=[dict(m_nTextureType='SPRITECARD_TEXTURE_COLOR',m_nTextureChannels='SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA_RGBALPHA',m_bReplaceTextureWithGradient=True,m_Gradient={'m_Stops':[dict(m_flPosition=0,m_Color=[0,0,0]),dict(m_flPosition=1,m_Color=[255,255,255])]})]
 sampled=[]
 def sample(layer,index,raw=False):
  sampled.append((index,raw));node=nodes.new('ShaderNodeTexImage');node.image=image;node.interpolation='Closest';node.extension='REPEAT'
  if name=='gradient_rgb_alpha':
   generated=bpy.data.images.new('Generated gradient',width=256,height=1,float_buffer=True);generated.colorspace_settings.name='Non-Color';generated.pixels.foreach_set(gradient_pixels(layer['m_Gradient']));generated.pack();node.image=generated
  coordinate=(.5,.5,0) if name=='gradient_rgb_alpha' else (.51,.51,0) if name=='distort_default_multiply' and index==0 else (.6,.6,0) if distort and index==0 else (.6,.7,0)
  uv=graph.vector(coordinate);links.new(uv,node.inputs['Vector']);return node,uv
 def parameter(value,default):
  if value!='animated':return default if value is None else value
  node=nodes.new('ShaderNodeValue')
  for frame,value in((1,0.),(2,.5)):
   node.outputs[0].default_value=value;node.outputs[0].keyframe_insert('default_value',frame=frame)
  return node.outputs[0]
 color,alpha=build_layer_stack(nodes,links,layers,sample,parameter,particle_input=lambda mode:particle_texture_value(mode,dict(shader_extra_data1=.5 if name=='zoom_default_particle' else 2.),'agr_shader_extra_fields_v1'))
 output=nodes.new('ShaderNodeOutputMaterial');emission=nodes.new('ShaderNodeEmission');split=nodes.new('ShaderNodeSeparateXYZ');links.new(color,split.inputs[0]);links.new(graph.vector((split.outputs[0],split.outputs[1],alpha)),emission.inputs['Color']);links.new(emission.outputs[0],output.inputs['Surface'])
 obj.data.materials.clear();obj.data.materials.append(material);scene.frame_set(2)
 scene.render.filepath=str(h/(name+'-rgba-v2.exr'));bpy.ops.render.render(write_still=True)
 rendered=bpy.data.images.load(scene.render.filepath,check_existing=False);actual=list(rendered.pixels[:4]);bpy.data.images.remove(rendered)
 expected=[expected_r,expected_g,expected_g,1.];assert max(abs(a-b) for a,b in zip(actual,expected))<2e-5,(name,actual,expected)
 assert sampled==([(0,False),(1,True)] if distort else [(0,False)]),sampled
 rows.append(dict(name=name,actual=actual,expected=expected))
 if rate=='animated':animated_material=material
assert animated_material is not None
obj.data.materials.clear();obj.data.materials.append(animated_material);scene.frame_set(2)
bpy.ops.wm.save_as_mainfile(filepath=str(h/'zoom-rgba-v2-pixels.blend'))
(h/'zoom-rgba-v2-pixels-build.json').write_text(json.dumps(dict(status='PASS_ACTUAL_ZOOM_NODES',cases=rows,limits='Nine rendered1x1 fixed-coordinate nearest-filter samples, including animated node, explicit PP multiplier, omitted native distortion amount with multiplied re-sample, and generated RGB-alpha gradient. Not game GPU derivative/atlas/filtering parity.'),indent=2),encoding='utf-8')
print('ZOOM_PIXELS_PASS',len(rows))
