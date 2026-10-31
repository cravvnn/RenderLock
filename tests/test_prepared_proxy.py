import numpy as np
import pytest

from agr.blender_prepared_proxy import simplify


def test_proxy_removes_duplicates_and_collinear_triangles_without_changing_inputs():
    points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [.5, 0, 0], [.01, 0, 0]])
    triangles = np.array([[0, 1, 2], [2, 1, 0], [0, 2, 3], [0, 4, 1], [0, 5, 1]], np.int32)
    original_points = points.copy(); original_faces = triangles.copy()
    vertices, faces = simplify(points, triangles)
    assert faces.shape == (2, 3) and vertices.shape == (4, 3)
    np.testing.assert_array_equal(points, original_points)
    np.testing.assert_array_equal(triangles, original_faces)
    np.testing.assert_allclose(vertices.min(0), [0, 0, 0])
    np.testing.assert_allclose(vertices.max(0), [1, 1, 0])


@pytest.mark.parametrize('points,faces', [([[np.nan, 0, 0]], [[0, 0, 0]]),
                                        ([[0, 0, 0]], [[0, 1, 2]]),
                                        ([[0, 0, 0]], [[-1, 0, 0]])])
def test_invalid_proxy_geometry_fails(points, faces):
    with pytest.raises(ValueError, match='Viewport proxy:'):
        simplify(points, np.asarray(faces, np.int32))
