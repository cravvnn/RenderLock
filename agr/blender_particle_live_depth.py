"""Per-evaluation projective card depth; no interpolation of inverse-scale deltas.

Caller owns new quads/materials and the normal package transaction. Camera edits
still require requalification of clipping bounds. Source clipping-floor and
lighting parity are not established by this geometric correction.
"""
import math
from .installed_effects import UnsupportedEffect
from .particle_depth_card_uv import square_basis,FACES

POLICY='agr_live_projective_card_depth_v1'
RATIO='AGR live depth ratio'
UV='AGR live depth UV'

def _graph(camera,bias,uv_name,unit_scale):
 import bpy
 g=bpy.data.node_groups.new('AGR live particle depth','GeometryNodeTree')
 g.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
 g.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
 nodes=g.nodes;links=g.links
 def node(kind):return nodes.new(kind)
 def link(a,b):links.new(a,b)
 def vector(operation,a,b=None):
  n=node('ShaderNodeVectorMath');n.operation=operation
  for v,s in zip((a,b),n.inputs):
   if v is None:continue
   if hasattr(v,'node'):link(v,s)
   else:s.default_value=v
  return n.outputs['Value' if operation=='DOT_PRODUCT'else 'Vector']
 def scalar(operation,a,b):
  n=node('ShaderNodeMath');n.operation=operation
  for v,s in zip((a,b),n.inputs):
   if hasattr(v,'node'):link(v,s)
   else:s.default_value=v
  return n.outputs[0]
 def transform(point,matrix):
  n=node('FunctionNodeTransformPoint');link(point,n.inputs['Vector']);link(matrix,n.inputs['Transform']);return n.outputs[0]
 inp=node('NodeGroupInput');out=node('NodeGroupOutput')
 self_obj=node('GeometryNodeSelfObject');self_info=node('GeometryNodeObjectInfo');self_info.transform_space='ORIGINAL'
 link(self_obj.outputs[0],self_info.inputs['Object'])
 cam=node('GeometryNodeObjectInfo');cam.transform_space='ORIGINAL';cam.inputs['Object'].default_value=camera
 pos=node('GeometryNodeInputPosition');world=transform(pos.outputs[0],self_info.outputs['Transform'])
 rotate=node('ShaderNodeVectorRotate');rotate.rotation_type='EULER_XYZ';rotate.inputs['Vector'].default_value=(0,0,-1)
 link(cam.outputs['Rotation'],rotate.inputs['Rotation']);forward=rotate.outputs['Vector']
 delta=vector('SUBTRACT',world,cam.outputs['Location']);depth=vector('DOT_PRODUCT',delta,forward)
 ratio=scalar('DIVIDE',scalar('ADD',depth,bias*unit_scale),depth)
 if camera.data.type=='ORTHO':
  ratio=1.;proxy=vector('ADD',world,vector('MULTIPLY',forward,(bias*unit_scale,)*3))
 else:
  scaled=node('ShaderNodeVectorMath');scaled.operation='SCALE';link(delta,scaled.inputs[0]);link(ratio,scaled.inputs['Scale'])
  proxy=vector('ADD',cam.outputs['Location'],scaled.outputs['Vector'])
 # Source keeps the original clip W: an entirely behind-camera quad remains
 # clipped even if its biased depth would cross the camera. Match the pure
 # preflight's identity result there; mixed-plane cases still fail preflight.
 front=scalar('GREATER_THAN',depth,1e-8)
 def choose(kind,original,biased):
  n=node('GeometryNodeSwitch');n.input_type=kind;link(front,n.inputs['Switch'])
  for value,socket in((original,n.inputs['False']),(biased,n.inputs['True'])):
   if hasattr(value,'node'):link(value,socket)
   else:socket.default_value=value
  return n.outputs[0]
 proxy=choose('VECTOR',world,proxy);ratio=choose('FLOAT',1.,ratio)
 inverse=node('FunctionNodeInvertMatrix');link(self_info.outputs['Transform'],inverse.inputs[0])
 local=transform(proxy,inverse.outputs[0])
 uv=node('GeometryNodeInputNamedAttribute');uv.data_type='FLOAT_VECTOR';uv.inputs['Name'].default_value=uv_name
 weighted=node('ShaderNodeVectorMath');weighted.operation='SCALE';link(uv.outputs['Attribute'],weighted.inputs[0])
 if isinstance(ratio,float):weighted.inputs['Scale'].default_value=ratio
 else:link(ratio,weighted.inputs['Scale'])
 geom=inp.outputs['Geometry']
 for name,kind,domain,value in((UV,'FLOAT_VECTOR','CORNER',weighted.outputs['Vector']),(RATIO,'FLOAT','POINT',ratio)):
  store=node('GeometryNodeStoreNamedAttribute');store.data_type=kind;store.domain=domain;store.inputs['Name'].default_value=name
  link(geom,store.inputs['Geometry'])
  if isinstance(value,float):store.inputs['Value'].default_value=value
  else:link(value,store.inputs['Value'])
  geom=store.outputs['Geometry']
 set_pos=node('GeometryNodeSetPosition');link(geom,set_pos.inputs['Geometry']);link(local,set_pos.inputs['Position']);link(set_pos.outputs[0],out.inputs['Geometry'])
 return g

