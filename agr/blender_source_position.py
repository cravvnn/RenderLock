"""Recover Source world coordinates from an AGR Blender shader position."""
import math


def source_position(nodes, links, position, coordinates):
    scale = float(coordinates['unit_scale'])
    origin = tuple(float(x) for x in coordinates['origin'])
    yaw = float(coordinates['world_yaw_degrees'])
    if scale <= 0 or len(origin) != 3 or not all(math.isfinite(x) for x in (scale, yaw, *origin)):
        raise ValueError('Invalid Source coordinate contract')
    if yaw:
        rotate = nodes.new('ShaderNodeVectorRotate'); rotate.rotation_type = 'Z_AXIS'
        rotate.inputs['Angle'].default_value = -math.radians(yaw)
        links.new(position, rotate.inputs['Vector']); position = rotate.outputs['Vector']
    size = nodes.new('ShaderNodeVectorMath'); size.operation = 'SCALE'
    size.inputs[3].default_value = 1 / scale; links.new(position, size.inputs[0])
    offset = nodes.new('ShaderNodeVectorMath'); offset.operation = 'ADD'
    offset.inputs[1].default_value = origin; links.new(size.outputs[0], offset.inputs[0])
    return offset.outputs[0]
