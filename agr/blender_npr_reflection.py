"""NPR reflection normalization; caller must qualify a positive SH denominator."""
import math


def create_normalization_group(*,ceiling_parameters,exposure_enabled):
    import bpy
    if len(ceiling_parameters)!=2 or not all(math.isfinite(x) for x in ceiling_parameters) or type(exposure_enabled) is not bool:
        raise ValueError('Explicit finite normalization parameters/gate required')
    group=bpy.data.node_groups.new('AGR NPR reflection normalization','ShaderNodeTree')
    for name in ('Radiance','Irradiance'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Roughness','Denominator'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    group.interface.new_socket(name='Reflection',in_out='OUTPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Factor',in_out='OUTPUT',socket_type='NodeSocketFloat')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def m(op,*values):
        n=nodes.new('ShaderNodeMath');n.operation=op
        for socket,value in zip(n.inputs,values):put(socket,value)
        return n.outputs[0]
    dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT'
    links.new(inp.outputs['Irradiance'],dot.inputs[0]);dot.inputs[1].default_value=(.21250000596046448,.715399980545044,.07209999859333038)
    ceiling=m('MAXIMUM',m('ADD',m('MULTIPLY',inp.outputs['Roughness'],ceiling_parameters[0]),ceiling_parameters[1]),1)
    if exposure_enabled:ceiling=m('MULTIPLY',ceiling,32)
    factor=m('MINIMUM',m('DIVIDE',dot.outputs['Value'],inp.outputs['Denominator']),ceiling)
    scale=nodes.new('ShaderNodeVectorMath');scale.operation='SCALE';links.new(inp.outputs['Radiance'],scale.inputs[0]);links.new(factor,scale.inputs[3])
    links.new(scale.outputs[0],out.inputs['Reflection']);links.new(factor,out.inputs['Factor'])
    return group
