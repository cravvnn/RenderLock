"""Explicit-input NPR rim nodes; screen depth reconstruction is not inferred."""
import math


def create_rim_group(*,wrap,falloff,strength,up_ramp,equal_ramp_policy,
                     depth_enabled,tolerance_min,tolerance_range):
    import bpy
    if not all(math.isfinite(x) for x in (wrap,falloff,strength,tolerance_min,tolerance_range)) or wrap<0 or falloff<=0 or strength<0 or tolerance_min<0 or tolerance_range<=0:
        raise ValueError('Invalid rim/depth controls')
    if len(up_ramp)!=2 or not all(math.isfinite(x) for x in up_ramp) or up_ramp[1]<up_ramp[0] or type(depth_enabled) is not bool:
        raise ValueError('Explicit ordered ramp/depth gate required')
    if equal_ramp_policy not in ('reject','strict_step_gpu_reference') or (up_ramp[0]==up_ramp[1] and equal_ramp_policy=='reject'):
        raise ValueError('Equal-ramp backend reference must be explicitly selected')
    group=bpy.data.node_groups.new('AGR NPR rim','ShaderNodeTree')
    group['scope']='Unit directions, bounded material masks, positive converted m6/depth samples; explicit equal-ramp policy'
    for name in ('Normal','View','Direct','Indirect'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Material AO','Mask','Current Depth','Sampled Depth'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    group.interface.new_socket(name='Rim',in_out='OUTPUT',socket_type='NodeSocketVector')
    for name in ('Factor','Depth Factor'):group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketFloat')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def m(op,*values):
        n=nodes.new('ShaderNodeMath');n.operation=op
        for socket,value in zip(n.inputs,values):put(socket,value)
        return n.outputs[0]
    def sat(x):return m('MINIMUM',1,m('MAXIMUM',0,x))
    dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT'
    links.new(inp.outputs['Normal'],dot.inputs[0]);links.new(inp.outputs['View'],dot.inputs[1])
    separate=nodes.new('ShaderNodeSeparateXYZ');links.new(inp.outputs['Normal'],separate.inputs[0]);z=separate.outputs['Z']
    up=m('GREATER_THAN',z,up_ramp[0]) if up_ramp[0]==up_ramp[1] else sat(m('DIVIDE',m('SUBTRACT',z,up_ramp[0]),up_ramp[1]-up_ramp[0]))
    facing=sat(m('DIVIDE',m('SUBTRACT',wrap,dot.outputs['Value']),(1+wrap)**2))
    factor=m('POWER',facing,falloff)
    t=sat(m('DIVIDE',m('SUBTRACT',inp.outputs['Sampled Depth'],m('ADD',tolerance_min,inp.outputs['Current Depth'])),tolerance_range))
    depth=m('MULTIPLY',m('MULTIPLY',t,t),m('SUBTRACT',3,m('MULTIPLY',2,t)))
    for value in (up,strength,inp.outputs['Material AO'],inp.outputs['Mask']):factor=m('MULTIPLY',factor,value)
    if depth_enabled:factor=m('MULTIPLY',factor,depth)
    add=nodes.new('ShaderNodeVectorMath');add.operation='ADD';links.new(inp.outputs['Direct'],add.inputs[0]);links.new(inp.outputs['Indirect'],add.inputs[1])
    scale=nodes.new('ShaderNodeVectorMath');scale.operation='SCALE';links.new(add.outputs[0],scale.inputs[0]);links.new(factor,scale.inputs[3])
    links.new(scale.outputs[0],out.inputs['Rim']);links.new(factor,out.inputs['Factor']);links.new(depth,out.inputs['Depth Factor'])
    return group
