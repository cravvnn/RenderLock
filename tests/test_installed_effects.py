import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from agr.installed_effects import (UnsupportedEffect, attachment_binding, classify_layer,
                                  fade, hermite, layer_particles, scalar)
from agr.blender_effects import particle_state, _texture_sequence


def definition():
    return {'_class': 'CParticleSystemDefinition', 'm_nMaxParticles': 4,
            'm_Emitters': [{'_class': 'C_OP_InstantaneousEmitter', 'm_nParticlesToEmit': 1}],
            'm_Initializers': [{'_class': 'C_INIT_InitFloat', 'm_nOutputField': 1,
                                'm_InputValue': .1}, {'_class': 'C_INIT_InitFloat', 'm_InputValue': 10}],
            'm_Operators': [{'_class': 'C_OP_PositionLock'}, {'_class': 'C_OP_FadeAndKill'}],
            'm_Renderers': [{'_class': 'C_OP_RenderSprites',
                             'm_vecTexturesInput': [{'m_hTexture': 'materials/test.vtex'}],
                             'm_nOutputBlendMode': 'PARTICLE_OUTPUT_BLEND_MODE_ADD'}]}


def instance():
    return {'id': '20:1', 'resource': 'particles/test.vpcf', 'created_tick': 100,
            'destroy_tick': None, 'destroy_immediately': False, 'release_ticks': [100]}


