import numpy as np
import pytest

from agr.blender_map_basis import transport_matrices


def test_positions_normals_and_directions_return_to_authored_lighting_frame():
    angle = .73
    rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                         [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    basis = np.eye(4)
    basis[:3, :3] = rotation * .42
    basis[:3, 3] = [17, -9, 3]
    transport = transport_matrices(basis)
    rng = np.random.default_rng(401)
    for _ in range(32):
        point = rng.normal(size=3)
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        world = basis @ np.r_[point, 1]
        np.testing.assert_allclose(transport['position'] @ world, np.r_[point, 1], atol=1e-12)
        normal = transport['normal'] @ (rotation @ direction)
        ray = transport['direction'] @ (rotation @ direction)
        np.testing.assert_allclose(normal / np.linalg.norm(normal), direction, atol=1e-12)
        np.testing.assert_allclose(ray / np.linalg.norm(ray), direction, atol=1e-12)
    assert transport['distance_scale'] == pytest.approx(1 / .42)


@pytest.mark.parametrize('matrix', [np.zeros((4, 4)), np.diag([-1, 1, 1, 1]),
                                   np.diag([1, 2, 1, 1]), np.eye(3), np.full((4, 4), np.nan)])
def test_unqualified_transforms_are_rejected(matrix):
    with pytest.raises(ValueError, match='External map:'):
        transport_matrices(matrix)
