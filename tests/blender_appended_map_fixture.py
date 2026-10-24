"""Small background Blender append/occlusion fixture, no private game assets.

Run with Blender --background --factory-startup --disable-autoexec
--python-exit-code 1 --python this_script -- --output /owned/test-case
"""
import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy
from agr import external_map
from agr.blender_external_map import append_external_map, group_renderlock_content
from agr.blender_shot import assess_appended_map_shot


def snapshot(scene, root):
    frames = []
    for frame in (1, 2, 3):
        scene.frame_set(frame)
        frames.append({'camera': [list(row) for row in scene.camera.matrix_world],
                       'root': [list(row) for row in root.matrix_world]})
    return frames


def main(output):
    output.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    library = output / 'synthetic-map.blend'
    collection = bpy.data.collections.new(external_map.MAP_COLLECTION)
    scene.collection.children.link(collection)
    bpy.ops.mesh.primitive_cube_add(location=(-2.5, 0, 1), scale=(.1, 3, 3))
    wall = bpy.context.object
    collection.objects.link(wall); scene.collection.objects.unlink(wall)
    collection['renderlock_map_profile'] = json.dumps(
        dict(package_id='synthetic-test-only', source_frame='vrf_blender_metres'))
    bpy.data.libraries.write(str(library), {collection})
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene; scene.frame_end = 3
    root = bpy.data.objects.new('actor', None); scene.collection.objects.link(root)
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1), scale=(.3, .3, .8))
    body = bpy.context.object; body.parent = root
    camera = bpy.data.objects.new('follow', bpy.data.cameras.new('follow'))
    scene.collection.objects.link(camera); scene.camera = camera
    for frame in (1, 2, 3):
        root.location.x = frame * .1
        camera.location = (frame * .1, -5, 1)
        root.keyframe_insert('location', frame=frame)
        camera.keyframe_insert('location', frame=frame)
    before = snapshot(scene, root)
    group_renderlock_content(scene)
    # Only this synthetic library's identity/counts are substituted. Production
    # hashes and guards are unchanged; all append/hierarchy/alignment code runs.
    definition = dict(package_id='synthetic-test-only', objects=1, meshes=1, vertices=8)
    with (patch.object(external_map, 'validate_map_library', return_value=dict(path=str(library), sha256='fixture')),
          patch.object(external_map, 'map_definition', return_value=definition)):
        report = append_external_map(scene, library, None)
    assert report['linked_libraries'] == 0
    assert report['timings_seconds']['localize'] >= 0
    shot = dict(mode='hero follow', sample_frames=[1, 2, 3], target_offset_m=[0, 0, 1],
                bounds_min_m=[-.3, -.3, 0], bounds_max_m=[.3, .3, 2])
    wall = next(o for o in bpy.data.collections[external_map.MAP_COLLECTION].all_objects if o.type == 'MESH')
    scene.frame_set(2, subframe=.25)
    blocked = assess_appended_map_shot(scene, root, shot)
    assert blocked['status'] == 'obstructed' and blocked['minimum_sampled_visibility'] == 0
    assert (scene.frame_current, scene.frame_subframe) == (2, .25)
    wall.hide_render = True
    hidden = assess_appended_map_shot(scene, root, shot)
    assert hidden['status'] == 'sampled_clear'
    wall.hide_render = False; wall.visible_camera = False
    invisible = assess_appended_map_shot(scene, root, shot)
    assert invisible['status'] == 'sampled_clear'
    wall.visible_camera = True
    with patch('agr.blender_shot._clear_ray', side_effect=RuntimeError('injected ray failure')):
        try:
            assess_appended_map_shot(scene, root, shot)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Ray failure was swallowed')
    assert (scene.frame_current, scene.frame_subframe) == (2, .25)
    assert before == snapshot(scene, root)
    saved = output / 'appended.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    scene = bpy.context.scene; root = bpy.data.objects['actor']
    assert before == snapshot(scene, root)
    assert not bpy.data.libraries
    reopened = assess_appended_map_shot(scene, root, shot)
    assert reopened['status'] == 'obstructed'
    (output / 'result.json').write_text(json.dumps(dict(status='PASS', append=report,
        blocked=blocked, hidden=hidden, camera_invisible=invisible, reopened=reopened,
        animation_unchanged=True, failure_restores_frame=True,
        limits='Synthetic background Blender fixture; no native Linux/full-map/render qualification.'), indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    main(args.output)
