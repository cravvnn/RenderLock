import pytest
from agr.cloth_proxy_settings import proxy_gravity_settings, proxy_clock_settings


def proxy(values):
    return {'vertices': [[0, 0, 0] for _ in values], 'paint': {'cloth_gravity$0': values}}


def test_nominal_conversion_is_explicitly_unvalidated():
    gravity,expected=1080,27.432
    result=proxy_gravity_settings(proxy([gravity]*3),.0254,(0,0,-9.81))
    assert result['target_acceleration_m_s2']==pytest.approx(expected)
    assert result['validated_for_export'] is False
    assert result['multiplier']*9.81==pytest.approx(expected)


@pytest.mark.parametrize('values', [[360,1080]])
def test_nonuniform_or_invalid_paint_is_not_averaged(values):
    with pytest.raises(ValueError):proxy_gravity_settings(proxy(values),.0254,(0,0,-9.81))


@pytest.mark.parametrize('gravity,enabled', [((1,0,-9.81),True)])
def test_unsupported_scene_forces_are_not_silently_reoriented(gravity,enabled):
    with pytest.raises(ValueError):proxy_gravity_settings(proxy([360]),.0254,gravity,enabled)


def test_clock_plan_has_correct_effective_acceleration():
    fps=128
    result=proxy_clock_settings(proxy([360]),.0254,(0,0,-9.81),fps)
    assert result['quality']==32
    assert result['time_scale']*fps==32
    actual=9.81*result['gravity_multiplier']*.001*(fps*result['time_scale'])**2
    assert actual==pytest.approx(9.144)


@pytest.mark.parametrize('fps', [64.00001])
def test_clock_rejects_unverified_timing(fps):
    with pytest.raises(ValueError):proxy_clock_settings(proxy([360]),.0254,(0,0,-9.81),fps)
