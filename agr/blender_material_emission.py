"""Retain converted material emission when replacing its lighting response."""


def add_converted_emission(nodes, links, bsdf, radiance):
    color = bsdf.inputs['Emission Color']
    strength = bsdf.inputs['Emission Strength']
    if not strength.is_linked and strength.default_value == 0:
        return radiance
    if not color.is_linked and all(v == 0 for v in color.default_value[:3]):
        return radiance
    emission = nodes.new('ShaderNodeVectorMath')
    emission.name = 'AGR converted material emission'
    emission.operation = 'SCALE'
    if color.is_linked:
        links.new(color.links[0].from_socket, emission.inputs[0])
    else:
        emission.inputs[0].default_value = color.default_value[:3]
    if strength.is_linked:
        links.new(strength.links[0].from_socket, emission.inputs[3])
    else:
        emission.inputs[3].default_value = strength.default_value
    total = nodes.new('ShaderNodeVectorMath')
    total.name = 'AGR lighting plus converted emission'
    total.operation = 'ADD'
    links.new(radiance, total.inputs[0])
    links.new(emission.outputs['Vector'], total.inputs[1])
    return total.outputs['Vector']
