"""Node equivalent of the qualified diffuse albedo/metallic factor."""


def create_diffuse_material_group():
    import bpy
    group=bpy.data.node_groups.new('AGR Source diffuse material','ShaderNodeTree')
    for name in ('Lighting','Albedo'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketColor')
    metal=group.interface.new_socket(name='Metallic',in_out='INPUT',socket_type='NodeSocketFloat')
    metal.min_value=0;metal.max_value=1;metal.default_value=0
    group.interface.new_socket(name='Diffuse',in_out='OUTPUT',socket_type='NodeSocketColor')
    nodes,links=group.nodes,group.links
    source=nodes.new('NodeGroupInput');output=nodes.new('NodeGroupOutput')
    complement=nodes.new('ShaderNodeMath');complement.operation='SUBTRACT';complement.inputs[0].default_value=1
    links.new(source.outputs['Metallic'],complement.inputs[1])
    color=nodes.new('ShaderNodeVectorMath');color.operation='MULTIPLY'
    links.new(source.outputs['Lighting'],color.inputs[0]);links.new(source.outputs['Albedo'],color.inputs[1])
    scale=nodes.new('ShaderNodeVectorMath');scale.operation='SCALE'
    links.new(color.outputs['Vector'],scale.inputs[0]);links.new(complement.outputs[0],scale.inputs['Scale'])
    links.new(scale.outputs['Vector'],output.inputs['Diffuse'])
    return group
