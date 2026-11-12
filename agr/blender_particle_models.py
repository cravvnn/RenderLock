"""Animated model particles with bounded morph geometry and an explicit PBR approximation."""
import math
import numpy as np
from pathlib import Path
from .installed_effects import UnsupportedEffect
from .particle_model_assets import validate_model,animation_rate
from .particle_model_geometry import read_model_geometry,morph_keyframes
from .particle_model_math import model_matrix


def _parameters(authored,group,value_key):
    rows=authored.get(group,[])
    if not isinstance(rows,list) or len(rows)>256:
        raise UnsupportedEffect('Invalid model particle material parameter table')
    result={}
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={'m_name',value_key}
                or not isinstance(row['m_name'],str) or row['m_name'] in result):
            raise UnsupportedEffect('Duplicate or unsupported model particle material parameter')
        result[row['m_name']]=row[value_key]
    return result


def material_profile(authored):
    """Recognize the direct attribute lookups used by the emissive bat material.

    This is a bounded Blender response, not an extension of the qualified Source
    constant-expression VM. Other runtime expressions remain unsupported.
    """
    if authored.get('m_shaderName')!='pbr.vfx':raise UnsupportedEffect('Unqualified model particle shader')
    expected={'g_flSelfIllumScale1':[25,22,66,211,186,0],
              'g_vSelfIllumTint1':[25,125,247,225,7,0]}
    programs=_parameters(authored,'m_dynamicParams','m_value')
    dynamic=programs
    particle_tint=(len(dynamic)==2 and programs==expected and authored.get('m_renderAttributesUsed')==['$SELFILLUM','$ILLUMCOLOR'])
    static_red=(len(dynamic)==1 and programs=={'g_flSelfIllumScale1':expected['g_flSelfIllumScale1']}
                and authored.get('m_renderAttributesUsed')==['$SELFILLUM'])
    if (not (particle_tint or static_red) or authored.get('m_dynamicTextureParams')):
        raise UnsupportedEffect('Unqualified model particle runtime material expression')
    ints=_parameters(authored,'m_intParams','m_nValue')
    if any(type(value) is not int for value in ints.values()):
        raise UnsupportedEffect('Invalid model particle integer material parameter')
    accepted={'F_OVERRIDE_BLOOM_AMOUNT','F_SELF_ILLUM','g_bMaskColorTint1','g_bMaskVertexColorTint1',
        'g_nScaleTexCoord2UByModelScaleAxis','g_nScaleTexCoord2UByModelScaleOrigin',
        'g_nScaleTexCoord2VByModelScaleAxis','g_nScaleTexCoord2VByModelScaleOrigin',
        'g_nScaleTexCoordUByModelScaleAxis','g_nScaleTexCoordUByModelScaleOrigin',
        'g_nScaleTexCoordVByModelScaleAxis','g_nScaleTexCoordVByModelScaleOrigin','g_nTextureColorTintMode1'}
    if static_red:accepted.add('F_TRANSLUCENT')
    expected_ints={key:(1 if key in {'F_OVERRIDE_BLOOM_AMOUNT','F_SELF_ILLUM','g_bMaskColorTint1','g_bMaskVertexColorTint1','F_TRANSLUCENT'}
        else 2 if key in {'g_nScaleTexCoord2VByModelScaleOrigin','g_nScaleTexCoordVByModelScaleOrigin'} else 0) for key in accepted}
    if set(ints)-accepted or any(value!=expected_ints[key] for key,value in ints.items()) or ints.get('F_SELF_ILLUM')!=1:
        raise UnsupportedEffect('Unqualified model particle material features')
    vectors=_parameters(authored,'m_vectorParams','m_value')
    if any(not isinstance(v,list) or len(v)!=4 or any(type(c) not in (int,float) or not math.isfinite(c) for c in v) for v in vectors.values()):
        raise UnsupportedEffect('Invalid model particle material vector')
    defaults={'g_vAlbedoContrastSaturationBrightness1':[1,1,1,0],
        'g_vAlbedoScrollSpeed1':[0,0,0,0],'g_vNormalAndRoughnessScrollSpeed1':[0,0,0,0],
        'g_vSelfIllumScrollSpeed1':[0,0,0,0],'g_vSelfIllumFresnelMaskTint1':[1,1,1,0],
        'g_vColorTint1':[1,1,1,0],'g_vSelfIllumTint1':([1,0,0,0] if static_red else [1,1,1,0]),'TextureColor1':[1,1,1,0]}
    if set(vectors)-set(defaults)-{'TextureRoughness1'} or any(vectors.get(k,v)!=v for k,v in defaults.items()):
        raise UnsupportedEffect('Unqualified model particle material vector response')
    floats=_parameters(authored,'m_floatParams','m_flValue')
    allowed={'g_flAlbedoScrollQuantize1','g_flNormalAndRoughnessScrollQuantize1','g_flOverrideBloomAmount1',
        'g_flSelfIllumAlbedoFactor1','g_flSelfIllumFresnelMaskExponent','g_flSelfIllumScale1',
        'g_flSelfIllumScrollQuantize1','g_fVertexColorStrength1'}
    if static_red:allowed.update(('g_flAlphaAnglePower1','g_flOpacityScale1'))
    if (set(floats)-allowed or any(type(v) not in (int,float) or not math.isfinite(v) for v in floats.values())
            or any(floats.get(k,0)!=0 for k in ('g_flAlbedoScrollQuantize1','g_flNormalAndRoughnessScrollQuantize1','g_flSelfIllumScrollQuantize1'))
            or floats.get('g_fVertexColorStrength1',1)!=1 or not 0<=floats.get('g_flSelfIllumAlbedoFactor1',0)<=1
            or floats.get('g_flAlphaAnglePower1',0)!=0 or floats.get('g_flOpacityScale1',1)!=1):
        raise UnsupportedEffect('Unqualified model particle material scalar response')
    textures=_parameters(authored,'m_textureParams','m_pValue')
    wanted={'g_tColor','g_tNormalRoughness','g_tSelfIllumMask','g_tTintMask'}
    if static_red:wanted.update(('g_tAmbientOcclusion','g_tMetalness'))
    if set(textures)!=wanted:
        raise UnsupportedEffect('Unqualified model particle texture set')
    floats['_particle_emission_tint']=particle_tint;floats['_translucent']=bool(ints.get('F_TRANSLUCENT'))
    return textures,floats


