"""Bake verified rope geometry samples; material/simulation support is separate.

One mesh per sampled tick preserves changing topology, UVs and vertex colors
without external playback handlers. Camera-dependent geometry uses the camera
position and projection explicitly sampled at each replay tick.
"""
from .particle_rope_geometry import build_rope_geometry


def bake_rope_geometry(samples,renderer,scene,collection,*,start_tick,tick_rate,unit_scale=.0254,definition=None,triangle_pairs=False):
    import bpy
    from .blender_effects import _linear
    previous=None;prepared=[];vertices=0
    for sample in samples:
        tick=sample['tick']
        if type(tick) is not int or (previous is not None and tick<=previous):
            raise ValueError('Rope sample ticks must be unique and increasing')
        previous=tick
        mesh=build_rope_geometry(sample['particles'],renderer,sample['age'],sample.get('camera_position'),
                                 camera_projection=sample.get('camera_projection'),definition=definition)
        vertices+=len(mesh['vertices'])
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
            faces=[triangle for a,b,c,d in faces for triangle in ((a,b,c),(a,c,d))]
        mesh.from_pydata([[float(x)*unit_scale for x in v] for v in geometry['vertices']],[],faces)
        uv=mesh.uv_layers.new(name='UVMap')
        colors=mesh.color_attributes.new(name='AGR Rope Color',type='FLOAT_COLOR',domain='CORNER')
        for loop in mesh.loops:
            uv.data[loop.index].uv=geometry['uv'][loop.vertex_index]
            colors.data[loop.index].color=geometry['colors'][loop.vertex_index]
        obj=bpy.data.objects.new(mesh.name,mesh);collection.objects.link(obj);objects.append(obj)
        obj['agr_rope_geometry_tick']=tick;obj['agr_rope_geometry_only']=True
        obj['agr_rope_subdivisions']=geometry['subdivisions']
        obj.visible_shadow=False
        frame=1+(tick-start_tick)*frame_step
        for at,hidden in ((frame-frame_step,True),(frame,False),(frame+frame_step,True)):
            obj.hide_render=hidden;obj.hide_viewport=hidden
            obj.keyframe_insert('hide_render',frame=at);obj.keyframe_insert('hide_viewport',frame=at)
        _linear(obj)
    return objects
