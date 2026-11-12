import numpy as np
import pytest
from agr.particle_snapshot_matrices import snapshot_skinning_matrices


def transforms(*xs):
    return [[x, 0, 0, 1, 0, 0, 0, 1] for x in xs]


def test_single_valid_ignores_weight_and_composes_inverse_rest():
    got, old = snapshot_skinning_matrices(transforms(-5), transforms(12),
        [0, 65535, 65535, 65535], [0, 0, 0], previous=transforms(10))
    np.testing.assert_array_equal(got, [[1, 0, 0, 7], [0, 1, 0, 0], [0, 0, 1, 0]])
    assert old[0, 3] == 5


def test_previous_bounds_change_both_outputs_without_renormalizing():
    got, old = snapshot_skinning_matrices(transforms(0, 0, 0), transforms(4, 8, 12),
        [0, 1, 2, 65535], [.2, .3, .5], previous=transforms(2, 6))
    np.testing.assert_allclose(got[0], [.5, 0, 0, 3.2], atol=1e-6)
    np.testing.assert_allclose(old[0], [.5, 0, 0, 2.2], atol=1e-6)


@pytest.mark.parametrize('weights, expected', [([.8, .7, .6], 0), ([-.5, 0, 0], 1)])
def test_fourth_weight_clamps(weights, expected):
    got, _ = snapshot_skinning_matrices(transforms(0, 0), transforms(0, 10),
        [0, 65535, 65535, 1], weights)
    assert got[0, 3] == expected*10


def test_invalid_slots_and_duplicate_indices_keep_slot_weights():
    got, _ = snapshot_skinning_matrices(transforms(0), transforms(10),
        [65535, 0, 65535, 0], [.1, .2, .3])
    assert got[0, 3] == pytest.approx(6, abs=1e-6)
    assert snapshot_skinning_matrices(transforms(0), transforms(0),
        [1, 2, 65535, 9], [1, 0, 0]) == (None, None)


@pytest.mark.parametrize('bad', [[True, 0, 0, 0], [-1, 0, 0, 0], [65536, 0, 0, 0], [0]])
def test_bad_indices_reject(bad):
    with pytest.raises(ValueError):
        snapshot_skinning_matrices(transforms(0), transforms(0), bad, [1, 0, 0])


@pytest.mark.parametrize('row', [[0]*8, [0, 0, 0, 1, 0, 0, 0, 2],
                                [0, 0, 0, 1, 0, 0, 0, float('nan')]])
def test_invalid_pose_rejects(row):
    with pytest.raises(ValueError):
        snapshot_skinning_matrices([row], transforms(0), [0]*4, [1, 0, 0])
