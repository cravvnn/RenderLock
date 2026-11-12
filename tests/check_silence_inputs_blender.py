"""Actual asset-free Blender pose/save checks, never native/effect parity.

One CPU process, no render, UI, game, helper or installed assets. Synthetic
observation bytes exercise transport only and are not real capture evidence.
"""
import copy
import json
import math
from pathlib import Path
import struct
import sys

import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agr.blender_effects import _linear
from agr.blender_silence_inputs import capture_points, validate_saved_inputs
from agr.particle_client_capture import bind_client_child_capture, CURRENT_CLIENT_SHA256
from agr import particle_silence_capture as birth_module
from agr.recorded_silence_effects import NORMAL_POLICY
from agr.particle_snapshot_provider import digest

out = Path(sys.argv[sys.argv.index('--') + 1])
out.mkdir(exist_ok=False)
unit = .0254
results = []
for fps in (24, 30, 60):
    case = out / str(fps)
    case.mkdir()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.render.fps = fps
    root = bpy.data.objects.new('replay root', None)
    scene.collection.objects.link(root)
    root['agr_entity_id'] = '56:895'
    rig = bpy.data.objects.new('rig', bpy.data.armatures.new('rig'))
    scene.collection.objects.link(rig)
    rig.parent = root
    rig['agr_entity_id'] = '56:895'
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for name, x in [('root', 0), ('head', 2), ('cloth', 4)]:
        b = rig.data.edit_bones.new(name)
        b.head = (x*unit, 0, 0)
        b.tail = (x*unit, unit, 0)
        if name != 'root':
            b.parent = rig.data.edit_bones['root']
    bpy.ops.object.mode_set(mode='OBJECT')
    for tick in (10, 12):
        frame = 1 + (tick-10)*fps/64
        root.location = ((100+3*(tick-10))*unit, 0, 0)
        root.keyframe_insert('location', frame=frame)
        rig.pose.bones['head'].location = ((tick-10)*unit, 0, 0)
        rig.pose.bones['head'].keyframe_insert('location', frame=frame)
    _linear(root)
    _linear(rig)
    rig.pose.bones['cloth'].scale = (1, 2, 1)
    model = dict(schema_version=1, resource='models/heroes_wip/priest/priest.vmdl_c', resource_sha256='b'*64,
        names=['root', 'head', 'cloth'], parents=[-1, 0, 0], definitions=[])
    entity = dict(id='56:895', model_constraints=model, render_root_yaw_degrees=90,
        samples=[dict(tick=t, position=[100+3*(t-10), 0, 0]) for t in range(10, 13)])
    for name in ('model_file', 'skeleton_file'):
        path = case / name
        path.write_bytes(name.encode())
        entity[name] = str(path)
    np.savez(case / 'poses.npz', ticks=np.array([10, 11, 12]))
    entity['pose_file'] = str(case / 'poses.npz')
    handle = (895 << 14) | 56
    runtime = (895 << 15) | 56
    fallback = dict(x=1., y=2., z=3.)
    instance = dict(id='5718:1', index=5718, generation=1, entity_id=entity['id'], entity_handle=handle,
        resource=birth_module.RESOURCE, resource_hash='123', created_tick=10, destroy_tick=None, events=[
        dict(type='CREATE', tick=10, data=dict(create_particle=dict(entity_handle=handle, attach_type=3, particle_name_index='123'))),
        dict(type='UPDATE_ENT', tick=10, data=dict(update_particle_ent=dict(control_point=0, entity_handle=handle,
            attach_type=1, attachment=0, include_wearables=True, fallback_position=fallback))),
        dict(type='UPDATE_ENT', tick=10, data=dict(update_particle_ent=dict(control_point=1, entity_handle=handle,
            attach_type=5, attachment=13, include_wearables=True))),
        dict(type='UPDATE_TRANSFORM', tick=10, data=dict(update_particle_transform=dict(control_point=2, position=dict(x=.87))))])
    data = dict(start_tick=10, end_tick=12, tick_rate=64, entities=[entity], particle_instances=[instance],
                metadata=dict(demo_sha256='a'*64))
    record, collection = bytearray(88), bytearray(0xe0)
    struct.pack_into('<i', record, 4, 1)
    struct.pack_into('<12f', record, 0x1c, *sum(birth_module.IDENTITY, []))
    struct.pack_into('<3f', record, 0x10, *fallback.values())
    struct.pack_into('<Q', record, 0x50, runtime)
    struct.pack_into('<Q', collection, 0x60, runtime)
    raw = dict(schema_version=1, policy=birth_module.POLICY, client_sha256=birth_module.CLIENT_SHA256,
        particles_sha256=birth_module.PARTICLES_SHA256, entity_id=entity['id'], resource=instance['resource'],
        created_tick=10, tick=10, raw_capture_sha256='c'*64, demo_sha256='a'*64, model=model['resource'],
        model_sha256=model['resource_sha256'], runtime_owner_handle=runtime, collection_owner_handle=runtime,
        coherent=True, identity_flags=0, skeleton_vtable_rva=0x285dc98,
        model_method='stock_dynamic_component_skeleton_model_getter_v1', owner_srt=[1, 2, 3, 1, 0, 0, 0, 1],
        cp0_record=record.hex(), collection_record=collection.hex())
    child_raw = dict(schema_version=1, policy='observed_parent_attachment_selection_v1', client_sha256=CURRENT_CLIENT_SHA256,
        raw_capture_sha256='d'*64, entity_id=entity['id'], tick=10, model=model['resource'], model_sha256=model['resource_sha256'],
        graph_coherence_policy='double_read_links_class_flags_v1', graph_coherent=True, eligible_render_children=0,
        graph_nodes=1, graph_sha256='e'*64)
    birth = birth_module.bind_silence_capture(raw, data, instance)
    child = bind_client_child_capture(child_raw, data, instance)
    inventory = dict(schema_version=1, model=model['resource'].removesuffix('_c'), model_bone_names=model['names'],
        sources=[dict(route='model', resource=model['resource'].removesuffix('_c'), sha256=model['resource_sha256'])],
        hitbox_sets=[dict(name='default', boxes=[dict(index=0, bone='head', model_bone_index=1,
            shape='Box', min=[-1, -1, -1], max=[1, 1, 1], radius=0)])])
    attachments = {'56:895/13': dict(entity_id=entity['id'], source_model=model['resource'], definition=dict(
        m_name='head_fx', m_nInfluences=1, m_influenceWeights=[1, 0, 0], m_bIgnoreRotation=False,
        m_bInfluenceRootTransform=[False]*3, m_influenceNames=['head', '', ''],
        m_vInfluenceRotations=[[0, 0, 0, 1]], m_vInfluenceOffsets=[[0, 0, 0]]))}
    rig['agr_preview_hidden'] = True
    rig.hide_viewport = True
    scene.frame_set(7, subframe=.25)
    old = (scene.frame_current, scene.frame_subframe, rig.hide_viewport)
    before = {key: set(getattr(bpy.data, key)) for key in ('objects', 'actions', 'materials', 'meshes')}
    args = (data, instance, entity, dict(rig=rig, root=root), scene, case, attachments, inventory, birth, child)
    points, provider = capture_points(*args)
    error = 0.
    for p in points:
        dt = p['tick'] - 10
        error = max(error, abs(p['position'][0] - (100+3*dt)),
                    abs(p['extra_control_points'][0]['position'][0] - (102+4*dt)),
                    abs(p['model_hitboxes'][0]['sets']['default'][0]['transform'][0] - (102+4*dt)))
        assert p['extra_control_points'][1]['position'] == [float(np.float32(.87)), 0, 0]
        assert abs(p['rotation'][2] - math.sqrt(.5)) < 1e-6
    assert error < 2e-5, error
    assert old == (scene.frame_current, scene.frame_subframe, rig.hide_viewport)
    assert all(set(getattr(bpy.data, k)) == v for k, v in before.items())
    provider['normal_inputs_policy'] = NORMAL_POLICY
    package = dict(hitbox_provider=provider, silence_birth_capture=birth, client_child_capture=child,
        simulation=dict(instances=[dict(id=instance['id'])]), simulation_request=dict(instances=[dict(control_points=points)]))
    # Saved recapture compares the complete provider and every input frame.
    bpy.ops.wm.save_as_mainfile(filepath=str(case / 'inputs.blend'), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(case / 'inputs.blend'), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    validate_saved_inputs(package, data, scene, case)
    rig, root = bpy.data.objects['rig'], bpy.data.objects['replay root']
    refused = []
    for kind in ('edited_root', 'missing_pose', 'hidden_ancestor', 'foreign_root', 'invalid_offset'):
        bad_data, bad_attachments = copy.deepcopy(data), copy.deepcopy(attachments)
        bad_entity = bad_data['entities'][0]
        bad_objects = dict(rig=rig, root=root)
        if kind == 'edited_root':
            root.delta_location.x = unit
        if kind == 'missing_pose':
            bad_entity['pose_unavailable_ticks'] = [11]
        if kind == 'hidden_ancestor':
            root.hide_viewport = True
        if kind == 'foreign_root':
            bad_objects['root'] = rig
        if kind == 'invalid_offset':
            bad_attachments['56:895/13']['definition']['m_vInfluenceRotations'][0] = [0, 0, 0, 2]
        try:
            if kind == 'edited_root':
                validate_saved_inputs(package, data, scene, case)
            else:
                capture_points(bad_data, instance, bad_entity, bad_objects, scene, case, bad_attachments, inventory, birth, child)
        except ValueError:
            refused.append(kind)
        finally:
            root.delta_location.x = 0
            root.hide_viewport = False
        assert (scene.frame_current, scene.frame_subframe, rig.hide_viewport) == old
    assert len(refused) == 5, refused
    results.append(dict(fps=fps, frames=len(points), max_source_position_error=error, saved_recapture='PASS', refused=refused,
        inputs_sha256=digest(points), scope='Synthetic pose/input/save only; no actual capture, simulation or render'))
(out / 'RESULT.json').write_text(json.dumps(dict(status='PASS', cases=results), indent=2), encoding='utf-8')
print('SILENCE_INPUT_CHECK_PASS', json.dumps(results))
