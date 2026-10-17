import numpy as np
import pytest
from agr.particle_bone_shapes import bone_boxes


def test_bone_boxes_preserve_order_and_transform_components():
    source = np.arange(24, dtype=np.float32).reshape(3, 8)
    before = source.copy()
    result = bone_boxes(source, offset=(10, -20, 30), capacity=2)
    np.testing.assert_array_equal(result['source_bone_indices'], [0, 1])
    np.testing.assert_array_equal(result['mins'], np.full((2, 3), -8))
    np.testing.assert_array_equal(result['maxs'], np.full((2, 3), 8))
    np.testing.assert_array_equal(result['transforms'][:, :3], source[:2, :3] + [10, -20, 30])
    np.testing.assert_array_equal(result['transforms'][:, 3:], source[:2, 3:])
    np.testing.assert_array_equal(source, before)
    assert not np.shares_memory(result['transforms'], source)


def test_empty_bones_remain_empty_without_inventing_origin_bone():
    result = bone_boxes(np.empty((0, 8)))
    assert result['transforms'].shape == (0, 8)
    assert result['mins'].shape == (0, 3)


@pytest.mark.parametrize('bad', [np.zeros((2, 7)), np.zeros(8), np.full((1, 8), np.nan)])
def test_invalid_bone_transforms_fail(bad):
    with pytest.raises(ValueError, match='bone transforms'):
        bone_boxes(bad)


@pytest.mark.parametrize('capacity', [0, -1, True, 1.5])
def test_invalid_capacity_fails(capacity):
    with pytest.raises(ValueError, match='Capacity'):
        bone_boxes(np.zeros((2, 8)), capacity=capacity)


def test_invalid_offset_fails():
    with pytest.raises(ValueError, match='offset'):
        bone_boxes(np.zeros((1, 8)), offset=(0, float('inf'), 0))