def validate_model_samples(renderer,descriptor,samples,unit_scale=.0254):
    """Refuse absent attributes and unsupported playback before allocating objects."""
    if type(unit_scale) not in (int,float) or not math.isfinite(unit_scale) or unit_scale<=0:
        raise UnsupportedEffect('Invalid model scene units')
    geometry=read_model_geometry(Path(descriptor['directory'])/'model.glb')
    animation=descriptor['animations'][0]
    if (geometry['animation_name']!=animation['name'] or len(geometry['times'])!=animation['frames']
            or not np.allclose(geometry['times'],np.arange(animation['frames'])/animation['fps'],atol=2e-7,rtol=0)):
        raise UnsupportedEffect('Particle morph animation metadata differs from geometry')
    if any(type(p.get('second_sequence')) is not int or p['second_sequence']!=0
           or type(p.get('sequence')) is not int or p['sequence']!=0 for _,_,p in samples):
        raise UnsupportedEffect('Unqualified or uncaptured model particle sequence selector')
    manual=renderer.get('m_bManualAnimFrame',False)
    schedule=morph_keyframes(geometry,samples,animation_rate(renderer),animation['fps'],manual=manual)
    matrices=[]
    for _,_,particle in samples:
        matrix=model_matrix(particle,renderer);matrix[:3,3]*=unit_scale
        matrices.append(matrix)
        value=particle.get('scratch_float0')
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise UnsupportedEffect('Uncaptured or invalid model self-illumination field')
    return geometry,schedule,matrices


