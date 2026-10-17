"""Research reflection coordinates; inputs are Source-space unit directions.

Callers must qualify positions inside the active expanded probe box. Zero ray
components use an infinite-distance sentinel for the box intersection.
"""
from agr.source_probe_assets import vector, rotation


def create_coordinates_group(properties,*,roughness_floor=.05,mip_scale=6):
    import bpy
    import math as scalar_math
    if not scalar_math.isfinite(roughness_floor) or not 0<=roughness_floor<=1 or not scalar_math.isfinite(mip_scale) or mip_scale<0:
        raise ValueError('Invalid reflection sampling parameters')
    p = properties
    if p.get('parentname') or p.get('uselocaloffset'):
        raise ValueError('Parented cubemap unqualified')
    low, high = vector(p['box_mins'])-.02, vector(p['box_maxs'])+.02
    if (high <= low).any():
        raise ValueError('Invalid cubemap bounds')
    axes, origin = rotation(p['angles']), vector(p['origin'])
    group = bpy.data.node_groups.new('AGR reflection coordinates', 'ShaderNodeTree')
    for name in ('Position', 'Normal', 'View'):
        group.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketVector')
    group.interface.new_socket(name='Roughness', in_out='INPUT', socket_type='NodeSocketFloat')
    for name in ('Direction', 'Dominant', 'Parallax'):
        group.interface.new_socket(name=name, in_out='OUTPUT', socket_type='NodeSocketVector')
    group.interface.new_socket(name='LOD', in_out='OUTPUT', socket_type='NodeSocketFloat')
    nodes, links = group.nodes, group.links
    inp, out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')

    def put(socket, value):
        if hasattr(value, 'node'): links.new(value, socket)
        else: socket.default_value = value

    def math(op, *values):
        node = nodes.new('ShaderNodeMath'); node.operation = op
        for socket, value in zip(node.inputs, values): put(socket, value)
        return node.outputs[0]

    def vec(op, *values):
        node = nodes.new('ShaderNodeVectorMath'); node.operation = op
        for i, value in enumerate(values): put(node.inputs[3 if op == 'SCALE' and i == 1 else i], value)
        return node.outputs['Value' if op == 'DOT_PRODUCT' else 'Vector']

    def local(value):
        node = nodes.new('ShaderNodeCombineXYZ')
        for socket, axis in zip(node.inputs, axes): put(socket, vec('DOT_PRODUCT', value, axis))
        return node.outputs[0]

    def split(value):
        node = nodes.new('ShaderNodeSeparateXYZ'); put(node.inputs[0], value)
        return node.outputs

    r = math('MAXIMUM', roughness_floor, inp.outputs['Roughness'])
    squared = math('MULTIPLY', r, r)
    smooth = math('SUBTRACT', 1, squared)
    dominant = math('MULTIPLY', smooth, math('ADD', math('SQRT', smooth), squared))
    n, view = inp.outputs['Normal'], inp.outputs['View']
    reflected = vec('SUBTRACT', vec('SCALE', n, math('MULTIPLY', 2, vec('DOT_PRODUCT', n, view))), view)
    ray = vec('NORMALIZE', vec('ADD', n, vec('SCALE', vec('SUBTRACT', reflected, n), dominant)))
    position, local_ray = local(vec('SUBTRACT', inp.outputs['Position'], origin)), local(ray)
    distances = []
    for pos, direction, lo, hi in zip(split(position), split(local_ray), low, high):
        far = math('MAXIMUM', math('DIVIDE', math('SUBTRACT', hi, pos), direction), math('DIVIDE', math('SUBTRACT', lo, pos), direction))
        zero = math('COMPARE', direction, 0, 0)
        distances.append(math('ADD', math('MULTIPLY', far, math('SUBTRACT', 1, zero)), math('MULTIPLY', zero, 1e30)))
    distance = math('ABSOLUTE', math('MINIMUM', distances[0], math('MINIMUM', distances[1], distances[2])))
    corrected = vec('NORMALIZE', vec('ADD', position, vec('SCALE', local_ray, distance)))
    sample = vec('NORMALIZE', vec('ADD', corrected, vec('SCALE', vec('SUBTRACT', local_ray, corrected), r)))
    for name, value in [('Direction', sample), ('Dominant', ray), ('Parallax', corrected), ('LOD', math('MULTIPLY', mip_scale, math('SQRT', r)))]:
        links.new(value, out.inputs[name])
    return group