class InstalledEffectsTests(unittest.TestCase):
    def test_release_keeps_authored_lifetime(self):
        p = layer_particles(definition(), instance(), 64, 300)[0]
        self.assertAlmostEqual(p['end_tick'], 106.4)

    def test_immediate_destroy_clips_lifetime(self):
        event = dict(instance(), destroy_tick=102, destroy_immediately=True)
        self.assertEqual(layer_particles(definition(), event, 64, 300)[0]['end_tick'], 102)

    def test_nonimmediate_destroy_keeps_burst_tail(self):
        event = dict(instance(), destroy_tick=102, destroy_immediately=False)
        self.assertAlmostEqual(layer_particles(definition(), event, 64, 300)[0]['end_tick'], 106.4)

    def test_authored_age_does_not_restart_at_clip_start(self):
        d, event = definition(), instance()
        p = layer_particles(d, event, 64, 300)[0]
        at = particle_state(d, p, p['renderers'][0], event, 106, 64)
        self.assertAlmostEqual(at['alpha'], .125)

    def test_unsupported_emitters_never_get_fallback_sprite(self):
        d = definition()
        d['m_Emitters'][0]['_class'] = 'C_OP_ContinuousEmitter'
        with self.assertRaisesRegex(UnsupportedEffect, 'instantaneous'):
            layer_particles(d, instance(), 64, 300)

    def test_unknown_operator_does_not_render(self):
        d = definition()
        d['m_Operators'].append({'_class': 'C_OP_CollideWithWorld'})
        with self.assertRaisesRegex(UnsupportedEffect, 'CollideWithWorld'):
            classify_layer(d)

    def test_changing_attachment_is_rejected(self):
        e = instance()
        e['events'] = [{'data': {'update_particle_ent': {'control_point': 0, 'entity_handle': 5455956,
                                                      'attach_type': 5, 'attachment': index}}} for index in (7, 8)]
        with self.assertRaisesRegex(UnsupportedEffect, 'changes'):
            attachment_binding(e)

    def test_source_attachment_point_is_not_guessed(self):
        e = instance()
        e['events'] = [{'data': {'update_particle_ent': {'control_point': 0, 'entity_handle': 5455956,
                                                      'attach_type': 1, 'attachment': 0}}}]
        with self.assertRaisesRegex(UnsupportedEffect, 'type 5'):
            attachment_binding(e)

    def test_reproducible_variation(self):
        d = definition()
        d['m_Initializers'][0]['m_InputValue'] = {'m_nType': 'PF_TYPE_RANDOM_UNIFORM', 'm_flRandomMin': .05, 'm_flRandomMax': .1}
        self.assertEqual(layer_particles(d, instance(), 64, 300), layer_particles(d, instance(), 64, 300))

    def test_nonfinite_and_nonliteral_values_rejected(self):
        for value in (math.inf, {'m_nType': 'PF_TYPE_PARTICLE_SPEED'}):
            with self.assertRaises(UnsupportedEffect):
                scalar(value)

    def test_curve_matches_endpoints(self):
        points = [{'x': 0, 'y': 0, 'm_flSlopeOutgoing': 1}, {'x': 1, 'y': 1, 'm_flSlopeIncoming': 1}]
        self.assertEqual([hermite(points, v) for v in (-1, 0, .5, 1, 2)], [0, 0, .5, 1, 1])

    def test_malformed_curve_rejected(self):
        with self.assertRaises(UnsupportedEffect):
            hermite([{'x': 1, 'y': 1}, {'x': 1, 'y': 2}], .5)

    def test_real_atlas_missing_sequence_has_no_fallback(self):
        atlas = {'is_sprite_sheet': True, 'sequences': {'0': {'status': 'supported', 'frames': [{'image': 'seq0.png'}]}}}
        with self.assertRaises(UnsupportedEffect):
            _texture_sequence(atlas, 8)
        atlas['is_sprite_sheet'] = False
        self.assertEqual(_texture_sequence(atlas, 8), 'seq0.png')

    def test_animated_atlas_has_no_first_frame_substitute(self):
        atlas = {'is_sprite_sheet': True, 'sequences': {'0': {'status': 'unsupported', 'frames': [{'image': 'a.png'}, {'image': 'b.png'}]}}}
        with self.assertRaises(UnsupportedEffect):
            _texture_sequence(atlas, 0)

    def test_fractional_particle_death_does_not_shift_integer_attachment_pose(self):
        try:
            import bpy
        except ImportError:
            self.skipTest('Run with Blender Python for attachment regression')
        from agr.blender_effects import build_effects, _linear
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.object.armature_add()
        rig = bpy.context.object
        rig.location.y = 0
        rig.keyframe_insert('location', frame=1)
        rig.location.y = 55
        rig.keyframe_insert('location', frame=56)
        _linear(rig)
        scene = bpy.context.scene
        scene.frame_end = 56
        # The former helper bake inserted this particle's fractional death as a
        # transform key. Blender merged it with frame55, moving the muzzle .005m.
        event = {'id': '1:1', 'resource': 'particles/test.vpcf', 'created_tick': 0}
        particle = {'born_tick': 0, 'end_tick': 54.005, 'renderers': []}
        manifest = {'schema_version': 1, 'textures': {}, 'limitations': [],
                    'attachments': {'1:1/1': {'entity_id': '1:1', 'index': 1,
                        'definition': {'m_name': 'muzzle', 'm_influenceNames': ['Bone'],
                            'm_vInfluenceRotations': [[0, 0, 0, 1]], 'm_vInfluenceOffsets': [[0, 0, 0]]}}},
                    'layers': [{'attachment': '1:1/1', 'kind': 'attachment_burst',
                                'instance': event, 'resource': event['resource'], 'particles': [particle]}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'effects.json'
            path.write_text(json.dumps(manifest))
            data = {'start_tick': 0, 'end_tick': 55, 'tick_rate': 64,
                    'effects': {'manifest_file': str(path)}}
            report = build_effects(data, temp, scene, None, {'1:1': {'rig': rig}}, 64)
        self.assertEqual(report['attachment_samples'], 56)
        scene.frame_set(55)
        helper = next(o for o in bpy.data.objects if o.get('agr_attachment_index'))
        self.assertAlmostEqual(helper.matrix_world.translation.y, 54, places=6)


if __name__ == '__main__':
    unittest.main()