def build_morph_curves(shape_keys, keys, schedule, start_tick, frame_step):
    """Bulk-write keys without Blender's near-frame deduplication.

    Both keyframe_insert and FCurve.update merge close authored breakpoints.
    Sort and recalculate handles explicitly, preserving distinct float32 times.
    """
    import bpy
    from .particle_model_geometry import encode_morph_curves
    frames,values,encoding=encode_morph_curves(schedule,start_tick,frame_step)
    action=bpy.data.actions.new(shape_keys.name+' | authored morph breakpoints')
    shape_keys.animation_data_create()
    if hasattr(action,'slots'):
        slot=action.slots.new(id_type='KEY',name=shape_keys.name)
        curves=action.layers.new('Authored poses').strips.new(type='KEYFRAME').channelbags.new(slot).fcurves
        shape_keys.animation_data.action=action;shape_keys.animation_data.action_slot=slot
    else:
        shape_keys.animation_data.action=action;curves=action.fcurves
    for index,key in enumerate(keys):
        curve=curves.new(key.path_from_id('value'),index=0)
        curve.keyframe_points.add(len(frames))
        curve.keyframe_points.foreach_set('co',np.column_stack((frames,values[:,index])).ravel())
        for point in curve.keyframe_points:point.interpolation='LINEAR'
        curve.update_autoflags(shape_keys)
        curve.keyframe_points.sort();curve.keyframe_points.handles_recalc()
        if len(curve.keyframe_points)!=len(frames):
            raise UnsupportedEffect('Blender discarded an authored model morph breakpoint')
    for key,value in encoding.items():shape_keys['agr_morph_'+key]=value
    return encoding


