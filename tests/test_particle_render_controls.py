import copy
import math
import pytest

from agr.installed_effects import UnsupportedEffect
from agr.particle_render_controls import render_contexts, render_scalar, SNAPSHOT_POLICY


def snapshot():
    return dict(control_point_snapshot_policy=SNAPSHOT_POLICY, instances=[dict(
        id='5706:1', control_point_indices=[0, 5], materialized_control_point_indices=[0, 5],
        frames=[dict(tick=18070, systems=[dict(system_path=path, system_age=.125,
            control_points=[dict(index=0, position=[100, 200, 300]),
                            dict(index=5, position=[value, 0, 0])])
            for path, value in [('root', 1), ('root/0', .25), ('root/1', 1)]])])])


def test_crossbow_flash_alpha_uses_recorded_switch_and_child_specific_values():
    contexts = render_contexts(snapshot())
    # Installed burst has two opposing CP5 alpha providers. The packet gives
    # CP5.x=1: one layer must be hidden while the other remains visible.
    direct = dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT', m_nControlPoint=5)
    inverse = dict(direct, m_nMapType='PF_MAP_TYPE_REMAP', m_flInput0=0,
                   m_flInput1=1, m_flOutput0=1, m_flOutput1=0)
    root = contexts['5706:1', 'root', 18070]
    child = contexts['5706:1', 'root/0', 18070]
    sibling = contexts['5706:1', 'root/1', 18070]
    assert (render_scalar(direct, context=root), render_scalar(inverse, context=root)) == (1, 0)
    assert render_scalar(inverse, context=child) == .75
    assert render_scalar(direct, context=sibling) == 1
    assert render_scalar(dict(m_nType='PF_TYPE_COLLECTION_AGE'), context=child) == .125


def test_legacy_simulations_keep_literals_but_cannot_guess_missing_control_values():
    assert render_contexts(dict(instances=[])) == {}
    assert render_scalar(3) == 3
    with pytest.raises(UnsupportedEffect, match='snapshot'):
        render_scalar(dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT', m_nControlPoint=5))


@pytest.mark.parametrize('change', [
    lambda s: s.update(control_point_snapshot_policy='unknown'),
    lambda s: s['instances'][0].update(materialized_control_point_indices=[0, 5, 9]),
    lambda s: s['instances'][0]['frames'][0]['systems'][0].pop('control_points'),
    lambda s: s['instances'][0]['frames'][0]['systems'][0]['control_points'].append(dict(index=5, position=[0, 0, 0])),
    lambda s: s['instances'][0]['frames'][0]['systems'][0]['control_points'][1].update(index=9),
    lambda s: s['instances'][0]['frames'][0]['systems'][0]['control_points'][1].update(position=[math.nan, 0, 0]),
    lambda s: s['instances'][0]['frames'][0]['systems'][0].update(system_age=-1),
])
def test_unqualified_or_incomplete_snapshots_cannot_render_as_success(change):
    source = snapshot()
    change(source)
    with pytest.raises(UnsupportedEffect):
        render_contexts(source)


def test_reading_a_missing_point_does_not_materialize_it_or_borrow_another_child():
    source = snapshot()
    source['instances'][0]['frames'][0]['systems'][0]['control_points'].pop()
    original = copy.deepcopy(source)
    context = render_contexts(source)['5706:1', 'root', 18070]
    with pytest.raises(UnsupportedEffect, match='snapshot'):
        render_scalar(dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT', m_nControlPoint=5), context=context)
    assert source == original


def test_remap_retains_descending_pairing_clamps_and_handles_equal_endpoints():
    provider = dict(m_nType='PF_TYPE_LITERAL', m_flLiteralValue=3,
        m_nMapType='PF_MAP_TYPE_REMAP', m_flInput0=2, m_flInput1=0, m_flOutput0=10, m_flOutput1=20)
    assert render_scalar(provider) == 10
    assert render_scalar(dict(provider, m_flLiteralValue=1)) == 15
    assert render_scalar(dict(provider, m_flInput0=0, m_flInput1=0, m_flLiteralValue=-1)) == 10
    assert render_scalar(dict(provider, m_flInput0=0, m_flInput1=0, m_flLiteralValue=0)) == 20
    assert render_scalar(dict(provider, m_nMapType='PF_MAP_TYPE_MULT', m_flMultFactor=4)) == 12


@pytest.mark.parametrize('provider', [
    dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT', m_nControlPoint=True),
    dict(m_nType='PF_TYPE_CONTROL_POINT_COMPONENT', m_nVectorComponent=3),
    dict(m_nType='PF_TYPE_RANDOM_UNIFORM'),
    dict(m_nType='PF_TYPE_LITERAL', m_flLiteralValue=True),
    dict(m_nType='PF_TYPE_LITERAL', m_nMapType='PF_MAP_TYPE_REMAP_BIASED'),
])
def test_unsupported_provider_semantics_are_not_silently_coerced(provider):
    with pytest.raises(UnsupportedEffect):
        render_scalar(provider, context=render_contexts(snapshot())['5706:1', 'root', 18070])
