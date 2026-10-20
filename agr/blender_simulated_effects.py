"""Bake explicit particle states into Blender; simulation and rendering are separate.

Input must include backend identity and diagnostics. This adapter currently renders
additive/alpha static-sheet sprites/trails and point lights, with a bounded
two-layer UV-distortion shader, and camera-sampled whole-system rope meshes.
It does not claim Source
shader or radiometric equivalence. Unsupported renderer settings fail explicitly.

Trail behavior follows ValveResourceFormat's RenderTrails implementation:
https://github.com/ValveResourceFormat/ValveResourceFormat (MIT), revision
42743fb93030c552f834fabb30708dad84d0cbf5.
UV distortion follows Renderer/Shaders/particle_spritecard.frag.slang at the same
revision: re-sample the prior layer with a signed RG offset, strength*.125*alpha.
"""
from __future__ import annotations
import json
import math
import hashlib
import re
from pathlib import Path
from .installed_effects import scalar, UnsupportedEffect


def texture_control_value(value, age, default=0):
    """Static/collection-age texture controls; never silently drop a provider."""
    if not isinstance(value, dict):
        return scalar(value, default=default)
    kind = value.get('m_nType', 'PF_TYPE_LITERAL')
    if kind == 'PF_TYPE_COLLECTION_AGE':
        result = age
    elif kind == 'PF_TYPE_LITERAL':
        result = value.get('m_flLiteralValue', default)
    else:
        raise UnsupportedEffect('Texture control provider ' + kind)
    mode = value.get('m_nMapType', 'PF_MAP_TYPE_DIRECT')
    if mode == 'PF_MAP_TYPE_MULT':
        result *= value.get('m_flMultFactor', 1)
    elif mode != 'PF_MAP_TYPE_DIRECT':
        raise UnsupportedEffect('Texture control mapping ' + mode)
    if not math.isfinite(result):
        raise UnsupportedEffect('Non-finite texture control')
    return result


def trail_dimensions(particle, renderer, step):
    delta = [a-b for a,b in zip(particle['previous'], particle['position'])]
    distance = math.sqrt(sum(v*v for v in delta))
    if distance < 1e-10:
        return 0., 0., [1.,0.,0.]
    multiplier = 1 if renderer.get('m_bIgnoreDT') or step <= 0 else 1/step
    length = particle['trail']*distance*multiplier*renderer.get('m_flLengthScale',1)
    length *= min(1.,particle['age']/max(1e-9,renderer.get('m_flLengthFadeInTime',0)))
    length = max(renderer.get('m_flMinLength',0),min(renderer.get('m_flMaxLength',2000),length))
    radius = min(particle['radius']*scalar(renderer.get('m_flRadiusScale'),default=1),
                 length*renderer.get('m_flConstrainRadiusToLengthRatio',1))
    return length,max(0.,radius),[v/distance for v in delta]


def texture_uv_parameters(controls, age, random_uv=(0., 0.)):
    """ParticleTextureLayer.PlaceCorner: rotate about .5, divide, add wrapped origin.

    Authored rotation is radians. Clamping is per vertex in Source/VRF, so a
    fragment-stage clamp is not equivalent and must not silently stand in for it.
    """
    scale=tuple(texture_control_value(controls.get('m_flFinalTextureScale'+axis),age,1) for axis in ('U','V'))
    if any(value==0 for value in scale):
        raise UnsupportedEffect('Zero particle texture scale')
    offset=tuple(texture_control_value(controls.get('m_flFinalTextureOffset'+axis),age)+random_uv[j]+.5
                 for j,axis in enumerate(('U','V')))
    origin=tuple(value-math.floor(value) for value in offset)
    rotation=texture_control_value(controls.get('m_flFinalTextureUVRotation'),age)
    return scale,origin,rotation


