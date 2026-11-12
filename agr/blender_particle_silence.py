"""Pinned Silence cylinder with keyed inputs and reference alpha/PBR response.

Uses a normal-frame / component-scale / Euler hierarchy, so anisotropic scale is
not silently decomposed into one lossy Blender TRS. Material phase and Source
lighting remain explicitly approximate. Construction is synchronous; ownership
and keyed-frame composition are covered by ModelTransformHierarchy.tla.
"""
import math
from .particle_silence_model import silence_material_profile,material_inputs,POLICY
from .blender_particle_model_transform import create_model_hierarchy,keyframe_model_transform,linearize_model_hierarchy

def build_silence_particle(name,renderer,descriptor,geometry,samples,simulation,collection,texture,unit_scale):
    from .particle_silence_model import validate_samples
    validated,_=validate_samples(renderer,descriptor,samples,simulation,unit_scale)
    # Use the fully revalidated byte-bound geometry, not a caller's mutable mesh.
    geometry=validated
    import bpy
    from .blender_effects import _linear
    texnames,floats,vectors=silence_material_profile(descriptor,renderer)
    inputs=[material_inputs(p,simulation)for _,_,p in samples]
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata((geometry['positions']*unit_scale).tolist(),[],geometry['indices'].tolist());mesh.update()
    uv=mesh.uv_layers.new(name='UVMap')
    for loop in mesh.loops:
        u,v=geometry['uv'][loop.vertex_index];uv.data[loop.index].uv=(float(u),1-float(v))
    for polygon in mesh.polygons:polygon.use_smooth=True
    mesh.normals_split_custom_set_from_vertices(geometry['normals'].tolist())
    obj=bpy.data.objects.new(name,mesh);collection.objects.link(obj);obj.visible_shadow=False
    hierarchy=create_model_hierarchy(obj,renderer,collection)
    mat=bpy.data.materials.new(name+' | Silence startup');mat.use_nodes=True;mesh.materials.append(mat)
    mat['agr_model_particle_material']=POLICY
    nodes,links=mat.node_tree.nodes,mat.node_tree.links;nodes.clear()
    def vector(operation,a,b):
        node=nodes.new('ShaderNodeVectorMath');node.operation=operation
        for value,target in((a,node.inputs[0]),(b,node.inputs[1])):
            if isinstance(value,(tuple,list)):target.default_value=value
            else:links.new(value,target)
        return node.outputs['Vector']
    def scalar(operation,a,b):
        node=nodes.new('ShaderNodeMath');node.operation=operation
        for value,target in((a,node.inputs[0]),(b,node.inputs[1])):
            if isinstance(value,(int,float)):target.default_value=value
            else:links.new(value,target)
        return node.outputs[0]
    coord=nodes.new('ShaderNodeTexCoord')
    source=vector('ADD',vector('MULTIPLY',coord.outputs['UV'],(1,-1,1)),(0,1,0))
    time=nodes.new('ShaderNodeValue');time.name='Silence replay time'
    scroll=nodes.new('ShaderNodeCombineXYZ');scroll.name='Silence OFFSET scroll speed'
    def mapped(prefix):
        speed=vectors.get('g_v'+prefix+'ScrollSpeed1',[0,0,0,0])
        scale=vectors.get('g_v'+prefix+'TexcoordScale1',[1,1,0,0])
        offset=vectors.get('g_v'+prefix+'TexcoordOffset1',[0,0,0,0])
        angle=floats.get('g_fl'+prefix+'TexcoordRotation1',0)
        dynamic=prefix=='Translucent0'
        if prefix.startswith('Translucent'):
            suffix=prefix[len('Translucent'):]
            speed=vectors['g_vTranslucentScrollSpeed'+suffix+'_1']
            scale=vectors['g_vTranslucentTexcoordScale'+suffix+'_1']
            offset=vectors['g_vTranslucentTexcoordOffset'+suffix+'_1']
            angle=floats['g_flTranslucentTexcoordRotation'+suffix+'_1']
        drift=nodes.new('ShaderNodeCombineXYZ')
        for axis in(0,1):
            if dynamic:
                # SeparateXYZ reads the keyed vector output, not a socket default.
                split=nodes.get('Silence scroll components')
                if split is None:
                    split=nodes.new('ShaderNodeSeparateXYZ');split.name='Silence scroll components';links.new(scroll.outputs[0],split.inputs[0])
                velocity=split.outputs[axis]
            links.new(scalar('FRACT',scalar('MULTIPLY',time.outputs[0],velocity if dynamic else speed[axis]),0),drift.inputs[axis])
        centre=vector('SUBTRACT',vector('ADD',source,drift.outputs[0]),(.5,.5,0))
        rotate=nodes.new('ShaderNodeVectorRotate');rotate.rotation_type='AXIS_ANGLE';rotate.inputs['Axis'].default_value=(0,0,1)
        rotate.inputs['Angle'].default_value=math.radians(angle);links.new(centre,rotate.inputs['Vector'])
        placed=vector('ADD',vector('MULTIPLY',rotate.outputs['Vector'],(*scale[:2],1)),(.5+offset[0],.5+offset[1],0))
        result=vector('ADD',vector('MULTIPLY',placed,(1,-1,1)),(0,1,0));result.node.label=prefix+' sampled UV'
        return result
    def image(resource,coordinate,raw=False):
        node=nodes.new('ShaderNodeTexImage');node.image=texture(resource,0)
        if raw:
            node.image=node.image.copy();node.image.colorspace_settings.name='Non-Color';node.image.pack()
        links.new(coordinate,node.inputs['Vector']);return node
    opacity=image(texnames['g_tColor'],mapped('Translucent0'))
    alternate=image(texnames['g_tAltTranslucency'],mapped('Translucent1'),True)
    split=nodes.new('ShaderNodeSeparateXYZ');links.new(alternate.outputs['Color'],split.inputs[0])
    info=nodes.new('ShaderNodeObjectInfo')
    alpha=scalar('MULTIPLY',scalar('MULTIPLY',opacity.outputs['Alpha'],split.outputs['X']),info.outputs['Alpha'])
    alpha=scalar('MULTIPLY',alpha,floats['g_flOpacityScale1'])
    tint=nodes.new('ShaderNodeRGB');tint.name='Silence COLOR and SELFILLUMTINT'
    strength=nodes.new('ShaderNodeValue');strength.name='Silence SELFILLUM'
    albedo=image(texnames['g_tColor'],mapped('Albedo'))
    illum=image(texnames['g_tSelfIllumMask'],mapped('SelfIllum'),True)
    mask=nodes.new('ShaderNodeSeparateXYZ');links.new(illum.outputs['Color'],mask.inputs[0])
    surface=nodes.new('ShaderNodeBsdfPrincipled')
    links.new(vector('MULTIPLY',albedo.outputs['Color'],tint.outputs[0]),surface.inputs['Base Color'])
    surface.inputs['Metallic'].default_value=0;surface.inputs['Roughness'].default_value=vectors['TextureRoughness1'][0]
    # SelfIllumAlbedoFactor is zero in this pinned material: tint is independent
    # of the sampled albedo. Alpha is applied once by the outer transparent mix.
    links.new(tint.outputs[0],surface.inputs['Emission Color'])
    links.new(scalar('MULTIPLY',mask.outputs['X'],strength.outputs[0]),surface.inputs['Emission Strength'])
    transparent=nodes.new('ShaderNodeBsdfTransparent');mix=nodes.new('ShaderNodeMixShader')
    links.new(alpha,mix.inputs[0]);links.new(transparent.outputs[0],mix.inputs[1]);links.new(surface.outputs[0],mix.inputs[2])
    output=nodes.new('ShaderNodeOutputMaterial');links.new(mix.outputs[0],output.inputs['Surface'])
    if hasattr(mat,'surface_render_method'):mat.surface_render_method='BLENDED'
    scene=bpy.context.scene;old=scene.frame_current,scene.frame_subframe
    step=(scene.render.fps/scene.render.fps_base)/simulation['tick_rate']
    try:
        for(tick,_,particle),values in zip(samples,inputs):
            frame=1+(tick-simulation['start_tick'])*step;scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
            keyframe_model_transform(obj,particle,renderer,frame,unit_scale,hierarchy)
            obj.color=(*particle['color'],max(0,particle['alpha']*particle['alpha_alternate']));obj.keyframe_insert('color',frame=frame)
            tint.outputs[0].default_value=(*values['color'],1);tint.outputs[0].keyframe_insert('default_value',frame=frame)
            strength.outputs[0].default_value=values['glow'];strength.outputs[0].keyframe_insert('default_value',frame=frame)
            for axis in(0,1):scroll.inputs[axis].default_value=values['scroll'][axis];scroll.inputs[axis].keyframe_insert('default_value',frame=frame)
            time.outputs[0].default_value=tick/simulation['tick_rate'];time.outputs[0].keyframe_insert('default_value',frame=frame)
            obj.hide_render=False;obj.hide_viewport=False
            for prop in('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame)
        for tick in(samples[0][0]-1,samples[-1][0]+1):
            frame=1+(tick-simulation['start_tick'])*step;obj.hide_render=True;obj.hide_viewport=True
            for prop in('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame)
    finally:scene.frame_set(old[0],subframe=old[1])
    _linear(obj);_linear(mat.node_tree);linearize_model_hierarchy(hierarchy)
    for key in('resource','source_sha256','geometry_sha256'):obj['agr_model_'+key]=descriptor[key]
    obj['agr_model_particle_policy']=descriptor['policy'];obj['agr_model_morph_count']=0;obj['agr_model_animation_driver']='static_identity_root'
    mat['agr_material_unused_variables']='$ALPHATEST is bound but not read by the pinned compiled material'
    return obj,['Silence cylinder uses reference alpha/PBR shading: Source lighting, packed normal response, depth behavior and exact material-clock phase remain approximate.']
