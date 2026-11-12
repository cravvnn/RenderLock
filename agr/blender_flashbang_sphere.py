"""Static sphere with two opacity masks, facing fade and diffuse scene lighting.

Geometry/UV/color/opacity are reconstructed; packed normals, exact Source PBR,
depth behavior, fog and lighting remain explicit approximations.
"""
import math


def build_material(name,texnames,floats,vectors,texture):
    import bpy
    material=bpy.data.materials.new(name+' | flashbang translucent PBR');material.use_nodes=True
    material['agr_model_particle_material']='bounded_flashbang_translucent_pbr_approximation'
    nodes,links=material.node_tree.nodes,material.node_tree.links;nodes.clear()
    def scalar(op,a,b):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for value,socket in ((a,node.inputs[0]),(b,node.inputs[1])):
            if isinstance(value,(int,float)):socket.default_value=value
            else:links.new(value,socket)
        return node.outputs[0]
    def vector(op,a,b):
        node=nodes.new('ShaderNodeVectorMath');node.operation=op
        for value,socket in ((a,node.inputs[0]),(b,node.inputs[1])):
            if isinstance(value,(tuple,list)):socket.default_value=value
            else:links.new(value,socket)
        return node.outputs['Vector']
    coordinate=nodes.new('ShaderNodeTexCoord')
    source=vector('ADD',vector('MULTIPLY',coordinate.outputs['UV'],(1,-1,1)),(0,1,0))
    def mapped(prefix):
        if prefix.startswith('Translucent'):
            suffix=prefix[len('Translucent'):]
            scale=vectors['g_vTranslucentTexcoordScale'+suffix+'_1']
            offset=vectors['g_vTranslucentTexcoordOffset'+suffix+'_1']
            angle=floats['g_flTranslucentTexcoordRotation'+suffix+'_1']
        else:
            scale=vectors.get('g_v'+prefix+'TexcoordScale1',[1,1,0,0])
            offset=vectors.get('g_v'+prefix+'TexcoordOffset1',[0,0,0,0])
            angle=floats.get('g_fl'+prefix+'TexcoordRotation1',0)
        rotate=nodes.new('ShaderNodeVectorRotate');rotate.rotation_type='AXIS_ANGLE'
        rotate.inputs['Axis'].default_value=(0,0,1);rotate.inputs['Angle'].default_value=math.radians(angle)
        links.new(vector('SUBTRACT',source,(.5,.5,0)),rotate.inputs['Vector'])
        placed=vector('ADD',vector('MULTIPLY',rotate.outputs['Vector'],(*scale[:2],1)),(.5+offset[0],.5+offset[1],0))
        return vector('ADD',vector('MULTIPLY',placed,(1,-1,1)),(0,1,0))
    def image(resource,coords,raw=False):
        node=nodes.new('ShaderNodeTexImage');node.image=texture(resource,0)
        if raw:
            node.image=node.image.copy();node.image.colorspace_settings.name='Non-Color';node.image.pack()
        links.new(coords,node.inputs['Vector']);return node
    base=image(texnames['g_tColor'],mapped('Albedo'))
    opacity=image(texnames['g_tColor'],mapped('Translucent0'))
    alt=image(texnames['g_tAltTranslucency'],mapped('Translucent1'),True)
    tintmask=image(texnames['g_tTintMask'],source,True)
    ao=image(texnames['g_tAmbientOcclusion'],mapped('AmbientOcclusion'),True)
    metal=image(texnames['g_tMetalness'],source,True)
    info=nodes.new('ShaderNodeObjectInfo')
    tint=nodes.new('ShaderNodeMixRGB');tint.blend_type='MULTIPLY'
    links.new(tintmask.outputs['Color'],tint.inputs[0]);links.new(base.outputs['Color'],tint.inputs[1]);links.new(info.outputs['Color'],tint.inputs[2])
    diffuse=nodes.new('ShaderNodeMixRGB');diffuse.blend_type='MULTIPLY';diffuse.inputs[0].default_value=1
    links.new(tint.outputs[0],diffuse.inputs[1]);links.new(ao.outputs['Color'],diffuse.inputs[2])
    color_node=nodes.new('NodeReroute');color_node.name='AGR sphere diffuse';links.new(diffuse.outputs[0],color_node.inputs[0])
    geometry=nodes.new('ShaderNodeNewGeometry');dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT'
    links.new(geometry.outputs['Normal'],dot.inputs[0]);links.new(geometry.outputs['Incoming'],dot.inputs[1])
    facing=scalar('ABSOLUTE',dot.outputs['Value'],0);power=floats['g_flAlphaAnglePower1']
    if power<0:facing=scalar('SUBTRACT',1,facing)
    fade=scalar('POWER',scalar('MAXIMUM',facing,.001),abs(power))
    red=nodes.new('ShaderNodeSeparateColor');red.mode='RGB';links.new(alt.outputs['Color'],red.inputs[0])
    alpha=scalar('MULTIPLY',scalar('MULTIPLY',opacity.outputs['Alpha'],red.outputs['Red']),info.outputs['Alpha'])
    alpha=scalar('MULTIPLY',scalar('MULTIPLY',alpha,fade),floats['g_flOpacityScale1'])
    alpha_node=nodes.new('NodeReroute');alpha_node.name='AGR sphere opacity';links.new(alpha,alpha_node.inputs[0])
    shader=nodes.new('ShaderNodeBsdfPrincipled');links.new(color_node.outputs[0],shader.inputs['Base Color'])
    shader.inputs['Roughness'].default_value=1;links.new(metal.outputs['Color'],shader.inputs['Metallic'])
    links.new(alpha_node.outputs[0],shader.inputs['Alpha'])
    output=nodes.new('ShaderNodeOutputMaterial');links.new(shader.outputs[0],output.inputs['Surface'])
    if hasattr(material,'surface_render_method'):material.surface_render_method='BLENDED'
    material['agr_unused_source_bindings']='$SELFILLUM,$SELFILLUMTINT,$DETAILOFFSETY,$HIGHLIGHT,$ALPHATEST are not read by the pinned compiled material'
    return material


