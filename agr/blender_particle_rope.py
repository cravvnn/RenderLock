"""Bake verified rope geometry samples; material/simulation support is separate.

One mesh per sampled tick preserves changing topology, UVs and vertex colors
without external playback handlers. Camera-dependent geometry uses the camera
position and projection explicitly sampled at each replay tick.
"""
from .particle_rope_geometry import build_rope_geometry
from .installed_effects import scalar


def bake_rope_geometry(samples,renderer,scene,collection,*,start_tick,tick_rate,unit_scale=.0254,definition=None,triangle_pairs=False):
    import bpy
    from .blender_effects import _linear
    previous=None;prepared=[];vertices=0
    for sample in samples:
        tick=sample['tick']
        if type(tick) is not int or (previous is not None and tick<=previous):
            raise ValueError('Rope sample ticks must be unique and increasing')
        previous=tick
        mesh=build_rope_geometry(sample['particles'],sample.get('renderer',renderer),sample['age'],sample.get('camera_position'),
                                 camera_projection=sample.get('camera_projection'),definition=definition)
        input_vertex_count=len(mesh['vertices'])
        bias=scalar(sample.get('renderer',renderer).get('m_flDepthBias'),default=0)
        if bias and len(mesh['vertices']):
            from .particle_depth_bias import projective_depth_bias,discard_xy_clipped_triangles
            if triangle_pairs:mesh=discard_xy_clipped_triangles(mesh,sample.get('camera_projection'))
            mesh['source_vertices']=mesh['vertices']
            mesh['vertices'],mesh['depth_ratio']=projective_depth_bias(mesh['vertices'],bias,sample.get('camera_projection'))
            mesh['depth_bias']=bias
        vertices+=input_vertex_count
        if vertices>2_000_000:raise ValueError('Rope geometry bake exceeds vertex budget')
        prepared.append((tick,mesh))
    frame_step=(scene.render.fps/scene.render.fps_base)/tick_rate
    if frame_step<=0 or unit_scale<=0:raise ValueError('Invalid rope timing or unit scale')
    objects=[]
    for tick,geometry in prepared:
        if not geometry['faces']:continue
        mesh=bpy.data.meshes.new('AGR rope geometry '+str(tick))
        faces=geometry['faces']
        if triangle_pairs:
            faces=[triangle for face in faces for triangle in ((face,)if len(face)==3 else
                ((face[0],face[1],face[2]),(face[0],face[2],face[3])))]
        mesh.from_pydata([[float(x)*unit_scale for x in v] for v in geometry['vertices']],[],faces)
        uv=mesh.uv_layers.new(name='UVMap')
        colors=mesh.color_attributes.new(name='AGR Rope Color',type='FLOAT_COLOR',domain='CORNER')
        ratios=geometry.get('depth_ratio')
        if ratios is not None:
            ratio=mesh.attributes.new(name='AGR Rope Depth Ratio',type='FLOAT',domain='POINT')
            source=mesh.attributes.new(name='AGR Rope Source Position',type='FLOAT_VECTOR',domain='POINT')
            for i,(value,position)in enumerate(zip(ratios,geometry['source_vertices'])):
                ratio.data[i].value=float(value);source.data[i].vector=[float(v)*unit_scale for v in position]
        for loop in mesh.loops:
            factor=1 if ratios is None else float(ratios[loop.vertex_index])
            uv.data[loop.index].uv=[float(v)*factor for v in geometry['uv'][loop.vertex_index]]
            colors.data[loop.index].color=geometry['colors'][loop.vertex_index]
        obj=bpy.data.objects.new(mesh.name,mesh);collection.objects.link(obj);objects.append(obj)
        obj['agr_rope_geometry_tick']=tick;obj['agr_rope_geometry_only']=True
        obj['agr_rope_subdivisions']=geometry['subdivisions']
        if ratios is not None:
            obj['agr_rope_depth_bias_policy']='projective_depth_proxy_v1';obj['agr_rope_depth_bias']=geometry['depth_bias']
            if 'depth_xy_cull'in geometry:
                obj['agr_rope_xy_cull_policy']=geometry['depth_xy_cull']['policy']
                obj['agr_rope_xy_culled_triangles']=geometry['depth_xy_cull']['culled_triangles']
        obj.visible_shadow=False
        frame=1+(tick-start_tick)*frame_step
        for at,hidden in ((frame-frame_step,True),(frame,False),(frame+frame_step,True)):
            obj.hide_render=hidden;obj.hide_viewport=hidden
            obj.keyframe_insert('hide_render',frame=at);obj.keyframe_insert('hide_viewport',frame=at)
        _linear(obj)
    return objects
