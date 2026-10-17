"""Physical invariants and fail-closed boundaries for the Haze hose prototype."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from agr.blender_cloth import _capsule_contact, solve_hoses
from agr.installed_cloth import HOSE_NAMES, UnsupportedCloth, haze_definition, prepare_cloth_assets


def authored_fixture():
    # A small artificial FE fixture, not redistributed game asset data.
    p = [[-1000-i*100, 0, 0] for i in range(4)]
    for i in range(4):
        p.extend([[i*100-10, 0, 10], [i*100+10, 0, 10]])
    p.extend([[i*100, 5, 10] for i in range(4)])
    rods = []
    bases = []
    for i in range(4):
        node, first = 12+i, 4+2*i
        for anchor in (first, first+1):
            length = float(np.linalg.norm(np.array(p[node])-p[anchor]))
            rods.append({'nNode': [anchor, node], 'flMinDist': length, 'flMaxDist': length})
        bases.append({'nNode': node, 'nNodeX0': first+1, 'nNodeX1': first,
                      'nNodeY0': node, 'nNodeY1': first+1})
    return {'m_pFeModel': {
        'm_CtrlName': list(HOSE_NAMES), 'm_nNodeCount': 16, 'm_nStaticNodes': 12,
        'm_nFirstPositionDrivenNode': 16, 'm_InitPose': [[*pos, 1, 0, 0, 0, 1] for pos in p],
        'm_NodeInvMasses': [0.]*12+[1.]*4, 'm_Rods': rods, 'm_NodeBases': bases,
        'm_AnimStrayRadii': [{'nNode': [i, i], 'flMaxDist': 3} for i in range(12, 16)],
        'm_NodeIntegrator': [{'flPointDamping': 0, 'flAnimationForceAttraction': 0,
                              'flAnimationVertexAttraction': 0, 'flGravity': 360} for _ in p],
        'm_TaperedCapsuleRigids': [{'nNode': i, 'nFlags': 0, 'nVertexMapIndex': 65535,
                                  'nCollisionMask': 15, 'vSphere': [[0, 0, 0, 1], [0, 0, 1, 1]]}
                                 for i in range(4)]}}


def samples(count=48):
    definition = haze_definition(authored_fixture())
    points = np.array(definition['initial_pose'])[:, :3]*.0254
    targets = np.repeat(points[None], count, axis=0)
    caps = np.empty((count, 4, 2, 4))
    for cap in range(4):
        caps[:, cap, :, :3] = points[cap]
        caps[:, cap, :, 3] = .0254
    return definition, targets, caps


def test_source_definition_rejects_garments_and_new_features():
    changed = authored_fixture()
    changed['m_pFeModel']['m_CtrlName'][12] = '$cloth_m1p4'
    with pytest.raises(UnsupportedCloth, match='garments'):
        haze_definition(changed)
    changed = authored_fixture()
    changed['m_pFeModel']['m_FitMatrices'] = [{}]
    with pytest.raises(UnsupportedCloth, match='unsupported feature'):
        haze_definition(changed)


@pytest.mark.parametrize('mutation', ['duplicate_rod', 'rest_length', 'static_mass', 'capsule_filter', 'nonfinite', 'new_attraction'])
def test_changed_source_contract_is_rejected(mutation):
    fe = authored_fixture()['m_pFeModel']
    if mutation == 'duplicate_rod':
        fe['m_Rods'][-1] = copy.deepcopy(fe['m_Rods'][0])
    elif mutation == 'rest_length':
        fe['m_Rods'][0]['flMaxDist'] += 1
    elif mutation == 'static_mass':
        fe['m_NodeInvMasses'][0] = 1
    elif mutation == 'capsule_filter':
        fe['m_TaperedCapsuleRigids'][0]['nCollisionMask'] = 1
    elif mutation == 'nonfinite':
        fe['m_InitPose'][12][0] = float('nan')
    elif mutation == 'new_attraction':
        fe['m_NodeIntegrator'][12]['flAnimationForceAttraction'] = .5
    with pytest.raises(UnsupportedCloth):
        haze_definition({'m_pFeModel': fe})


def test_gravity_moves_only_free_controls_and_is_reproducible():
    definition, targets, caps = samples()
    solved, report = solve_hoses(definition, targets, caps, 64)
    again, _ = solve_hoses(definition, targets, caps, 64)
    assert np.array_equal(solved, again)
    assert np.array_equal(solved[:, :12], targets[:, :12])
    assert report['max_displacement_m'] > .0001
    assert report['max_stray_excess_m'] < 1e-12
    assert report['max_capsule_penetration_m'] == 0
    assert report['max_rod_residual_m'] < .001
    assert np.isfinite(solved).all()


def test_translating_coordinate_origin_does_not_change_solution():
    definition, targets, caps = samples(16)
    expected, _ = solve_hoses(definition, targets, caps, 64)
    translation = np.array([163., 123., 13.])
    shifted_caps = caps.copy()
    shifted_caps[:, :, :, :3] += translation
    actual, _ = solve_hoses(definition, targets+translation, shifted_caps, 64)
    assert np.allclose(actual-translation, expected, atol=1e-10)


def test_moving_pins_are_exact_and_teleport_restarts_are_reported():
    definition, targets, caps = samples(16)
    targets[:, :12, 0] += np.arange(16)[:, None]*.001
    targets[8:] += [5, 0, 0]
    caps[8:, :, :, :3] += [5, 0, 0]
    actual, report = solve_hoses(definition, targets, caps, 64)
    assert np.array_equal(actual[:, :12], targets[:, :12])
    assert report['reset_sample_indices'] == [8]


def test_tapered_capsule_projects_outside_and_handles_axis_point():
    ends = np.array([[0., 0., 0., .2], [0., 0., 1., .4]])
    for point in (np.array([.1, 0., .5]), np.array([0., 0., .5])):
        depth, normal = _capsule_contact(point, ends)
        assert depth > 0 and np.isfinite(normal).all()
        result = point + normal*depth
        assert _capsule_contact(result, ends)[0] < 1e-9


def test_incompatible_capsule_is_reported_without_dropping_stray_bound():
    definition, targets, caps = samples(8)
    caps[:, 0, :, :3] = targets[:, 12, None, :]
    caps[:, 0, :, 3] = .5
    _, report = solve_hoses(definition, targets, caps, 64)
    assert report['max_capsule_penetration_m'] > .4
    assert report['max_stray_excess_m'] < 1e-12


def test_invalid_samples_are_not_baked():
    definition, targets, caps = samples(2)
    targets[0, 12, 0] = float('nan')
    with pytest.raises(UnsupportedCloth, match='Invalid secondary'):
        solve_hoses(definition, targets, caps, 64)


def test_preparation_keeps_unsupported_entities_explicit(tmp_path, monkeypatch):
    def fake_decode(resource, provider, decoder):
        return (authored_fixture() if resource.endswith('haze.vmdl_c') else {}), 'test_sha'
    monkeypatch.setattr('agr.installed_cloth.decode_physics', fake_decode)
    segment = {'entities': [{'id': '1', 'hero_name': 'Haze'}, {'id': '2', 'hero_name': 'Bebop',
                            'secondary_motion_file': 'stale.json'}]}
    bindings = [{'id': '1', 'resource': 'models/haze.vmdl_c'},
                {'id': '2', 'resource': 'models/bebop.vmdl_c'}]
    report = prepare_cloth_assets(segment, bindings, None, None, tmp_path, log=lambda _: None, include_hose_prototype=True)
    assert report['status'] == 'prepared_partial'
    assert len(report['supported_entities']) == len(report['unsupported_entities']) == 1
    assert Path(segment['entities'][0]['secondary_motion_file']).is_file()
    assert 'secondary_motion_file' not in segment['entities'][1]
    assert json.loads((tmp_path/'secondary-motion/report.json').read_text()) == report


def test_public_cloth_option_does_not_enable_hose_prototype(tmp_path, monkeypatch):
    monkeypatch.setattr('agr.installed_cloth.decode_physics',
                        lambda *args: (authored_fixture(), 'test_sha'))
    segment = {'entities': [{'id': '1', 'hero_name': 'Haze',
                             'secondary_motion_file': 'previous-hose-bake.json'}]}
    report = prepare_cloth_assets(segment, [{'id': '1', 'resource': 'models/haze.vmdl_c'}],
                                  None, None, tmp_path, log=lambda _: None)
    assert report['status'] == 'no_supported_entities'
    assert not report['supported_entities']
    assert 'not enabled' in report['unsupported_entities'][0]['reason']
    assert 'secondary_motion_file' not in segment['entities'][0]
