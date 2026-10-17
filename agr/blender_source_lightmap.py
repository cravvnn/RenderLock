"""Blender node evaluation of the qualified Source static diffuse SH branch.

No bpy import until construction; caller owns source identity, image decoding,
UV mapping, normal coordinate conversion and material/direct-light integration.
"""


def create_diffuse_group():
    import bpy
    from .source_lightmap import SHADER_SHA256

    group = bpy.data.node_groups.new('AGR Source 6774 diffuse SH', 'ShaderNodeTree')
    group['agr_source_shader_sha256'] = SHADER_SHA256
    group['agr_scope'] = 'Static indirect diffuse term only; no direct light/specular/material response'
    for name in ('DC', 'R', 'G', 'B', 'Normal'):
        group.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketVector')
    group.interface.new_socket(name='Diffuse', in_out='OUTPUT', socket_type='NodeSocketVector')
    nodes, links = group.nodes, group.links
    inputs = nodes.new('NodeGroupInput')
    output = nodes.new('NodeGroupOutput')

    def put(socket, value):
        if hasattr(value, 'node'):
            links.new(value, socket)
        else:
            socket.default_value = value

    def math(op, *values):
        node = nodes.new('ShaderNodeMath')
        node.operation = op
        for socket, value in zip(node.inputs, values):
            put(socket, value)
        return node.outputs[0]

    def vector(op, a, b=None):
        node = nodes.new('ShaderNodeVectorMath')
        node.operation = op
        put(node.inputs[0], a)
        if b is not None:
            put(node.inputs[3] if op == 'SCALE' else node.inputs[1], b)
        return node.outputs['Value' if op in ('DOT_PRODUCT', 'LENGTH') else 'Vector']

    def split(value):
        node = nodes.new('ShaderNodeSeparateXYZ')
        put(node.inputs[0], value)
        return list(node.outputs)

    def combine(values):
        node = nodes.new('ShaderNodeCombineXYZ')
        for socket, value in zip(node.inputs, values):
            put(socket, value)
        return node.outputs[0]

    dc = split(inputs.outputs['DC'])
    world_vectors = []
    for channel, component in zip(('R','G','B'), dc):
        encoded = inputs.outputs[channel]
        decoded = vector('SUBTRACT', vector('SCALE', encoded, 2.0), (1,1,1))
        coefficients = split(vector('SCALE', vector('SCALE', decoded, component), 1.7320507764816284))
        world_vectors.append(combine([math('MULTIPLY', coefficients[2], -1),
                                      math('MULTIPLY', coefficients[0], -1), coefficients[1]]))
    weighted = [vector('SCALE', v, weight) for v, weight in zip(world_vectors,
                (.2125999927520752,.7152000069618225,.0722000002861023))]
    direction_raw = vector('ADD', vector('ADD', weighted[0], weighted[1]), weighted[2])
    denominator = math('MAXIMUM', vector('LENGTH', direction_raw), 1e-5)
    direction = vector('SCALE', direction_raw, math('DIVIDE', 1, denominator))
    normal = inputs.outputs['Normal']
    nd = vector('DOT_PRODUCT', direction, normal)
    quadratic = math('MULTIPLY', .31539157032966614,
                     math('SUBTRACT', math('MULTIPLY', math('MULTIPLY', 3, nd), nd), 1))
    colors = []
    for value, v in zip(dc, world_vectors):
        ratio = math('ABSOLUTE', math('DIVIDE', vector('DOT_PRODUCT', v, direction), math('MAXIMUM', 1e-5, value)))
        linear = math('MULTIPLY', math('ADD', math('MULTIPLY', value, .886227548122406),
                      math('MULTIPLY', vector('DOT_PRODUCT', v, normal), 1.0233277082443237)), .31830987334251404)
        correction = math('MULTIPLY', math('MULTIPLY', math('MULTIPLY',
            math('MULTIPLY', value, ratio), math('ADD', .07999999821186066,
            math('MULTIPLY', ratio, .6000000238418579))), .25), quadratic)
        colors.append(math('MAXIMUM', 0, math('ADD', linear, correction)))
    put(output.inputs['Diffuse'], combine(colors))
    return group
