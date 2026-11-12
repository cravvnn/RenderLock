"""Actual node Action radius interpolation; synthetic, no game assets or render.

Run background Blender --python this_file -- <output.json>.
Fresh normal exports, native arithmetic and rendered appearance are separate gates.
"""
from pathlib import Path
import json,math,sys
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agr.blender_effects import _channels,_linear
from agr.blender_particle_light_range import configure_standard_light,keyframe_radius

def action_points(action):
    return [(f.data_path,f.array_index,tuple(tuple(k.co)for k in f.keyframe_points))for f in _channels(action)]

def run():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.render.engine='CYCLES';rows=[]
    for fps in (24,30,60):
        scene.render.fps=fps;scene.render.fps_base=1.
        for attenuation in ('quadratic','linear'):
            light=bpy.data.lights.new(f'{fps} {attenuation}','POINT')
            obj=bpy.data.objects.new(light.name,light);scene.collection.objects.link(obj)
            light.energy=2.;light.keyframe_insert('energy',frame=1)
            original_action=light.animation_data.action;original_points=action_points(original_action)
            radius=configure_standard_light(light,.0254,attenuation)
            assert light.node_tree.animation_data.action is not original_action
            for tick,value in ((100,1.),(102,5.)):
                keyframe_radius(radius,value,1+(tick-100)*fps/60)
            _linear(light.node_tree)
            for tick,expected in ((100.,1.),(100.5,2.),(101.,3.),(101.5,4.),(102.,5.)):
                frame=1+(tick-100)*fps/60
                scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
                scene.view_layers[0].update()
                assert abs(radius.default_value-expected)<2e-6,(fps,attenuation,tick,radius.default_value)
                rows.append(dict(fps=fps,attenuation=attenuation,tick=tick,radius=radius.default_value))
            assert light.animation_data.action is original_action
            assert action_points(original_action)==original_points
            try:configure_standard_light(light,.0254,attenuation)
            except ValueError as exc:assert 'animated shader'in str(exc)
            else:raise AssertionError('Existing animated node tree was replaced')
            assert action_points(original_action)==original_points
    return dict(status='PASS',checks=rows,existing_light_actions_preserved=True,
        scope='Synthetic actual Blender node Action interpolation at24/30/60FPS and animated-tree refusal; no native renderer, FX replay, photometry or pixel equivalence claim.')

if __name__=='__main__':
    out=Path(sys.argv[sys.argv.index('--')+1]);result=run()
    out.write_text(json.dumps(result,indent=2));print(json.dumps(result))
