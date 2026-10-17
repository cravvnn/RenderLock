"""Bake explicit camera-forward primary reference at exported integer frames."""
import math
import numpy as np
from .npr_primary import primary_direction


def camera_primary_track(scene, coordinates, *, assume_camera_forward=False):
    if assume_camera_forward is not True:
        raise ValueError('Explicit camera-forward primary assumption required')
    yaw=float(coordinates['world_yaw_degrees'])
    if not math.isfinite(yaw):raise ValueError('Finite Source world yaw required')
    if scene.frame_end<scene.frame_start:raise ValueError('Empty primary frame range')
    import bpy
    from mathutils import Vector
    yaw=math.radians(yaw);rows=[]
    frame,subframe=scene.frame_current,scene.frame_subframe
    try:
        for current in range(scene.frame_start,scene.frame_end+1):
            scene.frame_set(current)
            if scene.camera is None or scene.camera.type!='CAMERA' or scene.camera.data.type!='PERSP':
                raise ValueError('Evaluated perspective camera required for primary reference')
            camera=scene.camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
            forward=camera.matrix_world.to_quaternion()@Vector((0,0,-1))
            xy=[math.cos(yaw)*forward.x+math.sin(yaw)*forward.y,
                -math.sin(yaw)*forward.x+math.cos(yaw)*forward.y]
            value=primary_direction([xy],.35)[0]
            rows.append(dict(frame=current,camera=scene.camera.name,primary=value.tolist()))
    finally:scene.frame_set(frame,subframe=subframe)
    return dict(rows=rows,assumption='Camera world forward projected to Source XY; weight .35.',
                limits='Reference camera fields, not proven engine view binding; integer frames only.')


def bind_primary_track(materials, track):
    """Bind on caller-owned prepared copies; no persistent Python handlers."""
    materials=list(materials);rows=track['rows']
    frames=[row['frame'] for row in rows]
    if not materials or len(set(materials))!=len(materials) or not frames:
        raise ValueError('Distinct materials and primary samples required')
    if any(type(frame) is not int for frame in frames) or frames!=list(range(frames[0],frames[-1]+1)):
        raise ValueError('Contiguous integer primary frames required')
    values=np.asarray([row['primary'] for row in rows],dtype=float)
    if values.shape!=(len(rows),3) or not np.isfinite(values).all() or np.any(abs(np.linalg.norm(values,axis=1)-1)>1e-5):
        raise ValueError('Finite normalized primary samples required')
    sockets=[]
    for material in materials:
        if not material.use_nodes:raise ValueError('Prepared NPR material required')
        node=material.node_tree.nodes.get('AGR NPR indirect preview')
        if node is None or 'Primary' not in node.inputs:raise ValueError('Prepared NPR primary input required')
        socket=node.inputs['Primary']
        if socket.is_linked or material.node_tree.animation_data is not None:
            raise ValueError('Primary requires unlinked input and unanimated material tree')
        sockets.append(socket)
    for socket in sockets:
        for row in rows:
            socket.default_value=row['primary']
            socket.keyframe_insert(data_path='default_value',frame=row['frame'],group='AGR primary reference')
        # Constant samples avoid inventing a normalized subframe camera path.
        animation=socket.id_data.animation_data
        for layer in animation.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points:key.interpolation='CONSTANT'
    return dict(status='baked_integer_frame_reference',frames=len(rows),materials=len(materials),
                limits='Rebake after camera edits; fractional frames hold the previous integer sample.')