def build_texture_uv_nodes(nodes, links, coordinate, controls, samples, *, trail=False):
    """Construct the affine source transform and key its parameters per frame."""
    if controls.get('m_bClampUVs'):
        return build_clamped_texture_uv_nodes(nodes, links, coordinate, controls, samples, trail=trail)
    centre=nodes.new('ShaderNodeVectorMath');centre.operation='SUBTRACT'
    centre.inputs[1].default_value=(.5,.5,0);links.new(coordinate,centre.inputs[0])
    rotation=nodes.new('ShaderNodeVectorRotate');rotation.rotation_type='AXIS_ANGLE'
    rotation.inputs['Axis'].default_value=(0,0,1);links.new(centre.outputs['Vector'],rotation.inputs['Vector'])
    divide=nodes.new('ShaderNodeVectorMath');divide.operation='DIVIDE'
    links.new(rotation.outputs['Vector'],divide.inputs[0])
    origin=nodes.new('ShaderNodeVectorMath');origin.operation='ADD'
    links.new(divide.outputs['Vector'],origin.inputs[0])
    for frame,age,random_uv in samples:
        scale,wrapped,angle=texture_uv_parameters(controls,age,random_uv)
        divide.inputs[1].default_value=(*scale,1)
        origin.inputs[1].default_value=(*wrapped,0)
        rotation.inputs['Angle'].default_value=angle
        for socket in (divide.inputs[1],origin.inputs[1],rotation.inputs['Angle']):
            socket.keyframe_insert('default_value',frame=frame)
    return origin


def texture_uv_corners(controls, age, random_uv=(0., 0.)):
    """Place/clamp vertices before interpolation, matching VRF PlaceCorner."""
    scale, origin, angle = texture_uv_parameters(controls, age, random_uv)
    sin, cos = math.sin(angle), math.cos(angle)
    corners = []
    for u, v in ((0, 0), (1, 0), (0, 1), (1, 1)):
        x, y = u - .5, v - .5
        placed = ((x*cos-y*sin)/scale[0]+origin[0], (x*sin+y*cos)/scale[1]+origin[1])
        if not all(math.isfinite(value) for value in placed):
            raise UnsupportedEffect('Non-finite particle UV corner')
        corners.append(tuple(max(0., min(1., value)) for value in placed))
    return corners


def build_clamped_texture_uv_nodes(nodes, links, coordinate, controls, samples, *, trail):
    """Interpolate clamped corners across the renderer's two source triangles.

    particle_spritecard.vert.slang + triangle strip: sprites split along u+v=1;
    trails split along u=v. A fragment clamp or bilinear interpolation differs.
    """
    split = nodes.new('ShaderNodeSeparateXYZ'); links.new(coordinate, split.inputs[0])
    u, v = split.outputs['X'], split.outputs['Y']
    def op(operation, a, b):
        node = nodes.new('ShaderNodeMath'); node.operation = operation
        for index, value in enumerate((a, b)):
            if isinstance(value, (int, float)): node.inputs[index].default_value = value
            else: links.new(value, node.inputs[index])
        return node.outputs[0]
    if trail:
        weights = (op('SUBTRACT', 1, op('MAXIMUM', u, v)),
                   op('MAXIMUM', op('SUBTRACT', u, v), 0),
                   op('MAXIMUM', op('SUBTRACT', v, u), 0), op('MINIMUM', u, v))
    else:
        total = op('ADD', u, v)
        weights = (op('MAXIMUM', op('SUBTRACT', 1, total), 0),
                   op('MINIMUM', u, op('SUBTRACT', 1, v)),
                   op('MINIMUM', v, op('SUBTRACT', 1, u)),
                   op('MAXIMUM', op('SUBTRACT', total, 1), 0))
    corners = []
    for weight in weights:
        node = nodes.new('ShaderNodeVectorMath'); node.operation = 'SCALE'
        links.new(weight, node.inputs['Scale']); corners.append(node)
    for frame, age, random_uv in samples:
        for node, uv in zip(corners, texture_uv_corners(controls, age, random_uv)):
            node.inputs[0].default_value = (*uv, 0)
            node.inputs[0].keyframe_insert('default_value', frame=frame)
    result = corners[0]
    for node in corners[1:]:
        add = nodes.new('ShaderNodeVectorMath'); add.operation = 'ADD'
        links.new(result.outputs['Vector'], add.inputs[0]); links.new(node.outputs['Vector'], add.inputs[1])
        result = add
    return result


