"""Blender NPR sampling-direction group; caller must qualify surface inputs."""
import math


def create_bounce_group(*,sharpness,influence_range):
    import bpy
    if not math.isfinite(sharpness) or not 0<=sharpness<1:
        raise ValueError('Invalid NPR sharpness')
    if len(influence_range)!=2 or not all(math.isfinite(v) for v in influence_range) or not 0<=influence_range[0]<=1 or influence_range[1]<=0:
        raise ValueError('Invalid distance-field influence range')
    group=bpy.data.node_groups.new('AGR research NPR bounce direction','ShaderNodeTree')
    group['scope']='Unit Source directions, nonvertical view, bounded AO/screen inputs; exposure/color separate'
    for name in ('Normal','Primary','View'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Material AO','Distance Field AO','Screen Factor'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    group.interface.new_socket(name='Direction',in_out='OUTPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Blend',in_out='OUTPUT',socket_type='NodeSocketFloat')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def m(op,*values):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]
    def v(op,a,b=None):
        node=nodes.new('ShaderNodeVectorMath');node.operation=op;put(node.inputs[0],a)
        if b is not None:put(node.inputs[3] if op=='SCALE' else node.inputs[1],b)
        return node.outputs['Value' if op=='DOT_PRODUCT' else 'Vector']
    def sat(x):return m('MINIMUM',1,m('MAXIMUM',0,x))
    light=m('ADD',.5,m('MULTIPLY',.5,v('DOT_PRODUCT',inp.outputs['Primary'],inp.outputs['Normal'])))
    soften=m('MULTIPLY',m('GREATER_THAN',light,.5),m('SUBTRACT',1,inp.outputs['Screen Factor']))
    light=m('SUBTRACT',light,m('MULTIPLY',m('SUBTRACT',light,.5),soften))
    dfao=m('ADD',influence_range[0],m('MULTIPLY',1-influence_range[0],sat(m('DIVIDE',inp.outputs['Distance Field AO'],influence_range[1]))))
    level=sat(m('SUBTRACT',m('MULTIPLY',2,m('MULTIPLY',light,m('MINIMUM',inp.outputs['Material AO'],dfao))),.5))
    fraction=m('FRACT',level);edge=m('MINIMUM',fraction,m('SUBTRACT',1,fraction))
    curve=m('MULTIPLY',.5,m('POWER',m('MULTIPLY',2,edge),1/(1-sharpness)))
    gain=m('ADD',curve,m('MULTIPLY',m('GREATER_THAN',fraction,.5),m('SUBTRACT',1,m('MULTIPLY',2,curve))))
    shaped=m('ADD',m('SUBTRACT',level,fraction),gain)
    z=m('SUBTRACT',shaped,.5)
    horizontal=v('NORMALIZE',v('MULTIPLY',inp.outputs['View'],(1,1,0)))
    direction=v('NORMALIZE',v('ADD',v('SCALE',horizontal,m('SUBTRACT',1,m('ABSOLUTE',z))),v('SCALE',(0,0,1),z)))
    links.new(direction,out.inputs['Direction'])
    links.new(m('MULTIPLY',.5,m('ADD',shaped,.5)),out.inputs['Blend'])
    return group
