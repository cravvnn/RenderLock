"""Collect current Blender projection before loading qualified research depth."""
import json
import math
from pathlib import Path

import bpy
import numpy as np

from agr.rim_depth_manifest import identity, file_sha256, load_verified


def current_identity(scene_path, coordinates):
    scene = bpy.context.scene
    if Path(bpy.data.filepath).resolve() != Path(scene_path).resolve():
        raise ValueError('Loaded scene is not the requested baseline')
    render = scene.render
    if render.resolution_percentage != 100 or render.use_border:
        raise ValueError('Depth requires full viewport at 100 percent')
    if scene.frame_subframe != 0 or getattr(render, 'use_motion_blur', False):
        raise ValueError('Subframe and motion blur depth are unqualified')
    camera = scene.camera
    if camera is None or camera.data.type != 'PERSP':
        raise ValueError('Perspective scene camera required')
    yaw = math.radians(coordinates['world_yaw_degrees'])
    rotation = np.array([[math.cos(yaw), -math.sin(yaw), 0],
                         [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
    source_to_world = np.eye(4)
    source_to_world[:3, :3] = rotation * coordinates['unit_scale']
    source_to_world[:3, 3] = -rotation @ np.asarray(coordinates['origin']) * coordinates['unit_scale']
    dg = bpy.context.evaluated_depsgraph_get()
    camera = camera.evaluated_get(dg)
    projection = camera.calc_matrix_camera(dg, x=render.resolution_x,
        y=render.resolution_y, scale_x=render.pixel_aspect_x, scale_y=render.pixel_aspect_y)
    matrix = (np.array(projection) @ np.array(camera.matrix_world.inverted()) @ source_to_world).T
    return identity(scene_sha256=file_sha256(scene_path), frame=scene.frame_current,
        camera=scene.camera.name, source_row_matrix=matrix,
        width=render.resolution_x, height=render.resolution_y,
        pixel_aspect=[render.pixel_aspect_x, render.pixel_aspect_y],
        coordinates=coordinates, blender_version=bpy.app.version_string, engine=render.engine,
        camera_clip_source=[float(camera.data.clip_start)/coordinates['unit_scale'],
                            float(camera.data.clip_end)/coordinates['unit_scale']])


def load_current_depth(scene_path, coordinates, depth_path, manifest_path, *, return_policy=False):
    """No image or shader mutation until current-state and payload checks pass.

    Call after opening the baseline and setting frame/viewport, before edits to
    geometry or visibility. Those arbitrary in-memory changes are not tracked.
    """
    current = current_identity(scene_path, coordinates)
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    depth,policy=load_verified(depth_path, manifest, current,return_policy=True)
    return (depth,current,policy) if return_policy else (depth,current)
