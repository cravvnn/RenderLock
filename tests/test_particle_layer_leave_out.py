import pytest

from agr.blender_simulated_effects import leave_out_layers, validate_renderer_classes
from agr.installed_effects import UnsupportedEffect
from agr.particle_owned_broad import POLICY
from agr.particle_standard_light import PARTICLES_SHA, PROFILE_KEY, PROFILE_POLICY

BAD_LIGHT = dict(_class='C_OP_RenderStandardLight', m_vecColorScale={'m_nType': 'PVEC_TYPE_CONTROL_POINT_VALUE'})
GOOD_LIGHT = dict(_class='C_OP_RenderStandardLight')


def assets(*renderers):
    return dict(definitions={'light.vpcf': dict(m_Renderers=list(renderers))},
                provenance={PROFILE_KEY: dict(policy=PROFILE_POLICY, particles_sha256=PARTICLES_SHA)})


TRACKS = {('root', 'path', 'light.vpcf', 0): []}


def test_only_fully_unverified_simulations_leave_layers_out():
    broad = dict(id='7:1', owned_broad_provider=dict(policy=POLICY))
    assert leave_out_layers(dict(instances=[broad]))
    assert not leave_out_layers(dict(instances=[broad, dict(id='8:1')]))
    assert not leave_out_layers(dict(instances=[]))


def test_a_layer_that_fails_its_checks_is_left_out_and_the_rest_kept():
    prepared = assets(BAD_LIGHT, GOOD_LIGHT)
    validate_renderer_classes(TRACKS, prepared, leave_out=True)
    assert list(prepared['omitted_layers']['light.vpcf']) == ['0']
    assert 'color-scale' in prepared['omitted_layers']['light.vpcf']['0']


def test_strict_packages_still_refuse_the_whole_effect():
    with pytest.raises(UnsupportedEffect):
        validate_renderer_classes(TRACKS, assets(BAD_LIGHT, GOOD_LIGHT))