def static_texture_sequence(descriptor, sequence, resource):
    """Resolve against each layer's own sheet, as VRF sprites/trails do.

    MKS stores sequence IDs; only dense zero-based IDs establish the array
    indexing used by the renderer. Never infer an order for sparse IDs.
    """
    if type(sequence) is not int or sequence < 0:
        raise UnsupportedEffect('Invalid sprite sequence: '+resource)
    sequences=descriptor.get('sequences',{})
    if descriptor.get('is_sprite_sheet'):
        if not sequences or set(sequences)!={str(i) for i in range(len(sequences))}:
            raise UnsupportedEffect('Ambiguous sprite sequence ordering: '+resource)
        sequence %= len(sequences)
    else:
        sequence=0
    selected=sequences.get(str(sequence))
    if not selected or selected.get('status')!='supported' or len(selected.get('frames',[]))!=1:
        raise UnsupportedEffect('Animated or unsupported sprite sequence: '+resource)
    return selected


def collect_particle_tracks(simulation):
    """Keep repeated resources distinct by stable simulation-tree path.

    Legacy states without paths remain usable only when resource names uniquely
    identify systems. Ambiguous legacy data must be regenerated, never merged.
    """
    tracks = {}
    instance_ids = set()
    for instance in simulation['instances']:
        if instance['id'] in instance_ids:
            raise ValueError('Duplicate simulated instance identity')
        instance_ids.add(instance['id'])
        identities = {}
        path_mode = None
        previous_tick = None
        for frame in instance['frames']:
            tick = frame['tick']
            if type(tick) is not int or (previous_tick is not None and tick <= previous_tick):
                raise ValueError('Invalid particle sample tick order')
            if previous_tick is not None and tick != previous_tick + 1:
                raise ValueError('Missing particle simulation tick; regenerate complete states')
            previous_tick = tick
            frame_systems = set()
            for system in frame['systems']:
                resource = system['name'].removesuffix('_c')
                has_path = 'system_path' in system
                if path_mode is not None and has_path != path_mode:
                    raise ValueError('Mixed legacy and tree-path particle identities')
                path_mode = has_path
                path = system.get('system_path', 'resource:' + resource)
                if has_path and (not isinstance(path, str) or not re.fullmatch(r'root(?:/(?:0|[1-9][0-9]*))*', path)):
                    raise ValueError('Invalid particle system tree path')
                if path in frame_systems:
                    raise ValueError('Duplicate or ambiguous particle system identity; regenerate simulation')
                frame_systems.add(path)
                if path in identities and identities[path] != resource:
                    raise ValueError('Particle system resource changed at a stable tree path')
                identities[path] = resource
                particle_ids = set()
                for particle in system['particles']:
                    particle_id = particle['id']
                    if type(particle_id) is not int or particle_id < 0 or particle_id in particle_ids:
                        raise ValueError('Invalid or duplicate particle identity in system sample')
                    particle_ids.add(particle_id)
                    key = (instance['id'], path, resource, particle_id)
                    if key in tracks and tracks[key][-1][0] != tick - 1:
                        raise ValueError('Particle identity reappears after death or missing states; cannot bridge lifetimes')
                    tracks.setdefault(key, []).append((tick, system['step'], particle))
    return tracks


def validate_render_samples(tracks):
    """Reject malformed numeric states before creating Blender data blocks."""
    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value)
    for samples in tracks.values():
        for tick, step, particle in samples:
            if not numeric(step) or step < 0:
                raise ValueError('Invalid particle simulation step')
            for field in ('position', 'previous', 'normal', 'rotation', 'color'):
                vector = particle.get(field)
                if (not isinstance(vector, (list, tuple)) or len(vector) != 3
                        or not all(numeric(v) for v in vector)):
                    raise ValueError('Invalid particle vector: ' + field)
            for field in ('radius', 'alpha', 'alpha_alternate', 'age', 'trail'):
                if not numeric(particle.get(field)):
                    raise ValueError('Invalid particle scalar: ' + field)
            if particle['age'] < 0 or type(particle.get('sequence')) is not int or particle['sequence'] < 0:
                raise ValueError('Invalid particle age or sprite sequence')


def collect_system_samples(simulation):
    """Retain live array order and empty samples for whole-system renderers."""
    systems = {}
    for instance in simulation['instances']:
        for frame in instance['frames']:
            for system in frame['systems']:
                resource = system['name'].removesuffix('_c')
                path = system.get('system_path', 'resource:'+resource)
                key = (instance['id'], path, resource)
                systems.setdefault(key, []).append((frame['tick'], system['particles']))
    return systems


