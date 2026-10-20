import math
import numpy as np
import pytest


@pytest.fixture
def preview():
    bpy=pytest.importorskip('bpy')
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.frame_start=1;scene.frame_end=3
    root=bpy.data.objects.new('Replay root',None);scene.collection.objects.link(root)
    root['agr_entity_id']='fixture:1'
    camera=bpy.data.objects.new('Camera',bpy.data.cameras.new('Camera'));scene.collection.objects.link(camera);scene.camera=camera
    lights=[]
    for label in ('Key','Fill'):
        lamp=bpy.data.objects.new('AGR | '+label,bpy.data.lights.new(label,'AREA'))
        scene.collection.objects.link(lamp);lights.append(lamp)
    for frame in range(1,4):
        root.location=(-1000+frame,2000-frame*2,50)
        root.keyframe_insert('location',frame=frame)
        camera.location=root.location+Vector((5,frame,3))
        camera.rotation_euler=(root.location+Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler()
        camera.keyframe_insert('location',frame=frame);camera.keyframe_insert('rotation_euler',frame=frame)
    framing=dict(entity_id='fixture:1',bounds_min_m=[-.5,-.5,0],bounds_max_m=[.5,.5,2],target_offset_m=[0,0,1])
    return bpy,scene,root,camera,lights,framing


def test_camera_relative_light_tracks_preserve_replay_and_view(preview):
    from agr.blender_preview_lighting import light_actor_preview
    from mathutils import Vector
    bpy,scene,root,camera,lights,framing=preview
    before=[]
    for frame in range(1,4):
        scene.frame_set(frame);before.append([np.array(root.matrix_world),np.array(camera.matrix_world)])
    scene.frame_set(2,subframe=.25)
    report=light_actor_preview(scene,root,framing)
    assert scene.frame_current==2 and scene.frame_subframe==.25
    assert report['status']=='artistic_preview' and report['editable'] is True
    for frame in range(1,4):
        scene.frame_set(frame)
        np.testing.assert_array_equal([np.array(root.matrix_world),np.array(camera.matrix_world)],before[frame-1])
        target=root.matrix_world.translation+Vector(framing['target_offset_m'])
        for light,row in zip(lights,report['lights']):
            offset=light.matrix_world.translation-target
            view_offset=camera.matrix_world.to_quaternion().inverted()@offset/2
            np.testing.assert_allclose(view_offset,row['view_offset_heights'],atol=1e-4)
            toward=light.matrix_world.to_quaternion()@Vector((0,0,-1))
            assert toward.dot((-offset).normalized())>1-1e-6
            assert light.data.energy==pytest.approx(row['power_w'],rel=1e-6)
            assert light.animation_data.action is not None and not light.hide_render


def test_missing_preview_fill_refuses_before_mutating_key(preview):
    from agr.blender_preview_lighting import light_actor_preview
    bpy,scene,root,camera,lights,framing=preview
    lights[1].name='Artist fill'
    before=(tuple(lights[0].location),lights[0].data.energy,lights[0].animation_data)
    with pytest.raises(ValueError,match='builder-owned'):
        light_actor_preview(scene,root,framing)
    assert (tuple(lights[0].location),lights[0].data.energy,lights[0].animation_data)==before


@pytest.mark.parametrize('change',[{'entity_id':'wrong'}, {'bounds_max_m':[.5,.5,float('nan')]},
                                  {'bounds_max_m':[.5,.5,0]}, {'bounds_max_m':[.5,.5,100]}])
def test_invalid_actor_bounds_or_identity_does_not_write_light_animation(preview,change):
    from agr.blender_preview_lighting import light_actor_preview
    bpy,scene,root,camera,lights,framing=preview
    framing.update(change)
    with pytest.raises(ValueError):light_actor_preview(scene,root,framing)
    assert all(light.animation_data is None for light in lights)
