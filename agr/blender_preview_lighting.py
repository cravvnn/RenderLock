"""Editable artistic key/fill lighting for a selected character follow shot."""
import math


def light_actor_preview(scene, root, framing):
    import bpy
    from mathutils import Vector
    if scene.camera is None or root.type != 'EMPTY':
        raise ValueError('Character preview lighting requires a camera and replay root')
    if framing.get('entity_id') != root.get('agr_entity_id'):
        raise ValueError('Character preview lighting actor identity differs')
    low,high=framing['bounds_min_m'],framing['bounds_max_m']
    center=Vector(framing['target_offset_m'])
    if (len(low)!=3 or len(high)!=3 or len(center)!=3
            or not all(math.isfinite(float(v)) for v in (*low,*high,*center))
            or any(b<a for a,b in zip(low,high))):
        raise ValueError('Invalid character preview bounds')
    height=float(high[2]-low[2])
    if not .1 <= height <= 32:
        raise ValueError('Character preview height outside supported metre bounds')
    frames=list(range(scene.frame_start,scene.frame_end+1))
    if not frames or len(frames)>4096:
        raise ValueError('Character preview lighting requires at most4096 frames')
    lights=[]
    for name in ('AGR | Key','AGR | Fill'):
        lamp=scene.objects.get(name)
        if lamp is None or lamp.type!='LIGHT' or lamp.data.type!='AREA' or lamp.parent or lamp.constraints:
            raise ValueError('Expected editable builder-owned preview area lights')
        lights.append(lamp)
    # Standard studio directions, relative to the viewing side. Power and source
    # size scale with distance for consistent framing, not a fit to game pixels.
    design=((-1.,1.,1.5,250.),(1.,.15,1.5,100.))
    rows=[]
    for lamp,(x,y,z,power) in zip(lights,design):
        distance=height*math.sqrt(x*x+y*y+z*z)
        rows.append(dict(name=lamp.name,power_w=power*(distance/3.)**2,
                         size_m=distance*.5,view_offset_heights=[x,y,z]))
    original_frame,original_subframe=scene.frame_current,scene.frame_subframe
    previous={}
    try:
        for lamp,row in zip(lights,rows):
            lamp.animation_data_clear()
            lamp.hide_render=False
            lamp.data.energy=row['power_w'];lamp.data.size=row['size_m']
        for frame in frames:
            scene.frame_set(frame)
            target=root.matrix_world.translation+center
            orientation=scene.camera.matrix_world.to_quaternion()
            for lamp,row in zip(lights,rows):
                offset=orientation@Vector(row['view_offset_heights'])*height
                lamp.location=target+offset
                rotation=(-offset).to_track_quat('-Z','Y')
                lamp.rotation_euler=rotation.to_euler('XYZ',previous.get(lamp.name)) if lamp.name in previous else rotation.to_euler('XYZ')
                previous[lamp.name]=lamp.rotation_euler.copy()
                lamp.keyframe_insert('location',frame=frame)
                lamp.keyframe_insert('rotation_euler',frame=frame)
        for lamp in lights:
            action=lamp.animation_data.action
            curves=([curve for layer in action.layers for strip in layer.strips
                     for bag in strip.channelbags for curve in bag.fcurves]
                    if hasattr(action,'layers') else action.fcurves)
            for curve in curves:
                for key in curve.keyframe_points:key.interpolation='LINEAR'
    finally:
        scene.frame_set(original_frame,subframe=original_subframe)
    return dict(status='artistic_preview',mode='camera_relative_key_fill',
        entity_id=root.get('agr_entity_id'),frames=len(frames),actor_height_m=height,
        lights=rows,editable=True,
        limits='Artistic Blender preview lights, not reconstructed Source direct lights or shadows. Integer-frame bake; rebake after camera/root edits.')
