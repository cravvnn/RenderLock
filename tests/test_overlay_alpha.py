import pytest
from agr.blender_overlay_material import alpha_ranges


def test_overlay_defaults_and_reversed_ranges():
    assert alpha_ranges({}) == [(0, 1), (0, 1)]
    assert alpha_ranges({'g_vColorTranslucencyInputRange': [0, .5]}) == [(0, .5), (0, 1)]
    assert alpha_ranges({'g_vColorTranslucencyInputRange': [.8, .2],
                         'g_vColorTranslucencyOutputRange': [.9, .1]}) == [(.8, .2), (.9, .1)]


@pytest.mark.parametrize('value', [None, [], [.1], [.2, .2], [float('nan'), 1], [0, float('inf')], [-1, 1]])
def test_invalid_input_ranges_fail_before_graph_edit(value):
    with pytest.raises(ValueError):
        alpha_ranges({'g_vColorTranslucencyInputRange': value})


@pytest.mark.parametrize('value', [[0, 2], [float('nan'), 1], None])
def test_invalid_output_ranges_fail_before_graph_edit(value):
    with pytest.raises(ValueError):
        alpha_ranges({'g_vColorTranslucencyOutputRange': value})
