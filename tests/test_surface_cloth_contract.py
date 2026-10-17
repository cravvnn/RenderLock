"""Backface sharing must preserve geometry, weights and coordinate frames."""
import numpy as np
import pytest

bpy = pytest.importorskip('bpy')
from agr.blender_surface_cloth import _matching_surface, _follow_surface


@pytest.fixture
def surfaces():
    objects, meshes, trees = [], [], set(bpy.data.node_groups)
    def make(name, points, weights):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(points, [], [(0, 1, 2)])
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        group = obj.vertex_groups.new(name='waist_cloth_1')
        for i, weight in enumerate(weights):
            group.add([i], weight, 'REPLACE')
        objects.append(obj)
        meshes.append(mesh)
        return obj
    carrier = make('cloth test carrier', [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [.2, .8, 1.])
    follower = make('cloth test backface', [(0, 1, 0), (0, 0, 0), (1, 0, 0)], [1., .2, .8])
    yield carrier, follower
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in meshes:
        bpy.data.meshes.remove(mesh)
    for tree in list(bpy.data.node_groups):
        if tree not in trees and tree.users == 0:
            bpy.data.node_groups.remove(tree)


def test_shared_surface_mapping_follows_deformed_carrier_without_changing_uv_topology(surfaces):
    carrier, follower = surfaces
    uv = follower.data.uv_layers.new(name='test UV')
    for loop, value in zip(uv.data, [(0.2, .8), (.1, .3), (.9, .1)]):
        loop.uv = value
    before = [tuple(loop.uv) for loop in uv.data]
    indices = _matching_surface(carrier, follower)
    assert indices.tolist() == [2, 0, 1]
    _follow_surface(carrier, follower, indices)
    carrier.data.vertices[1].co.z = .125
    carrier.data.update()
    bpy.context.view_layer.update()
    evaluated = follower.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        positions = np.array([v.co[:] for v in mesh.vertices])
        expected = np.array([carrier.data.vertices[i].co[:] for i in indices])
        assert np.array_equal(positions, expected)
        assert len(mesh.polygons) == 1
        assert [tuple(loop.uv) for loop in mesh.uv_layers['test UV'].data] == before
    finally:
        evaluated.to_mesh_clear()


def test_same_positions_with_different_skin_binding_do_not_share_simulation(surfaces):
    carrier, follower = surfaces
    follower.vertex_groups['waist_cloth_1'].add([0], .25, 'REPLACE')
    assert _matching_surface(carrier, follower) is None


def test_nonbijective_surface_is_not_matched(surfaces):
    carrier, follower = surfaces
    follower.data.vertices[0].co = follower.data.vertices[1].co
    assert _matching_surface(carrier, follower) is None


def test_different_object_coordinates_are_not_shared(surfaces):
    carrier, follower = surfaces
    follower.location.x = 1.
    bpy.context.view_layer.update()
    assert _matching_surface(carrier, follower) is None