def build_model_particle(name,renderer,assets,samples,simulation,collection,texture,unit_scale):
    from .particle_flashbang_sphere import is_renderer
    if is_renderer(renderer):
        from .blender_flashbang_sphere import build_particle
        return build_particle(name,renderer,assets,samples,simulation,collection,texture,unit_scale)
    import bpy
    from mathutils import Matrix
    from .blender_effects import _linear
    descriptor=validate_model(renderer,assets);authored=descriptor['material']
    geometry,schedule,matrices=validate_model_samples(renderer,descriptor,samples,unit_scale)
    texnames,floats=material_profile(authored)
    mesh=bpy.data.meshes.new(name)
    vertices=geometry['positions']*unit_scale
    mesh.from_pydata(vertices.tolist(),[],geometry['indices'].tolist());mesh.update()
    uv=mesh.uv_layers.new(name='UVMap')
    for loop in mesh.loops:
        u,v=geometry['uv'][loop.vertex_index];uv.data[loop.index].uv=(float(u),1-float(v))
    for poly in mesh.polygons:poly.use_smooth=True
    obj=bpy.data.objects.new(name,mesh);collection.objects.link(obj);obj.rotation_mode='QUATERNION'
    obj.visible_shadow=False
    obj.shape_key_add(name='Basis')
    keys=[]
    for index,delta in enumerate(geometry['morphs']):
        key=obj.shape_key_add(name=f'Authored morph {index}')
        key.slider_min=-10;key.slider_max=10
        key.data.foreach_set('co',(vertices+delta*unit_scale).astype(np.float32).ravel());keys.append(key)
    mat=bpy.data.materials.new(name+' | installed emissive material');mat.use_nodes=True
    mat['agr_model_particle_material']='bounded_pbr_emission_approximation'
    nodes=mat.node_tree.nodes;links=mat.node_tree.links;nodes.clear()
    base=nodes.new('ShaderNodeTexImage');base.image=texture(texnames['g_tColor'],0)
    mask=nodes.new('ShaderNodeTexImage');mask.image=texture(texnames['g_tSelfIllumMask'],0).copy()
    mask.image.colorspace_settings.name='Non-Color';mask.image.pack()
    tintmask=nodes.new('ShaderNodeTexImage');tintmask.image=texture(texnames['g_tTintMask'],0).copy()
    tintmask.image.colorspace_settings.name='Non-Color';tintmask.image.pack()
    info=nodes.new('ShaderNodeObjectInfo')
    tint=nodes.new('ShaderNodeMixRGB');tint.blend_type='MULTIPLY'
    links.new(tintmask.outputs['Color'],tint.inputs[0]);links.new(base.outputs['Color'],tint.inputs[1]);links.new(info.outputs['Color'],tint.inputs[2])
    shader=nodes.new('ShaderNodeBsdfPrincipled');links.new(tint.outputs[0],shader.inputs['Base Color'])
    shader.inputs['Roughness'].default_value=.5
    albedo=nodes.new('ShaderNodeMixRGB');albedo.inputs[0].default_value=floats.get('g_flSelfIllumAlbedoFactor1',0)
    albedo.inputs[1].default_value=(1,1,1,1);links.new(base.outputs['Color'],albedo.inputs[2])
    glow=nodes.new('ShaderNodeMixRGB');glow.blend_type='MULTIPLY';glow.inputs[0].default_value=1
    links.new(albedo.outputs[0],glow.inputs[1])
    if floats['_particle_emission_tint']:links.new(info.outputs['Color'],glow.inputs[2])
    else:glow.inputs[2].default_value=(1,0,0,1)
    links.new(glow.outputs[0],shader.inputs['Emission Color'])
    strength=nodes.new('ShaderNodeMath');strength.operation='MULTIPLY';links.new(mask.outputs['Color'],strength.inputs[0])
    links.new(strength.outputs[0],shader.inputs['Emission Strength'])
    alpha=info.outputs['Alpha']
    if floats['_translucent']:
        opacity=nodes.new('ShaderNodeMath');opacity.operation='MULTIPLY'
        links.new(alpha,opacity.inputs[0]);links.new(base.outputs['Alpha'],opacity.inputs[1]);alpha=opacity.outputs[0]
    links.new(alpha,shader.inputs['Alpha'])
    output=nodes.new('ShaderNodeOutputMaterial');links.new(shader.outputs[0],output.inputs['Surface'])
    if hasattr(mat,'surface_render_method'):mat.surface_render_method='BLENDED'
    mesh.materials.append(mat)
    scene=bpy.context.scene
    frame_step=(scene.render.fps/scene.render.fps_base)/simulation['tick_rate']
    previous_rotation=None
    for (tick,_,particle),matrix in zip(samples,matrices):
        frame=1+(tick-simulation['start_tick'])*frame_step
        obj.matrix_world=Matrix(matrix.tolist())
        if previous_rotation is not None and obj.rotation_quaternion.dot(previous_rotation)<0:
            obj.rotation_quaternion.negate()
        previous_rotation=obj.rotation_quaternion.copy()
        for prop in ('location','rotation_quaternion','scale'):obj.keyframe_insert(prop,frame=frame)
        alpha=particle['alpha']*particle['alpha_alternate']
        obj.color=(*particle['color'],max(0,alpha));obj.keyframe_insert('color',frame=frame)
        value=particle.get('scratch_float0')
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise UnsupportedEffect('Uncaptured or invalid model self-illumination field')
        strength.inputs[1].default_value=value;strength.inputs[1].keyframe_insert('default_value',frame=frame)
        obj.hide_render=False;obj.hide_viewport=False
        for prop in ('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame)
    build_morph_curves(mesh.shape_keys,keys,schedule,simulation['start_tick'],frame_step)
    for tick in (samples[0][0]-1,samples[-1][0]+1):
        frame=1+(tick-simulation['start_tick'])*frame_step;obj.hide_render=True;obj.hide_viewport=True
        for prop in ('hide_render','hide_viewport'):obj.keyframe_insert(prop,frame=frame)
    _linear(obj);_linear(mesh.shape_keys);_linear(mat.node_tree)
    obj['agr_model_particle_policy']=descriptor['policy'];obj['agr_model_resource']=descriptor['resource']
    obj['agr_model_source_sha256']=descriptor['source_sha256'];obj['agr_model_geometry_sha256']=descriptor['geometry_sha256']
    obj['agr_model_morph_count']=len(keys)
    obj['agr_model_animation_driver']='captured_manual_frame38' if renderer.get('m_bManualAnimFrame') else 'age_rate_fps_with_authored_breakpoints'
    return obj,[
        ('Model morphs interpolate captured manual-frame field 38 while preserving authored pose breakpoints; native animation scheduling is not qualified.' if renderer.get('m_bManualAnimFrame')
         else 'Model morphs preserve authored pose breakpoints between captured age samples; native animation scheduling is not qualified.'),
        'Model PBR/emission is approximate: normal/roughness packing, AO/metal maps, Fresnel response, Source lighting, bloom and decals are not reconstructed.']