def build_simulated_effects(simulation, assets, scene, camera, unit_scale=.0254):
    import bpy
    from mathutils import Vector, Quaternion
    from .blender_effects import _linear
    if simulation.get('schema_version') != 1 or not simulation.get('backend'):
        raise ValueError('Particle simulation schema/backend identity missing')
    if simulation.get('warnings'):
        raise ValueError('Particle simulation has unresolved diagnostics')
    output_fps = scene.render.fps / scene.render.fps_base
    frame_step = output_fps / simulation['tick_rate']
    tracks=collect_particle_tracks(simulation)
    validate_render_samples(tracks)
    collection=bpy.data.collections.new('AGR Simulated Effects')
    scene.collection.children.link(collection)
    materials={};images={};omissions=[]
    resource_paths={}
    for instance,path,resource,_ in tracks:
        resource_paths.setdefault((instance,resource),set()).add(path)
    def texture(resource,sequence):
        descriptor=assets['textures'][resource]
        seq=static_texture_sequence(descriptor,sequence,resource)
        path=Path(descriptor['directory'])/seq['frames'][0]['image']
        key=str(path.resolve())
        if key not in images:
            from .blender_images import load_packed_image
            images[key]=load_packed_image(key,check_existing=False)
        return images[key]
    births={i['id']:i['birth'] for i in simulation['instances']}
    def material(instance,system_path,particle_id,resource,index,renderer,sequence,samples):
        key=(instance,system_path,particle_id,resource,index,sequence)
        if key in materials:return materials[key]
        blend=renderer.get('m_nOutputBlendMode','PARTICLE_OUTPUT_BLEND_MODE_ALPHA')
        if blend not in ('PARTICLE_OUTPUT_BLEND_MODE_ADD','PARTICLE_OUTPUT_BLEND_MODE_ALPHA'):
            raise UnsupportedEffect('Unsupported simulated sprite blend '+blend)
        mat=bpy.data.materials.new('AGR simulated | '+Path(resource).stem);mat.use_nodes=True
        if hasattr(mat,'surface_render_method'):mat.surface_render_method='BLENDED'
        nodes=mat.node_tree.nodes;links=mat.node_tree.links;nodes.clear()
        info=nodes.new('ShaderNodeObjectInfo');coord=nodes.new('ShaderNodeTexCoord')
        color=info.outputs['Color'];alpha=info.outputs['Alpha']
        layers=[t for t in renderer.get('m_vecTexturesInput',[]) if t.get('m_hTexture')]
        def mapped_texture(tex, layer_index, raw=False):
            image=nodes.new('ShaderNodeTexImage');image.image=texture(tex['m_hTexture'],sequence)
            if raw:
                raw_key=('raw',image.image.name)
                if raw_key not in images:
                    images[raw_key]=image.image.copy();images[raw_key].colorspace_settings.name='Non-Color'
                image.image=images[raw_key]
            controls=tex.get('m_TextureControls',{})
            random_uv=(0.,0.)
            if controls.get('m_bRandomizeOffsets'):
                # Retain prior substitute offsets for unambiguous resources.
                identity=instance if len(resource_paths[(instance,resource)])==1 else instance+'/'+system_path
                digest=hashlib.sha256(f'{identity}/{particle_id}/{index}/{layer_index}'.encode()).digest()
                random_uv=tuple(int.from_bytes(digest[j:j+4],'little')/2**32 for j in (0,4))
                omissions.append(resource+': UV random offsets use a deterministic substitute seed; recorded engine seed unavailable')
            uv_samples=[(1+(tick-simulation['start_tick'])*frame_step,
                         (tick-births[instance])/simulation['tick_rate'],random_uv) for tick,_,_ in samples]
            mapping=build_texture_uv_nodes(nodes,links,coord.outputs['UV'],controls,uv_samples,
                                           trail=renderer['_class']=='C_OP_RenderTrails')
            links.new(mapping.outputs['Vector'],image.inputs['Vector'])
            return image,mapping
        for layer_index,tex in enumerate(layers):
            if not tex.get('m_hTexture'):continue
            texture_type=tex.get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')
            if texture_type=='SPRITECARD_TEXTURE_UVDISTORTION':
                if layer_index!=1 or len(layers)!=2 or layers[0].get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')!='SPRITECARD_TEXTURE_COLOR':
                    raise UnsupportedEffect('UV distortion requires a preceding color layer')
                continue  # Consumed with its preceding color layer below.
            if texture_type!='SPRITECARD_TEXTURE_COLOR':
                raise UnsupportedEffect('Unsupported texture type '+texture_type)
            if tex.get('m_bReplaceTextureWithGradient') or tex.get('m_nTextureBlendMode','SPRITECARD_TEXTURE_BLEND_MULTIPLY')!='SPRITECARD_TEXTURE_BLEND_MULTIPLY':
                raise UnsupportedEffect('Unsupported simulated texture-layer operation')
            image,mapping=mapped_texture(tex,layer_index)
            if layer_index+1<len(layers) and layers[layer_index+1].get('m_nTextureType')=='SPRITECARD_TEXTURE_UVDISTORTION':
                distortion=layers[layer_index+1]
                if layer_index!=0 or len(layers)!=2 or distortion.get('m_nTextureBlendMode')!='SPRITECARD_TEXTURE_BLEND_REPLACE':
                    raise UnsupportedEffect('Only a color layer followed by replacing UV distortion is implemented')
                noise,_=mapped_texture(distortion,layer_index+1,raw=True)
                shift=nodes.new('ShaderNodeVectorMath');shift.operation='SUBTRACT';shift.inputs[1].default_value=(.5,.5,0)
                links.new(noise.outputs['Color'],shift.inputs[0])
                power=nodes.new('ShaderNodeMath');power.operation='MULTIPLY';links.new(noise.outputs['Alpha'],power.inputs[0]);power.inputs[1].default_value=-.25*scalar(distortion.get('m_TextureControls',{}).get('m_flDistortion'),default=0)
                offset=nodes.new('ShaderNodeVectorMath');offset.operation='SCALE';links.new(shift.outputs['Vector'],offset.inputs[0]);links.new(power.outputs[0],offset.inputs['Scale'])
                add_uv=nodes.new('ShaderNodeVectorMath');add_uv.operation='ADD';links.new(mapping.outputs['Vector'],add_uv.inputs[0]);links.new(offset.outputs['Vector'],add_uv.inputs[1]);links.new(add_uv.outputs['Vector'],image.inputs['Vector'])
            mix=nodes.new('ShaderNodeMixRGB');mix.blend_type='MULTIPLY';mix.inputs[0].default_value=1
            links.new(color,mix.inputs[1]);links.new(image.outputs['Color'],mix.inputs[2]);color=mix.outputs[0]
            mult=nodes.new('ShaderNodeMath');mult.operation='MULTIPLY';links.new(alpha,mult.inputs[0]);links.new(image.outputs['Alpha'],mult.inputs[1]);alpha=mult.outputs[0]
        power=nodes.new('ShaderNodeMath');power.operation='MULTIPLY';power.inputs[0].default_value=1
        if blend=='PARTICLE_OUTPUT_BLEND_MODE_ADD':links.new(alpha,power.inputs[0])
        power.inputs[1].default_value=scalar(renderer.get('m_flOverbrightFactor'),default=1)
        emit=nodes.new('ShaderNodeEmission');links.new(color,emit.inputs['Color']);links.new(power.outputs[0],emit.inputs['Strength'])
        transparent=nodes.new('ShaderNodeBsdfTransparent')
        if blend=='PARTICLE_OUTPUT_BLEND_MODE_ADD':
            add=nodes.new('ShaderNodeAddShader');links.new(transparent.outputs[0],add.inputs[0]);links.new(emit.outputs[0],add.inputs[1])
        else:
            add=nodes.new('ShaderNodeMixShader');links.new(alpha,add.inputs[0]);links.new(transparent.outputs[0],add.inputs[1]);links.new(emit.outputs[0],add.inputs[2])
        output=nodes.new('ShaderNodeOutputMaterial');links.new(add.outputs[0],output.inputs['Surface'])
        materials[key]=mat;return mat
    objects=[];billboard_helpers=[];rope_objects=[];rope_systems=[]
    original_frame = scene.frame_current
    original_subframe = scene.frame_subframe
    try:
        for (instance,system_path,resource),samples in collect_system_samples(simulation).items():
            definition=assets['definitions'][resource]
            renderers=[(index,r) for index,r in enumerate(definition.get('m_Renderers',[]))
                       if r['_class']=='C_OP_RenderRopes']
            if not renderers or not any(particles for _,particles in samples):
                continue
            if camera is None or camera.type!='CAMERA':
                raise UnsupportedEffect('Rope geometry requires a sampled scene camera')
            from .blender_particle_rope import bake_rope_geometry
            from .blender_rope_material import prepare_rope_shader_colors, build_rope_material
            camera_samples=[]
            for tick,particles in samples:
                frame=1+(tick-simulation['start_tick'])*frame_step
                scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
                graph=bpy.context.evaluated_depsgraph_get()
                evaluated=camera.evaluated_get(graph)
                projection=evaluated.calc_matrix_camera(graph,x=scene.render.resolution_x,
                    y=scene.render.resolution_y,scale_x=scene.render.pixel_aspect_x,
                    scale_y=scene.render.pixel_aspect_y)
                projection_y=float(projection[1][1])
                if evaluated.data.type=='PERSP':
                    parameters=dict(type='PERSP',projection_y=projection_y)
                elif evaluated.data.type=='ORTHO' and projection_y>0:
                    parameters=dict(type='ORTHO',height=2/(projection_y*unit_scale))
                else:
                    raise UnsupportedEffect('Unsupported rope camera projection '+evaluated.data.type)
                camera_samples.append(dict(tick=tick,age=(tick-births[instance])/simulation['tick_rate'],
                    particles=particles,camera_position=[float(v/unit_scale) for v in evaluated.matrix_world.translation],
                    camera_projection=parameters))
            for index,renderer in renderers:
                if renderer.get('m_bOnlyRenderInEffectsBloomPass'):
                    omissions.append(resource+': source bloom-only pass omitted; use scene compositor glare')
                    continue
                baked=bake_rope_geometry(camera_samples,renderer,scene,collection,
                    start_tick=simulation['start_tick'],tick_rate=simulation['tick_rate'],
                    unit_scale=unit_scale,definition=definition,triangle_pairs=True)
                by_tick={sample['tick']:sample for sample in camera_samples}
                for obj in baked:
                    tick=obj['agr_rope_geometry_tick'];sample=by_tick[tick]
                    sequence=sample['particles'][0]['sequence']
                    layer_images={layer['m_hTexture']:texture(layer['m_hTexture'],sequence)
                        for layer in renderer.get('m_vecTexturesInput',[]) if layer.get('m_hTexture')}
                    prepare_rope_shader_colors(obj.data,renderer)
                    mat,limitations=build_rope_material(renderer,layer_images,scene_light=(1,1,1),age=sample['age'])
                    obj.data.materials.append(mat);omissions.extend(resource+': '+item for item in limitations)
                    obj.name=f'AGR FX rope | {instance} | {system_path} | {index} | {tick}'
                    obj['agr_particle_resource']=resource;obj['agr_particle_instance']=instance
                    obj['agr_particle_system_path']=system_path;obj['agr_simulation_backend']=simulation['backend']
                    obj['agr_particle_rope_sampled_camera']=camera.name
                    obj['agr_rope_geometry_only']=False
                    obj['agr_rope_renderer_index']=index
                objects.extend(baked);rope_objects.extend(baked)
                rope_systems.append(dict(instance=instance,path=system_path,resource=resource,renderer=index,
                    meshes=len(baked),subdivisions=sorted({o['agr_rope_subdivisions'] for o in baked})))
    finally:
        scene.frame_set(original_frame,subframe=original_subframe)
    for (instance,system_path,resource,particle_id),samples in tracks.items():
        if len({p['sequence'] for _,_,p in samples}) != 1:
            raise UnsupportedEffect('Changing sprite sequences are not implemented: '+resource)
        definition=assets['definitions'][resource]
        for index,renderer in enumerate(definition.get('m_Renderers',[])):
            if renderer['_class']=='C_OP_RenderRopes':
                continue # Whole live-array geometry was baked once per system above.
            if renderer.get('m_bOnlyRenderInEffectsBloomPass'):
                omissions.append(resource+': source bloom-only pass omitted; use scene compositor glare')
                continue
            kind=renderer['_class'];light=kind=='C_OP_RenderStandardLight';trail=kind=='C_OP_RenderTrails'
            if kind not in ('C_OP_RenderSprites','C_OP_RenderTrails','C_OP_RenderStandardLight'):
                raise UnsupportedEffect('Simulated renderer '+kind)
            name=f'AGR FX | {instance} | {Path(resource).stem} | {system_path} | {particle_id}.{index}'
            if light:
                data=bpy.data.lights.new(name,'POINT');data.shadow_soft_size=.05
            else:
                data=bpy.data.meshes.new(name)
                vertices=[(0,0,-1),(1,0,-1),(1,0,1),(0,0,1)] if trail else [(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)]
                data.from_pydata(vertices,[],[(0,1,2,3)]);uv=data.uv_layers.new()
                coords=[(0,0),(0,1),(1,1),(1,0)] if trail else [(0,0),(1,0),(1,1),(0,1)]
                for item,value in zip(uv.data,coords):item.uv=value
                data.materials.append(material(instance,system_path,particle_id,resource,index,renderer,samples[0][2]['sequence'],samples))
            obj=bpy.data.objects.new(name,data);collection.objects.link(obj);objects.append(obj);obj.rotation_mode='QUATERNION'
            obj['agr_particle_resource']=resource;obj['agr_particle_instance']=instance;obj['agr_simulation_backend']=simulation['backend']
            obj['agr_particle_system_path']=system_path
            normal_aligned=renderer.get('m_nOrientationType')=='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL'
            facing_helper=None
            if not light:
                obj.visible_shadow=False
                if trail:
                    constraint=obj.constraints.new('LOCKED_TRACK');constraint.target=camera
                    constraint.track_axis='TRACK_Y';constraint.lock_axis='LOCK_X'
                    constraint.name='AGR trail camera'
                elif not normal_aligned:
                    # Keep translation/scale/color on the particle itself. A separate
                    # animated anchor avoids a dependency cycle while native tracking
                    # supplies camera facing and the sprite retains its recorded roll.
                    facing_helper=bpy.data.objects.new(name+' | camera facing',None)
                    collection.objects.link(facing_helper);billboard_helpers.append(facing_helper)
                    facing_helper['agr_particle_billboard_helper']=True
                    facing_helper.hide_select=True
                    facing_helper.empty_display_size=.025
                    track=facing_helper.constraints.new('TRACK_TO');track.target=camera
                    track.name='AGR sprite camera';track.track_axis='TRACK_Z';track.up_axis='UP_Y'
                    rotation=obj.constraints.new('COPY_ROTATION');rotation.target=facing_helper
                    rotation.name='AGR camera facing before particle roll'
                    rotation.mix_mode='BEFORE';rotation.owner_space='WORLD';rotation.target_space='WORLD'
            for tick,step,p in samples:
                frame=1+(tick-simulation['start_tick'])*frame_step
                obj.location=Vector(p['position'])*unit_scale
                alpha=p['alpha']*p['alpha_alternate']*scalar(renderer.get('m_flAlphaScale'),default=1)
                obj.color=(*p['color'],max(0,alpha))
                if light:
                    scale=renderer.get('m_vecColorScale',{})
                    tint=[v/255 for v in scale.get('m_LiteralColor',[255,255,255])[:3]] if scale.get('m_nType')=='PVEC_TYPE_LITERAL_COLOR' else scale.get('m_vLiteralValue',[1,1,1])
                    data.color=[a*b for a,b in zip(p['color'],tint)]
                    radius=p['radius']*scalar(renderer.get('m_flRadiusMultiplier'),default=8)*unit_scale
                    data.energy=max(0,alpha)*scalar(renderer.get('m_flIntensity'),default=1)*20*4*math.pi*radius*radius
                    data.use_custom_distance=True;data.cutoff_distance=max(.01,radius)
                    data.keyframe_insert('energy',frame=frame);data.keyframe_insert('color',frame=frame)
                    data.keyframe_insert('cutoff_distance',frame=frame)
                elif trail:
                    length,radius,direction=trail_dimensions(p,renderer,step)
                    obj.rotation_quaternion=Vector((1,0,0)).rotation_difference(Vector(direction))
                    obj.location-=Vector(direction)*length*renderer.get('m_flForwardShift',0)*unit_scale
                    obj.scale=(max(1e-9,length*unit_scale),1,max(1e-9,radius*unit_scale))
                else:
                    radius=p['radius']*scalar(renderer.get('m_flRadiusScale'),default=1)*unit_scale
                    obj.scale=(radius,radius,radius)
                    if normal_aligned:
                        obj.rotation_quaternion=Vector((0,0,1)).rotation_difference(Vector(p['normal']).normalized())@Quaternion((0,0,1),p['rotation'][2])
                    else:
                        facing_helper.location=obj.location
                        facing_helper.keyframe_insert('location',frame=frame)
                        obj.rotation_quaternion=Quaternion((0,0,1),p['rotation'][2])
                for prop in ('location','scale','rotation_quaternion','color'):obj.keyframe_insert(prop,frame=frame)
                obj.hide_render=False;obj.hide_viewport=False
                obj.keyframe_insert('hide_render',frame=frame);obj.keyframe_insert('hide_viewport',frame=frame)
            first=1+(samples[0][0]-simulation['start_tick'])*frame_step;last=1+(samples[-1][0]-simulation['start_tick'])*frame_step
            # Samples are one replay tick apart, regardless of output FPS. A
            # whole output-frame offset stretches low-FPS tails and clips high-FPS ones.
            boundaries = (first-frame_step, last+frame_step)
            for frame in boundaries:
                obj.hide_render=True;obj.hide_viewport=True;obj.keyframe_insert('hide_render',frame=frame);obj.keyframe_insert('hide_viewport',frame=frame)
            if light:
                for frame in boundaries:data.energy=0;data.keyframe_insert('energy',frame=frame)
                _linear(data)
            _linear(obj)
            if facing_helper is not None:_linear(facing_helper)
    report={'status':'experimental','instances':len(simulation['instances']),'particle_tracks':len(tracks),'objects':len(objects),'point_lights':sum(o.type=='LIGHT' for o in objects),'textures':len(images),'backend':simulation['backend'],'omissions':sorted(set(omissions)),
            'limitations':['Blender shader approximation; no Source bloom/depth intersection pass','Light intensity calibrated to Blender units; not measured engine radiometry','Random engine seed is not available in the demo','Only the supplied recorded instances/control-point history are simulated']}
    report.update(camera_binding='live_constraints',camera_object=camera.name,billboard_helpers=len(billboard_helpers))
    if rope_objects:
        live_constraints=bool(billboard_helpers or any(c.type=='LOCKED_TRACK' for o in objects for c in o.constraints))
        report['camera_binding']='sampled_rope_meshes_with_live_sprite_trail_constraints' if live_constraints else 'sampled_rope_meshes'
        report['rope_systems']=rope_systems
        report['rope_lighting']={'policy':'explicit neutral RGB; Source lighting is not reconstructed','rgb':[1,1,1]}
        report['limitations'].append('Rope geometry samples camera position and projection per replay tick. Moving, animating or replacing the camera afterward requires rebuilding rope geometry.')
    if any(obj.type == 'MESH' for obj in objects):
        from .blender_effects import ensure_particle_transparency
        report['cycles_transparent_bounces'] = ensure_particle_transparency(scene)
    if billboard_helpers or any(c.type=='LOCKED_TRACK' for o in objects for c in o.constraints):
        from . import blender_particle_camera
        source=Path(blender_particle_camera.__file__).read_text(encoding='utf-8')
        script=next((t for t in bpy.data.texts if t.get('agr_particle_camera_script')==1 and t.as_string()==source),None)
        if script is None:
            script=bpy.data.texts.new('AGR_Retarget_Effects.py');script.write(source)
            script['agr_particle_camera_script']=1
        report['camera_retarget_text']=script.name
        scene['agr_simulated_effects_camera']=camera.name
    report['limitations'].append('Tracking uses world-up; roll is ambiguous on the world-up axis. A different active camera requires retargeting constraints.')
    scene['agr_simulated_effects_report']=json.dumps(report)
    return report
