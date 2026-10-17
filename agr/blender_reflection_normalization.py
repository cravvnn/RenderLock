"""Qualified positive-denominator normalization with explicit ceiling constants."""


def create_normalization_group(ceiling_parameters):
    import bpy
    import numpy as np
    params = np.asarray(ceiling_parameters,dtype=float)
    if params.shape != (2,) or not np.isfinite(params).all():
        raise ValueError('Explicit finite ceiling parameters required')
    group = bpy.data.node_groups.new('AGR reflection normalization','ShaderNodeTree')
    for name,kind in [('Normal','Vector'),('Irradiance','Vector'),('SH Vector','Vector'),('SH Constant','Float'),('Roughness','Float')]:
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocket'+kind)
    for name in ('Factor','Denominator','Ceiling'):
        group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketFloat')
    group['agr_ceiling_parameters'] = params.tolist()
    group['agr_scope'] = 'Requires positive denominator; ceiling parameters are explicit research inputs, not captured engine constants'
    nodes,links = group.nodes,group.links
    inp,out = nodes.new('NodeGroupInput'),nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'): links.new(value,socket)
        else: socket.default_value=value
    def math(op,*values):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]
    def dot(a,b):
        node=nodes.new('ShaderNodeVectorMath');node.operation='DOT_PRODUCT'
        put(node.inputs[0],a);put(node.inputs[1],b);return node.outputs['Value']
    denominator=math('ADD',dot(inp.outputs['Normal'],inp.outputs['SH Vector']),inp.outputs['SH Constant'])
    ceiling=math('MAXIMUM',1,math('ADD',math('MULTIPLY',math('MAXIMUM',.05,inp.outputs['Roughness']),params[0]),params[1]))
    ratio=math('DIVIDE',dot(inp.outputs['Irradiance'],(.21250000596046448,.7153999805450439,.07209999859333038)),denominator)
    for name,value in [('Factor',math('MINIMUM',ratio,ceiling)),('Denominator',denominator),('Ceiling',ceiling)]:links.new(value,out.inputs[name])
    return group