def install_depth_card(obj,camera,bias,unit_scale=.0254):
 """Install on an owned quad after caller's actual camera/clip preflight."""
 import bpy
 if bpy.app.version<(5,2,0):raise UnsupportedEffect('Live particle depth requires Blender 5.2 or newer')
 if camera.type!='CAMERA'or camera.data.type not in{'PERSP','ORTHO'}:
  raise UnsupportedEffect('Projective depth requires a perspective or orthographic camera')
 if type(bias)not in(int,float)or not math.isfinite(bias)or abs(bias)>1e5:
  raise UnsupportedEffect('Live depth requires a qualified finite literal bias')
 mesh=obj.data
 if obj.type!='MESH'or mesh.shape_keys or obj.modifiers or len(mesh.vertices)!=4 or len(mesh.polygons)!=1 or tuple(mesh.polygons[0].vertices)!=(0,1,2,3):
  raise UnsupportedEffect('Live depth requires a new unmodified quad')
 if len(mesh.materials)!=1 or mesh.materials[0].users!=1 or len(mesh.uv_layers)!=1:
  raise UnsupportedEffect('Live depth requires its own material and one UV layer')
 mat=mesh.materials[0];nodes=mat.node_tree.nodes;links=mat.node_tree.links
 sources=[n for n in nodes if n.bl_idname=='ShaderNodeTexCoord']
 if len(sources)!=1:raise UnsupportedEffect('Live depth requires one source UV graph')
 targets=[l.to_socket for l in links if l.from_socket==sources[0].outputs['UV']]
 if not targets:raise UnsupportedEffect('Live depth source UV is disconnected')
 uv_name=mesh.uv_layers[0].name;corners=[tuple(p.uv)for p in mesh.uv_layers[0].data];square_basis(corners)
 vertices=[v.co.copy()for v in mesh.vertices]
 for layer in list(mesh.uv_layers):mesh.uv_layers.remove(layer)
 mesh.clear_geometry();mesh.from_pydata(vertices,[],FACES);uv=mesh.uv_layers.new(name=uv_name)
 for loop in mesh.loops:uv.data[loop.index].uv=corners[loop.vertex_index]
 modifier=obj.modifiers.new('AGR projective particle depth','NODES');modifier.node_group=_graph(camera,bias,uv_name,unit_scale)
 attributes=[]
 for name in(UV,RATIO):
  n=nodes.new('ShaderNodeAttribute');n.attribute_name=name;attributes.append(n)
 combine=nodes.new('ShaderNodeCombineXYZ')
 for socket in combine.inputs:links.new(attributes[1].outputs['Fac'],socket)
 divide=nodes.new('ShaderNodeVectorMath');divide.operation='DIVIDE'
 links.new(attributes[0].outputs['Vector'],divide.inputs[0]);links.new(combine.outputs[0],divide.inputs[1])
 for target in targets:links.new(divide.outputs['Vector'],target)
 obj['agr_particle_live_depth_policy']=POLICY;obj['agr_particle_live_depth_bias']=bias
 obj['agr_particle_depth_sampled_camera']=camera.name
 return dict(policy=POLICY,objects=1,camera=camera.name,bias=bias)

def prepare_depth_cards(rows,scene,camera,unit_scale=.0254):
 """Qualify replay samples and 24/30/60 output times before installing graphs."""
 import bpy,numpy as np
 from mathutils import Matrix,Vector
 from .particle_depth_bias import qualify_depth_card
 if not rows:return dict(policy=POLICY,objects=0)
 fps=scene.render.fps/scene.render.fps_base;frames={};prepared=[]
 original=scene.frame_current,scene.frame_subframe
 for row in rows:
  values={bias for _,_,bias in row['samples']}
  if len(values)!=1:raise UnsupportedEffect('Live depth currently requires a literal constant bias')
  bias=next(iter(values));obj=row['obj'];prepared.append((obj,bias))
  times={frame for _,frame,_ in row['samples']};lo,hi=min(times),max(times)
  for rate in(24,30,60):
   first=math.ceil((lo-1)*rate/fps-1e-8);last=math.floor((hi-1)*rate/fps+1e-8)
   times.update(1+n*fps/rate for n in range(first,last+1))
  for at in times:frames.setdefault(at,[]).append((obj,bias))
 try:
  for at,items in sorted(frames.items()):
   scene.frame_set(math.floor(at),subframe=at-math.floor(at));graph=bpy.context.evaluated_depsgraph_get();view=camera.evaluated_get(graph)
   basis=np.asarray(view.matrix_world.to_3x3())
   if not np.allclose(basis.T@basis,np.eye(3),atol=1e-5):raise UnsupportedEffect('Depth camera scale/shear is not qualified')
   matrix=view.calc_matrix_camera(graph,x=scene.render.resolution_x,y=scene.render.resolution_y,
      scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)@view.matrix_world.inverted()@Matrix.Diagonal((unit_scale,unit_scale,unit_scale,1))
   projection=dict(world_to_clip=[list(r)for r in matrix],forward=list((view.matrix_world.to_3x3()@Vector((0,0,-1))).normalized()))
   for obj,bias in items:
    world=np.asarray([list(obj.evaluated_get(graph).matrix_world@v.co)for v in obj.data.vertices])/unit_scale
    qualify_depth_card(world,bias,projection)
  for obj,bias in prepared:install_depth_card(obj,camera,bias,unit_scale)
 finally:
  scene.frame_set(original[0],subframe=original[1]);bpy.context.view_layer.update()
 return dict(policy=POLICY,objects=len(prepared),qualified_camera_frames=len(frames),camera=camera.name,
  scope='Live projective geometry/UV evaluation; camera bounds qualified at supplied replay samples and 24/30/60 FPS. Camera/projection edits need requalification; Source clipping-floor, lighting and full shader parity remain unsupported.')
