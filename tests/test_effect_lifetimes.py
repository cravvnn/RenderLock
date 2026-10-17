from copy import deepcopy
import unittest

from agr.effect_lifetimes import maximum_effect_age, partition_expired_history


def burst(life=1):
    return {'_class': 'CParticleSystemDefinition', 'm_nBehaviorVersion': 12, 'm_flConstantLifespan': life,
            'm_Emitters': [{'_class': 'C_OP_InstantaneousEmitter'}],
            'm_Operators': [{'_class': 'C_OP_Decay'}]}


class EffectLifetimeTests(unittest.TestCase):
    def bound(self, d):
        return maximum_effect_age('root', lambda _: d)

    def assertConservative(self, actual, nominal):
        import struct
        source_float = struct.unpack('<f', struct.pack('<f', nominal))[0]
        self.assertGreaterEqual(actual, source_float)
        self.assertLess(actual, nominal + 1e-5)

    def test_default_lifetime_requires_active_decay(self):
        d=burst();d.pop('m_flConstantLifespan')
        self.assertEqual(self.bound(d), 1)
        d['m_Operators']=[]
        self.assertIsNone(self.bound(d))
        d['m_Operators']=[{'_class':'C_OP_Decay','m_nOpEndCapState':'PARTICLE_ENDCAP_ENDCAP_ON'}]
        self.assertIsNone(self.bound(d))

    def test_emission_window_and_max_random_life(self):
        d=burst();d['m_Emitters']=[{'_class':'C_OP_ContinuousEmitter','m_flStartTime':2,'m_flEmissionDuration':3}]
        d['m_Initializers']=[{'_class':'C_INIT_RandomLifeTime','m_fLifetimeMin':.2,'m_fLifetimeMax':.75}]
        self.assertConservative(self.bound(d),5.75)
        d['m_Emitters'][0]['m_flEmissionDuration']=0
        self.assertIsNone(self.bound(d))

    def test_init_float_strength_and_unknown_mapping(self):
        d=burst();d['m_Initializers']=[{'_class':'C_INIT_InitFloat','m_nOutputField':1,
            'm_InputValue':{'m_nType':'PF_TYPE_RANDOM_UNIFORM','m_flRandomMin':.1,'m_flRandomMax':.2},
            'm_InputStrength':2}]
        self.assertIsNone(self.bound(d))  # Extrapolation below zero is unqualified.
        d['m_flConstantLifespan'] = .05
        self.assertConservative(self.bound(d), .35)
        d['m_Initializers'][0]['m_InputValue']['m_nMapType']='PF_MAP_TYPE_CURVE'
        self.assertIsNone(self.bound(d))

    def test_partial_strength_keeps_the_existing_lifespan_contribution(self):
        d = burst(1)
        d['m_Initializers'] = [{'_class': 'C_INIT_InitFloat', 'm_nOutputField': 1,
            'm_InputValue': .1, 'm_InputStrength': .5}]
        self.assertAlmostEqual(self.bound(d), .55)
        d['m_Initializers'][0]['m_InputStrength'] = 0
        self.assertEqual(self.bound(d), 1)

    def test_extrapolated_strength_uses_both_ends_of_prior_random_life(self):
        d = burst()
        d['m_Initializers'] = [
            {'_class': 'C_INIT_RandomLifeTime', 'm_fLifetimeMin': .1, 'm_fLifetimeMax': .9},
            {'_class': 'C_INIT_InitFloat', 'm_nOutputField': 1, 'm_InputValue': .5, 'm_InputStrength': 2}]
        self.assertConservative(self.bound(d), .9)

    def test_complete_tree_child_delay_and_disabled_child(self):
        root=burst(.1);root['m_Children']=[{'m_ChildRef':'child','m_flDelay':2},
                                        {'m_ChildRef':'missing','m_bDisableChild':True}]
        defs={'root':root,'child':burst(.4)}
        self.assertConservative(maximum_effect_age('root',defs.__getitem__),2.4)
        root['m_Children'][0]['m_bEndCap']=True
        self.assertIsNone(maximum_effect_age('root',defs.__getitem__))

    def test_empty_root_children_are_still_bounded(self):
        defs={'root':{'_class':'CParticleSystemDefinition','m_Children':[{'m_ChildRef':'child'}]},'child':burst(.4)}
        self.assertConservative(maximum_effect_age('root',defs.__getitem__),.4)
        defs['child']['m_Children']=[{'m_ChildRef':'root'}]
        self.assertIsNone(maximum_effect_age('root',defs.__getitem__))

    def test_unknown_lifetime_operator_and_suppressed_decay_not_pruned(self):
        for op in ({'_class':'C_OP_Unknown'}, {'_class':'C_OP_SetFloat','m_nOutputField':1}):
            d=burst();d['m_Operators'].append(op)
            self.assertIsNone(self.bound(d))
        for key,value in [('m_flOpStrength',0),('m_flOpStartFadeInTime',2),('m_flOpEndFadeOutTime',1)]:
            d=burst();d['m_Operators'][0][key]=value
            self.assertIsNone(self.bound(d))

    def test_fade_kill_uses_authored_fraction(self):
        d=burst(.4);d['m_Operators']=[{'_class':'C_OP_FadeAndKill','m_flEndFadeOutTime':2}]
        self.assertConservative(self.bound(d),.8)

    def test_early_alpha_fade_is_not_early_particle_death(self):
        d = burst(.4)
        d['m_Operators'] = [{'_class': 'C_OP_FadeAndKill', 'm_flEndFadeOutTime': .25}]
        self.assertConservative(self.bound(d), .4)

    def test_unknown_snapshots_parent_deaths_or_init_retained(self):
        for key,value in [('m_flInitFromKilledParentParticles',1),('m_nSnapshotControlPoint',0),('m_nMaxEmittedPerFrame',1)]:
            d=burst();d['m_Emitters'][0][key]=value
            self.assertIsNone(self.bound(d))
        d=burst();d['m_Initializers']=[{'_class':'C_INIT_Unknown'}]
        self.assertIsNone(self.bound(d))

    def test_attachment_motion_normal_and_color_do_not_extend_lifetime(self):
        d = burst(.1)
        d['m_Initializers'] = [{'_class': 'C_INIT_NormalAlignToCP'}]
        d['m_Operators'] += [{'_class': 'C_OP_PositionLock', 'm_bLockRot': True},
                             {'_class': 'C_OP_ColorInterpolate'}]
        self.assertConservative(self.bound(d), .1)
        for name, field in [('C_OP_PositionLock', 'm_nFieldOutput'),
                            ('C_OP_PositionLock', 'm_nFieldOutputPrev'),
                            ('C_OP_ColorInterpolate', 'm_nFieldOutput')]:
            for value in (1, 8, True, 0.0):
                changed = deepcopy(d)
                next(op for op in changed['m_Operators'] if op['_class'] == name)[field] = value
                self.assertIsNone(self.bound(changed))

    def test_old_bursts_are_pruned_but_quantized_boundary_is_retained(self):
        def event(identity, birth):
            return {'id': identity, 'resource': 'root', 'created_tick': birth,
                    'events': [{'type': 'CREATE', 'tick': birth}, {'type': 'RELEASE', 'tick': birth}]}
        rows = [event('old', 100), event('boundary', 103), event('new', 110)]
        original = deepcopy(rows)
        kept, report = partition_expired_history(rows, 110, 64, lambda _: burst(.1))
        self.assertEqual([i['id'] for i in kept], ['boundary', 'new'])
        self.assertEqual(report['removed_instances'], 1)
        self.assertEqual(rows, original)

    def test_priest_ring_and_rotation_only_operations_do_not_extend_life(self):
        d = burst(.25)
        d['m_Initializers'] = [{'_class': 'C_INIT_RingWave'},
            {'_class': 'C_INIT_InitFloat', 'm_nOutputField': 5}]
        d['m_Operators'] += [{'_class': 'C_OP_SpinUpdate'},
            {'_class': 'C_OP_RemapSpeed', 'm_nFieldOutput': 5},
            {'_class': 'C_OP_RampScalarSpline', 'm_nField': 4}]
        self.assertEqual(self.bound(d), .25)
        for kind, field in [('C_OP_RemapSpeed', 'm_nFieldOutput'), ('C_OP_RampScalarSpline', 'm_nField')]:
            for value in (1, 8, True, 4.0):
                changed = deepcopy(d)
                next(op for op in changed['m_Operators'] if op['_class'] == kind)[field] = value
                self.assertIsNone(self.bound(changed))

    def test_float_rounding_cannot_prune_an_extrapolated_lifespan_early(self):
        d = burst(.05)
        d['m_Initializers'] = [{'_class': 'C_INIT_InitFloat', 'm_nOutputField': 1,
            'm_InputValue': 1_000_000, 'm_InputStrength': 2}]
        # Actual compiled Single.Lerp returns 2,000,000. Ideal double arithmetic
        # gives 1,999,999.95: a full replay-tick guard would not cover the error.
        self.assertGreaterEqual(self.bound(d), 2_000_000)

    def test_single_precision_overflow_stays_unqualified(self):
        self.assertIsNone(self.bound(burst(1e100)))

    def test_frozen_deferred_or_distance_gated_clocks_cannot_expire_by_replay_age(self):
        for field, value in [('m_flStopSimulationAfterTime', .01), ('m_flPreSimulationTime', 2),
                ('m_flMinimumSimTime', .1), ('m_flMaximumSimTime', .1),
                ('m_flMinimumTimeStep', .1), ('m_flMaximumTimeStep', .0001),
                ('m_flMaxDrawDistance', 100)]:
            d = burst(.05); d[field] = value
            self.assertIsNone(self.bound(d), field)

    def test_emitterless_initial_particles_are_not_an_empty_container(self):
        d = {'_class': 'CParticleSystemDefinition', 'm_nInitialParticles': 1}
        self.assertIsNone(self.bound(d))

    def test_legacy_first_writer_cannot_be_replaced_by_a_shorter_second_lifetime(self):
        d = burst()
        d['m_Initializers'] = [{'_class': 'C_INIT_InitFloat', 'm_nOutputField': 1,
            'm_InputValue': life} for life in (.9, .1)]
        for behavior in (0, 5):
            d['m_nBehaviorVersion'] = behavior
            self.assertConservative(self.bound(d), .9)
        d['m_nFirstMultipleOverride_BackwardCompat'] = 1
        self.assertConservative(self.bound(d), .1)
        d['m_nBehaviorVersion'] = 12
        self.assertConservative(self.bound(d), .1)

    def test_freezes_seeks_and_invalid_clocks_cannot_be_pruned(self):
        base = {'id': 'old', 'resource': 'root', 'created_tick': 0,
                'events': [{'type': 'CREATE', 'tick': 0}]}
        for event in ({'type': 'SET_FROZEN', 'tick': 1}, {'type': 'SKIP_TO_TIME', 'tick': 1},
                      {'type': 'UNKNOWN', 'tick': 1}, {'type': 'CREATE', 'tick': 0},
                      {'type': 'UPDATE_TRANSFORM', 'tick': -1}, {'type': 'RELEASE', 'tick': True}):
            row = deepcopy(base); row['events'].append(event)
            kept, report = partition_expired_history([row], 1000, 64, lambda _: burst(.1))
            self.assertEqual(kept, [row])
            self.assertEqual(report['removed_instances'], 0)

    def test_release_without_a_finite_death_proof_remains_unknown(self):
        row = {'id': 'old', 'resource': 'root', 'created_tick': 0,
               'events': [{'type': 'CREATE', 'tick': 0}, {'type': 'RELEASE', 'tick': 0}]}
        d = burst(); d['m_Operators'] = []
        kept, report = partition_expired_history([row], 100000, 64, lambda _: d)
        self.assertEqual(kept, [row])
        self.assertEqual(report['removed_instances'], 0)


if __name__=='__main__':unittest.main()
