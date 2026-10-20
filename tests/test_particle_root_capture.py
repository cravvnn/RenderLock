"""Actual Blender regression for root controls beside unchanged model bones."""
import math

import numpy as np
import pytest


def test_root_control_uses_explicit_render_basis_without_rotating_model_twice(tmp_path):
    bpy = pytest.importorskip('bpy')
    from mathutils import Matrix
    from agr.blender_particle_model import capture_root_model_points

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 30
    scene.render.fps_base = 1.001
    root = bpy.data.objects.new('Replay root', None)
    scene.collection.objects.link(root)
    root['agr_entity_id'] = '7:12'
    armature = bpy.data.armatures.new('Exact fixture skeleton')
    rig = bpy.data.objects.new('Fixture rig', armature)
    scene.collection.objects.link(rig)
    rig['agr_entity_id'] = '7:12'
    rig.parent = root
    rig.rotation_euler.z = math.pi
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = armature.edit_bones.new('root_motion')
    bone.head = (.1,.2,.3)
    bone.tail = (.1,.2,.4)
    bpy.ops.object.mode_set(mode='OBJECT')
    pose = tmp_path / 'poses.npz'
    np.savez(pose, ticks=[100,101,102])
    model = dict(schema_version=1, resource='models/fixture.vmdl',
        resource_sha256='a'*64, names=['root_motion'], parents=[-1], definitions=[])
    entity = dict(id='7:12', model_constraints=model, pose_file=str(pose),
        samples=[dict(tick=tick) for tick in (100,101,102)])
    data = dict(start_tick=100,end_tick=102,tick_rate=64)
    frames = [1 + (tick-100)*scene.render.fps/scene.render.fps_base/64
              for tick in (100,101,102)]
    for index, frame in enumerate(frames):
        root.location = (.254+index*.1,.508,.762)
        root.rotation_euler = (.1,-.2,.3+index*.2)
        root.keyframe_insert('location',frame=frame)
        root.keyframe_insert('rotation_euler',frame=frame)
    scene.frame_set(7,subframe=.375)
    objects = dict(root=root,rig=rig)
    instance = dict(created_tick=100)
    legacy, old_provenance = capture_root_model_points(data,entity,objects,scene,instance,tmp_path)
    entity['render_root_yaw_degrees'] = 180
    corrected, provenance = capture_root_model_points(data,entity,objects,scene,instance,tmp_path)
    assert (scene.frame_current,scene.frame_subframe) == (7,.375)
    assert old_provenance['render_root_yaw_degrees'] == 0
    assert provenance['render_root_yaw_degrees'] == 180
    for index, (before, after) in enumerate(zip(legacy,corrected)):
        assert before['tick'] == after['tick'] == 100+index
        np.testing.assert_array_equal(before['position'], after['position'])
        np.testing.assert_array_equal(before['model_bones'], after['model_bones'])
        scene.frame_set(math.floor(frames[index]),subframe=frames[index]%1)
        expected = root.matrix_world @ Matrix.Rotation(math.pi,4,'Z')
        expected_quat = expected.to_quaternion()
        np.testing.assert_allclose(after['position'], np.asarray(expected.translation)/.0254,atol=1e-5)
        q = np.asarray([expected_quat.x,expected_quat.y,expected_quat.z,expected_quat.w])
        assert abs(q @ after['rotation']) == pytest.approx(1,abs=2e-6)
        assert abs(np.asarray(before['rotation']) @ after['rotation']) < 1e-6
        actual_bone = rig.matrix_world @ rig.pose.bones['root_motion'].matrix
        np.testing.assert_allclose(np.asarray(after['model_bones'])[0,:3],
                                   np.asarray(actual_bone.translation)/.0254,atol=1e-5)
    scene.frame_set(7,subframe=.375)
    for invalid in (None,True,'180',float('nan'),float('inf')):
        entity['render_root_yaw_degrees'] = invalid
        with pytest.raises(ValueError,match='render-root yaw'):
            capture_root_model_points(data,entity,objects,scene,instance,tmp_path)
        assert (scene.frame_current,scene.frame_subframe) == (7,.375)
    entity['render_root_yaw_degrees'] = 180
    root.scale = (2,2,2)
    with pytest.raises(ValueError,match='finite rigid world transform'):
        capture_root_model_points(data,entity,objects,scene,instance,tmp_path)
    assert (scene.frame_current,scene.frame_subframe) == (7,.375)
