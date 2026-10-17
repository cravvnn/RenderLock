"""Real Blender checks for sprite/light lifetimes at differing output rates."""
import math

import pytest


@pytest.mark.parametrize('fps,base', [(24,1), (30,1), (64,1), (120,1), (128,1), (24,1.001)])
def test_particles_and_lights_end_on_replay_ticks_at_any_output_rate(fps, base):
    bpy = pytest.importorskip('bpy')
    from agr.blender_simulated_effects import build_simulated_effects
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = fps
    scene.render.fps_base = base
    camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
    scene.collection.objects.link(camera)
    camera.location = (0,-10,0)
    scene.camera = camera
    particle = dict(id=0,position=[1,2,3],previous=[1,2,3],radius=1,alpha=1,age=0,trail=1,
                    alpha_alternate=1,color=[1,1,1],sequence=0,normal=[0,0,1],rotation=[0,0,0])
    frames = [dict(tick=tick,systems=[dict(name=name+'_c',system_path=path,step=1/64,
        particles=[particle] if tick in (101,102) else [])
        for name,path in [('test.vpcf','root'),('light.vpcf','root/0')]]) for tick in range(100,104)]
    simulation = dict(schema_version=1,backend='timing fixture',warnings=[],start_tick=100,
                      end_tick=103,tick_rate=64,instances=[dict(id='1:1',birth=100,frames=frames)])
    assets = dict(definitions={
        'test.vpcf':dict(m_Renderers=[dict(_class='C_OP_RenderSprites',
            m_nOutputBlendMode='PARTICLE_OUTPUT_BLEND_MODE_ADD',
            m_nOrientationType='PARTICLE_ORIENTATION_ALIGN_TO_PARTICLE_NORMAL',m_vecTexturesInput=[])]),
        'light.vpcf':dict(m_Renderers=[dict(_class='C_OP_RenderStandardLight')])},textures={})
    report = build_simulated_effects(simulation,assets,scene,camera)
    assert report['objects'] == 2 and report['point_lights'] == 1
    assert report['cycles_transparent_bounces'] == scene.cycles.transparent_max_bounces == 64
    objects = [obj for obj in scene.objects if obj.get('agr_particle_instance') == '1:1']
    effective_fps = scene.render.fps / scene.render.fps_base
    first = 1 + effective_fps/64
    last = 1 + 2*effective_fps/64
    death = 1 + 3*effective_fps/64

    def at(frame):
        scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))

    at(first-1e-3)
    assert all(obj.hide_render for obj in objects)
    at((first+last)/2)
    assert all(not obj.hide_render for obj in objects)
    at((last+death)/2)
    assert all(not obj.hide_render for obj in objects)
    assert next(obj for obj in objects if obj.type == 'LIGHT').data.energy > 0
    at(death+1e-3)
    assert all(obj.hide_render for obj in objects)
    assert next(obj for obj in objects if obj.type == 'LIGHT').data.energy == 0


def test_overlapping_transparent_cards_do_not_turn_black_in_cycles(tmp_path):
    bpy = pytest.importorskip('bpy')
    import numpy as np
    from agr.blender_effects import ensure_particle_transparency
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = 32
    scene.render.resolution_percentage = 100
    scene.render.threads_mode = 'FIXED'; scene.render.threads = 2
    scene.world = bpy.data.worlds.new('Background')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (.25,.25,.25,1)
    camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
    scene.collection.objects.link(camera); camera.location=(0,0,5); scene.camera=camera
    material = bpy.data.materials.new('Transparent card'); material.use_nodes=True
    nodes=material.node_tree.nodes;nodes.clear()
    shader=nodes.new('ShaderNodeBsdfTransparent');output=nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(shader.outputs[0],output.inputs['Surface'])
    for index in range(12):
        mesh=bpy.data.meshes.new('Card');mesh.from_pydata([(-2,-2,0),(2,-2,0),(2,2,0),(-2,2,0)],[],[(0,1,2,3)])
        mesh.materials.append(material)
        obj=bpy.data.objects.new('Card',mesh);scene.collection.objects.link(obj);obj.location.z=index*.05
    def center_mean(name):
        bpy.ops.render.render(write_still=False)
        path=tmp_path/(name+'.exr')
        scene.render.image_settings.file_format='OPEN_EXR'
        bpy.data.images['Render Result'].save_render(str(path),scene=scene)
        image=bpy.data.images.load(str(path),check_existing=False)
        pixels=np.asarray(image.pixels[:]).reshape((32,32,4))
        result=float(pixels[14:18,14:18,:3].mean())
        bpy.data.images.remove(image)
        return result
    scene.cycles.transparent_max_bounces=8
    assert center_mean('baseline') < .01
    ensure_particle_transparency(scene)
    assert center_mean('corrected') > .2
    scene.cycles.transparent_max_bounces=128
    assert ensure_particle_transparency(scene)==128
