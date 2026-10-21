"""Actor names are cosmetic: source rig, mesh and model-track identities survive."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agr.blender_object_names import (
    ORIGINAL_NAME_PROPERTY, actor_label, bounded_name, object_part,
    refresh_secondary_motion_names, rename_imported_objects,
)


class Object(dict):
    def __init__(self, name, kind='MESH'):
        super().__init__()
        self.name, self.type = name, kind
        self.data = object()


class ObjectNameTests(unittest.TestCase):
    def test_existing_catalog_overrides_source_nickname_with_unknown_fallback(self):
        self.assertEqual(actor_label(dict(id='7:2', hero_id='76', hero_name='necro')),
                         'Graves [7:2]')
        self.assertEqual(actor_label(dict(id='7:2', hero_id=999, hero_name='Future Hero')),
                         'Future Hero [7:2]')
        self.assertEqual(actor_label(dict(id='7:2', hero_id=999)), 'Hero 999 [7:2]')

    def test_path_model_resource_and_part_suffixes(self):
        for name in (r'\input\models\heroes_wip\necro\necro.vmdl_c.head',
                     '/input/models/heroes_wip/necro/necro.vmdl.head'):
            self.assertEqual(object_part(name, 'MESH'), 'head')
        self.assertEqual(object_part('necro.vmdl_c.head.001', 'MESH'), 'head.001')
        self.assertEqual(object_part('necro.vmdl_c', 'ARMATURE'), 'armature')
        self.assertEqual(object_part('Head attachment', 'EMPTY'), 'Head attachment')

    def test_collisions_are_deterministic_for_unordered_imports_and_external_names(self):
        names = [r'\a\necro.vmdl_c.head', r'\b\necro.vmdl_c.head',
                 r'\c\necro.vmdl_c.head.001']
        def renamed(order):
            objects = [Object(name) for name in order]
            result = rename_imported_objects(objects, 'Graves [7:2]',
                                             names + ['Graves [7:2] | head'])
            self.assertEqual(len(set(result.values())), len(names))
            self.assertNotIn('Graves [7:2] | head', result.values())
            return result
        self.assertEqual(renamed(names), renamed(reversed(names)))

    def test_unicode_bounds_preserve_part_tail_and_hash_distinct_middle_text(self):
        a = 'Graves [' + '界' * 60 + 'A' + '] | head.001'
        b = a.replace('A]', 'B]')
        self.assertNotEqual(bounded_name(a), bounded_name(b))
        for name in (a, b):
            self.assertLessEqual(len(bounded_name(name).encode('utf-8')), 63)
            self.assertTrue(bounded_name(name).startswith('Graves ['))
            self.assertTrue(bounded_name(name).endswith('head.001'))
        objects = [Object(a), Object(b)]
        rename_imported_objects(objects, '界' * 60, [])
        self.assertTrue(all(len(obj.name.encode('utf-8')) <= 63 for obj in objects))

    def test_only_owned_object_names_change_and_originals_survive_repeat(self):
        mesh = Object(r'\input\necro.vmdl_c.head')
        camera = Object('Imported camera', 'CAMERA')
        unrelated = Object('Map/reference')
        data = mesh.data
        rename_imported_objects([mesh, camera], 'Graves [7:2]',
                                [mesh.name, camera.name, unrelated.name])
        first = mesh.name
        self.assertEqual(first, 'Graves [7:2] | head')
        self.assertEqual(mesh[ORIGINAL_NAME_PROPERTY], r'\input\necro.vmdl_c.head')
        rename_imported_objects([mesh], 'Graves [7:2]', [mesh.name, unrelated.name])
        self.assertEqual(mesh.name, first)
        self.assertIs(mesh.data, data)
        self.assertEqual(camera.name, 'Imported camera')
        self.assertEqual(unrelated.name, 'Map/reference')

    def test_model_form_labels_are_distinct_with_one_logical_actor(self):
        common = dict(hero_id=80, hero_name='werewolf')
        labels = [actor_label(dict(common, id=identity, model_track=dict(
            source_entity_id='7:2', start_tick=tick)))
            for identity, tick in (('7:2', 100), ('7:2@model:102', 102))]
        self.assertEqual(labels, ['Silver [7:2] | model 100', 'Silver [7:2] | model 102'])
        self.assertEqual(actor_label(dict(common, id='7:2')), 'Silver [7:2]')
        self.assertNotEqual(actor_label(dict(common, id='7:3')), 'Silver [7:2]')

    def test_cloth_report_references_follow_renames_without_renaming_bones(self):
        report = dict(objects=['old'], parts=[dict(objects=['old'], proxy='proxy')],
                      affected_bones=['old'], reason='old')
        refresh_secondary_motion_names(report, {'old': 'Graves | head', 'proxy': 'new proxy'})
        self.assertEqual(report['objects'], ['Graves | head'])
        self.assertEqual(report['parts'][0]['proxy'], 'new proxy')
        self.assertEqual(report['affected_bones'], ['old'])
        self.assertEqual(report['reason'], 'old')


@unittest.skipUnless(importlib.util.find_spec('bpy'), 'Requires headless Blender')
class BlenderObjectNameTests(unittest.TestCase):
    def test_saved_fixture_preserves_rig_pose_mesh_contract_and_shared_forms(self):
        import bpy
        import numpy as np
        from agr.blender_scene import build_scene
        from agr.asset_converter import sha
        from agr.model_tracks import expand_model_tracks
        from agr.source_hero_lighting_package import canonical_sha

        # Synthetic two-rig triangle asset: no installed/replay assets or conversion.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        contract = dict(schema_version=1, mode='source_mesh_group_visibility',
            default_mask='1', unconditional=False,
            meshes=[dict(source_mesh_index=0, mesh_group_mask='1')])
        contract['content_sha256'] = canonical_sha(contract)
        collection = bpy.context.scene.collection
        rigs = []
        for name in ('SourceRig', 'UnusedRig'):
            rig = bpy.data.objects.new(name, bpy.data.armatures.new(name + ' data'))
            collection.objects.link(rig)
            bpy.context.view_layer.objects.active = rig
            rig.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT')
            bone = rig.data.edit_bones.new('Root')
            bone.head, bone.tail = (0, 0, 0), (0, 1, 0)
            bpy.ops.object.mode_set(mode='OBJECT')
            rig.select_set(False)
            rigs.append(rig)
        mesh = bpy.data.meshes.new('Source mesh identity')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 2)], [], [(0, 1, 2)])
        coat = bpy.data.objects.new(r'\input\models\necro\necro.vmdl_c.body', mesh)
        collection.objects.link(coat)
        coat.parent = rigs[0]
        coat.vertex_groups.new(name='Root').add([0, 1, 2], 1., 'REPLACE')
        coat.modifiers.new('Skin binding', 'ARMATURE').object = rigs[0]
        material = bpy.data.materials.new('Source material identity')
        mesh.materials.append(material)
        for key, value in dict(agr_source_mesh_index=0, agr_mesh_group_mask='1',
            agr_mesh_default_mask='1', agr_mesh_groups_unconditional=False,
            agr_mesh_group_contract=contract['content_sha256']).items():
            coat[key] = value
        control = bpy.data.objects.new(r'\input\models\necro\attachment', None)
        collection.objects.link(control)
        control.parent = rigs[0]
        constraint = control.constraints.new('COPY_LOCATION')
        constraint.target, constraint.subtarget = rigs[0], 'Root'

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            asset = directory / 'fixture.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(asset))
            contract['model_sha256'] = sha(asset)
            (directory / 'skeleton.json').write_text(json.dumps(dict(names=['Root'], parents=[-1])))
            np.savez(directory / 'pose.npz', ticks=np.array([100, 101, 102]),
                     q=np.tile([0., 0., 0., 1.], (3, 1, 1)),
                     t=np.array([[[0., 0., z]] for z in (0., 1., 2.)]), s=np.ones((3, 1)))
            def actor(identity, hero_id, handles):
                return dict(id=identity, entity_index=int(identity.split(':')[0]), entity_serial=2,
                    hero_id=hero_id, hero_name='necro' if hero_id == 76 else 'werewolf',
                    model_handle=handles[0], samples=[dict(tick=tick, model_handle=handle,
                        position=[tick - 100, 0, 0], present=True, mesh_group_mask='1')
                        for tick, handle in zip(range(100, 103), handles)])
            data = dict(schema_version=1, start_tick=100, end_tick=102, tick_rate=64,
                        entities=[actor('7:2', 76, ['11'] * 3), actor('8:2', 80, ['22', '22', '33'])])
            expand_model_tracks(data)
            for entity in data['entities']:
                entity.update(model_file=str(asset), asset_objects=['SourceRig', 'UnusedRig'],
                    rig_object='SourceRig', skeleton_file=str(directory / 'skeleton.json'),
                    pose_file=str(directory / 'pose.npz'), require_weighted_bone_mapping=True,
                    model_mesh_groups=contract, secondary_motion_file='fixture-cloth.json')
            segment, output = directory / 'segment.json', directory / 'named.blend'
            segment.write_text(json.dumps(data))
            cloth_calls = []
            def cloth(rig, *args, **kwargs):
                # Original source suffixes and selected rig still exist at this stage.
                self.assertTrue(rig.name.startswith('SourceRig'))
                candidates = [obj for obj in rig.children if obj.type == 'MESH']
                self.assertEqual(len(candidates), 1)
                self.assertTrue(candidates[0].name.endswith('.body'))
                cloth_calls.append(rig)
                return dict(status='fixture_only', objects=[candidates[0].name])
            with patch('agr.blender_cloth.bake_secondary_motion', side_effect=cloth):
                build_scene(segment, None, output, shot_hero=80)
            self.assertEqual(len(cloth_calls), 3)
            report = json.loads(output.with_suffix('.scene.json').read_text())
            self.assertEqual(report['camera']['entity_id'], '8:2')
            self.assertEqual(len({row['root'] for row in report['entities']}), 2)
            self.assertEqual(report['entities'][1]['root'], report['entities'][2]['root'])
            self.assertEqual({row['pose']['status'] for row in report['entities']}, {'baked'})
            self.assertEqual({row['mesh_groups']['status'] for row in report['entities']},
                             {'baked_constant_visibility'})
            bpy.ops.wm.open_mainfile(filepath=str(output))
            originals = [obj for obj in bpy.data.objects if ORIGINAL_NAME_PROPERTY in obj]
            self.assertEqual(len(originals), 12)  # mesh, two rigs, one empty per track.
            for obj in originals:
                self.assertTrue(obj.name.startswith(('Graves [7:2]', 'Silver [8:2]')))
                self.assertLessEqual(len(obj.name.encode('utf-8')), 63)
                self.assertNotIn('\\', obj.name)
                self.assertEqual(obj.get('agr_entity_id'), '7:2' if obj.name.startswith('Graves') else '8:2')
                if obj.type == 'MESH':
                    self.assertEqual(obj.data.materials[0].name.split('.00')[0], 'Source material identity')
                    self.assertEqual(list(obj.vertex_groups.keys()), ['Root'])
                    self.assertEqual(obj['agr_source_mesh_index'], 0)
                    self.assertIn('SourceRig', obj.modifiers[0].object[ORIGINAL_NAME_PROPERTY])
                elif obj.type == 'EMPTY':
                    self.assertIn('SourceRig', obj.constraints[0].target[ORIGINAL_NAME_PROPERTY])
                    self.assertEqual(obj.constraints[0].subtarget, 'Root')
            for row in report['entities']:
                rig = bpy.data.objects[row['rig']]
                self.assertIn('SourceRig', rig[ORIGINAL_NAME_PROPERTY])
                self.assertEqual(list(rig.data.bones.keys()), ['Root'])
                for tick in (100, 101, 102):
                    bpy.context.scene.frame_set(tick - 99)
                    self.assertAlmostEqual(rig.pose.bones['Root'].matrix.translation.z,
                                           (tick - 100) * .0254, places=6)
                self.assertIn(row['secondary_motion']['objects'][0], bpy.data.objects)
            self.assertIn('AGR | reference grid and lighting', bpy.data.collections)
            self.assertIn('AGR | overview camera', bpy.data.objects)
            self.assertEqual({obj.name for obj in bpy.data.collections if obj.name.startswith('Silver')},
                             {'Silver [8:2] | model 100', 'Silver [8:2] | model 102'})


if __name__ == '__main__':
    unittest.main()
