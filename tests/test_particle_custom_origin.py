import math
import numpy as np
import pytest
from agr.particle_custom_origin import custom_origin_transform


def test_owner_rotation_and_scale_apply_to_local_offset_and_basis():
    q = math.sqrt(.5)
    owner = [100, 200, 300, 2, 0, 0, q, q]
    # Local X translation follows the owner's rotated/scaled X; authoring a
    # local quarter-turn also rotates the output frame, not its translation.
    authored = [[0, -1, 0, 3], [1, 0, 0, 0], [0, 0, 1, 0]]
    expected = [[-2, 0, 0, 100], [0, -2, 0, 206], [0, 0, 2, 300]]
    np.testing.assert_allclose(custom_origin_transform(owner, authored), expected, atol=1e-5)


def test_authored_affine_matrix_is_not_silently_orthogonalized():
    authored = np.array([[1, .5, 0, 7], [0, 3, 0, -8], [0, 0, 0, 9]], dtype=np.float32)
    np.testing.assert_array_equal(custom_origin_transform([0, 0, 0, 1, 0, 0, 0, 1], authored), authored)


@pytest.mark.parametrize('scale', [0, -1])
def test_unusable_owner_scale_is_refused(scale):
    with pytest.raises(ValueError):
        custom_origin_transform([0, 0, 0, scale, 0, 0, 0, 1], np.eye(4)[:3])


@pytest.mark.parametrize('owner,authored', [
    ([0]*7, np.eye(4)[:3]), ([0]*9, np.eye(4)[:3]),
    ([0, 0, 0, 1, 0, 0, 0, 1], np.eye(4)),
    ([0, 0, 0, 1, 0, 0, 0, 1], list(range(12))),
    ([False]*8, np.eye(4)[:3]),
    (['0']*8, np.eye(4)[:3]),
    ([0, 0, 0, 1, 0, 0, 0, 1], [[True]*4]*3),
    ([0, 0, 0, 1, 0, 0, 0, 0], np.eye(4)[:3]),
    ([0, 0, 0, 1, 0, 0, 0, 2], np.eye(4)[:3]),
])
def test_missing_or_malformed_transform_is_not_filled_in(owner, authored):
    with pytest.raises(ValueError):
        custom_origin_transform(owner, authored)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), 1e100])
def test_nonfinite_or_out_of_range_source_inputs_are_refused(bad):
    owner = [0, 0, 0, 1, 0, 0, 0, 1]
    authored = np.eye(4)[:3]
    broken = owner.copy(); broken[0] = bad
    with pytest.raises(ValueError):custom_origin_transform(broken, authored)
    authored[2, 3] = bad
    with pytest.raises(ValueError):custom_origin_transform(owner, authored)


def test_finite_inputs_that_overflow_source_output_are_refused():
    with pytest.raises(ValueError, match='overflow'):
        custom_origin_transform([0, 0, 0, 2, 0, 0, 0, 1],
                                [[1, 0, 0, 3e38], [0, 1, 0, 0], [0, 0, 1, 0]])
