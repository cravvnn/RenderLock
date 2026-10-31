"""Transport prepared-map lighting lookups into the replay coordinate frame.

Geometry and Blender lights move with the map. Numeric lighting atlases stay in
their authored frame, so shader positions, directions and normals need the
inverse transport. This adapter does not implement Source lighting shaders.
"""
from __future__ import annotations

import json

ALGORITHM = 'map-lighting-basis-v1'
_GROUP_NODES = {
    'Deadlock baked light': 78,
    'Deadlock fog': 36,
    'Deadlock probe light': 648,
    'Deadlock reflections': 1013,
}


def transport_matrices(matrix):
    import numpy as np
    value = np.asarray(matrix, dtype=np.float64)
    if (value.shape != (4, 4) or not np.isfinite(value).all()
            or not np.allclose(value[3], [0, 0, 0, 1], rtol=0, atol=1e-10)):
        raise ValueError('External map: invalid lighting coordinate transform.')
    linear = value[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0]))
    if (not 0 < scale <= 100 or np.linalg.det(linear) <= 0
            or not np.allclose(linear.T @ linear, np.eye(3) * scale**2, rtol=2e-6, atol=1e-7)):
        raise ValueError('External map: lighting requires a positive uniform map scale.')
    inverse = np.linalg.inv(value)
    return dict(position=inverse, direction=inverse[:3, :3], normal=linear.T,
                distance_scale=1 / scale)


def _vector(tree, socket, matrix, offset, *, label, normalize):
    targets = [link.to_socket for link in socket.links]
    if not targets:
        raise ValueError(f'External map: missing shader connection: {tree.name}/{label}.')
    for link in list(socket.links):
        tree.links.remove(link)
    xyz = tree.nodes.new('ShaderNodeCombineXYZ')
    xyz.name = 'RenderLock | ' + label
    for axis in range(3):
        dot = tree.nodes.new('ShaderNodeVectorMath')
        dot.operation = 'DOT_PRODUCT'
        dot.inputs[1].default_value = tuple(float(x) for x in matrix[axis])
        tree.links.new(socket, dot.inputs[0])
        addition = tree.nodes.new('ShaderNodeMath')
        addition.operation = 'ADD'
        addition.inputs[1].default_value = float(offset[axis])
        tree.links.new(dot.outputs['Value'], addition.inputs[0])
        tree.links.new(addition.outputs[0], xyz.inputs[axis])
    output = xyz.outputs[0]
    if normalize:
        norm = tree.nodes.new('ShaderNodeVectorMath')
        norm.operation = 'NORMALIZE'
        tree.links.new(output, norm.inputs[0])
        output = norm.outputs['Vector']
    for target in targets:
        tree.links.new(output, target)


def prepare_lighting_basis(world, matrix, *, namespace=''):
    """Apply once to a newly opened qualified builder14 map, before composition.

    The exact node inventory is deliberately qualified. Changed builder graphs
    require review rather than silently receiving a guessed coordinate repair.
    """
    import bpy
    values = transport_matrices(matrix)
    groups = {}
    for name, count in _GROUP_NODES.items():
        tree = bpy.data.node_groups.get(namespace + name)
        if (tree is None or tree.library or len(tree.nodes) != count
                or tree.get('renderlock_lighting_basis') is not None):
            raise ValueError(f'External map: unqualified or already aligned lighting group: {name}.')
        groups[name] = tree
    if world is None or world.library or not world.use_nodes:
        raise ValueError('External map: missing local prepared-map sky.')
    plans = []
    for name in ('Deadlock baked light', 'Deadlock probe light'):
        tree = groups[name]
        inputs = [n for n in tree.nodes if n.type == 'GROUP_INPUT' and 'Normal' in n.outputs]
        if len(inputs) != 1:
            raise ValueError(f'External map: ambiguous lighting normal input: {name}.')
        plans.append((tree, inputs[0].outputs['Normal'], 'normal', name + ' normal'))
    for name in ('Deadlock reflections', 'Deadlock fog'):
        tree = groups[name]
        geometry = [n for n in tree.nodes if n.type == 'NEW_GEOMETRY']
        wanted = ('Incoming', 'Position') if name == 'Deadlock fog' else ('Incoming',)
        for output in wanted:
            sockets = [n.outputs[output] for n in geometry if n.outputs[output].is_linked]
            if len(sockets) != 1:
                raise ValueError(f'External map: ambiguous {name} {output} lookup.')
            plans.append((tree, sockets[0], 'position' if output == 'Position' else 'direction',
                          name + ' ' + output))
    sky = [n.outputs['Generated'] for n in world.node_tree.nodes
           if n.type == 'TEX_COORD' and n.outputs['Generated'].is_linked]
    fog_distance = [n.outputs['View Distance'] for n in groups['Deadlock fog'].nodes
                    if n.type == 'CAMERA' and n.outputs['View Distance'].is_linked]
    if len(sky) != 1 or len(fog_distance) != 1 or any(not s.is_linked for _, s, _, _ in plans):
        raise ValueError('External map: unqualified sky or fog coordinate inputs.')
    plans.append((world.node_tree, sky[0], 'direction', 'sky direction'))
    # Validate the complete plan before mutating any node tree.
    for tree, socket, kind, label in plans:
        value = values[kind]
        offset = value[:3, 3] if kind == 'position' else (0, 0, 0)
        _vector(tree, socket, value[:3, :3], offset, label=label, normalize=kind != 'position')
    tree = groups['Deadlock fog']
    distance = fog_distance[0]
    targets = [link.to_socket for link in distance.links]
    for link in list(distance.links):
        tree.links.remove(link)
    node = tree.nodes.new('ShaderNodeMath')
    node.operation = 'MULTIPLY'
    node.name = 'RenderLock | authored fog distance'
    node.inputs[1].default_value = values['distance_scale']
    tree.links.new(distance, node.inputs[0])
    for target in targets:
        tree.links.new(node.outputs[0], target)
    result = dict(algorithm=ALGORITHM, matrix=[[float(x) for x in row] for row in matrix],
                  vector_transports=len(plans), fog_distance_scale=values['distance_scale'])
    encoded = json.dumps(result, sort_keys=True)
    for tree in (*groups.values(), world.node_tree):
        tree['renderlock_lighting_basis'] = encoded
    return result