def build_mesh(name,geometry,unit_scale):
    """Keep visible source triangles; repeated-index faces have no surface.

    Blender 5.2 custom-normal construction can crash on repeated-index faces.
    Do not use mesh.validate here: it can also rewrite other source topology.
    Distinct-index faces, including coincident positions, remain untouched.
    """
    import bpy
    faces=geometry['indices'].tolist()
    visible=[face for face in faces if len(set(face))==3]
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata((geometry['positions']*unit_scale).tolist(),[],visible);mesh.update()
    uv=mesh.uv_layers.new(name='UVMap')
    for loop in mesh.loops:
        u,v=geometry['uv'][loop.vertex_index];uv.data[loop.index].uv=(float(u),1-float(v))
    for polygon in mesh.polygons:polygon.use_smooth=True
    mesh.normals_split_custom_set_from_vertices(geometry['normals'].tolist())
    mesh['agr_source_triangles']=len(faces)
    mesh['agr_repeated_index_triangles_removed']=len(faces)-len(visible)
    return mesh


def build_particle(name,renderer,assets,samples,simulation,collection,texture,unit_scale):
    import bpy
    from mathutils import Matrix
    from .blender_effects import _linear
    from .particle_flashbang_sphere import MODEL,validate_samples,material_profile
    descriptor=assets['models'][MODEL]
    geometry,matrices=validate_samples(renderer,descriptor,samples,unit_scale)
    texnames,floats,vectors=material_profile(descriptor['material'])
    mesh=build_mesh(name,geometry,unit_scale)
    obj=bpy.data.objects.new(name,mesh);collection.objects.link(obj);obj.rotation_mode='QUATERNION';obj.visible_shadow=False
    mesh.materials.append(build_material(name,texnames,floats,vectors,texture))
    scene=bpy.context.scene;step=(scene.render.fps/scene.render.fps_base)/simulation['tick_rate'];previous=None
    for (tick,_,particle),matrix in zip(samples,matrices):
        frame=1+(tick-simulation['start_tick'])*step;obj.matrix_world=Matrix(matrix.tolist())
        if previous is not None and obj.rotation_quaternion.dot(previous)<0:obj.rotation_quaternion.negate()
        previous=obj.rotation_quaternion.copy()
        for prop in ('location','rotation_quaternion','scale'):obj.keyframe_insert(prop,frame=frame)
        obj.color=(*particle['color'],particle['alpha']*particle['alpha_alternate']);obj.keyframe_insert('color',frame=frame)
        obj.hide_render=False;obj.hide_viewport=False
        for prop in ('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame)
    for tick in (samples[0][0]-1,samples[-1][0]+1):
        frame=1+(tick-simulation['start_tick'])*step;obj.hide_render=True;obj.hide_viewport=True
        for prop in ('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame)
    _linear(obj)
    obj['agr_model_particle_policy']=descriptor['policy'];obj['agr_model_resource']=descriptor['resource']
    obj['agr_model_source_sha256']=descriptor['source_sha256'];obj['agr_model_geometry_sha256']=descriptor['geometry_sha256']
    obj['agr_model_morph_count']=0;obj['agr_model_animation_driver']='static_baked_source_root'
    return obj,['Flashbang sphere translucent PBR is approximate: packed normals/roughness, exact Source lighting, fog and depth behavior are not reconstructed.']
