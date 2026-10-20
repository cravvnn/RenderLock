"""Retarget AGR's native particle constraints to the active Blender camera.

This standalone script is also stored in exported scenes as a Blender Text.
Run it manually after switching cameras for sprite/trail constraints. Those
constraints follow the existing camera as it moves. Camera-sampled rope meshes
require geometry rebuilding and are rejected here. Simulation is not rerun.
"""


def retarget_simulated_effects(scene, camera):
    if camera is None or camera.type != 'CAMERA' or camera.name not in scene.objects:
        raise ValueError('Choose an active camera in this scene first')
    if any(obj.get('agr_particle_rope_sampled_camera') for obj in scene.objects):
        raise ValueError('Rope geometry uses sampled camera position and projection. Rebuild the rope geometry for the changed camera before retargeting particle constraints.')
    targets = []
    for obj in scene.objects:
        if obj.get('agr_particle_billboard_helper'):
            matches = [c for c in obj.constraints if c.name == 'AGR sprite camera' and c.type == 'TRACK_TO']
            if len(matches) != 1:
                raise ValueError('Unexpected AGR billboard tracking graph: ' + obj.name)
            targets.extend(matches)
        elif obj.get('agr_particle_instance'):
            targets.extend(c for c in obj.constraints if c.name == 'AGR trail camera' and c.type == 'LOCKED_TRACK')
    for constraint in targets:
        constraint.target = camera
    scene['agr_simulated_effects_camera'] = camera.name
    return len(targets)


if __name__ == '__main__':
    import bpy
    count = retarget_simulated_effects(bpy.context.scene, bpy.context.scene.camera)
    print(f'Retargeted {count} AGR particle constraints to {bpy.context.scene.camera.name}')
