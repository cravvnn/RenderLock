"""Frame-specific Source-space rim UV projection with explicit matrix constants."""
import numpy as np


def create_projection_group(*,world_to_clip,sample_distance,translation,uv_scale):
    import bpy
    matrix=np.asarray(world_to_clip,dtype=float);offset=np.asarray(translation,dtype=float);scale=np.asarray(uv_scale,dtype=float)
    if matrix.shape!=(4,4) or offset.shape!=(4,) or scale.shape!=(2,) or not all(np.isfinite(x).all() for x in (matrix,offset,scale)) or not np.isfinite(sample_distance) or sample_distance<0 or np.any(scale<=0):raise ValueError('Explicit finite projection contract required')
    group=bpy.data.node_groups.new('AGR frame rim projection','ShaderNodeTree')
    for name in ('Position','Normal'):group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Current UV','Sample UV'):group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Valid',in_out='OUTPUT',socket_type='NodeSocketFloat')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def m(op,*values):
        n=nodes.new('ShaderNodeMath');n.operation=op
        for socket,value in zip(n.inputs,values):put(socket,value)
        return n.outputs[0]
    def v(op,a,b=None):
        n=nodes.new('ShaderNodeVectorMath');n.operation=op;put(n.inputs[0],a)
        if b is not None:put(n.inputs[3] if op=='SCALE' else n.inputs[1],b)
        return n.outputs['Value' if op in ('DOT_PRODUCT','LENGTH') else 'Vector']
    direction=v('ADD',inp.outputs['Normal'],(0,0,1))
    displaced=v('ADD',inp.outputs['Position'],v('SCALE',v('NORMALIZE',direction),sample_distance))
    valid=m('GREATER_THAN',v('LENGTH',direction),0)
    for name,position in [('Current UV',inp.outputs['Position']),('Sample UV',displaced)]:
        shifted=v('ADD',position,offset[:3])
        clip=[m('ADD',v('DOT_PRODUCT',shifted,matrix[:3,j]),float((1+offset[3])*matrix[3,j])) for j in range(4)]
        valid=m('MULTIPLY',valid,m('GREATER_THAN',clip[3],0))
        x=m('MULTIPLY',m('ADD',m('MULTIPLY',m('DIVIDE',clip[0],clip[3]),.5),.5),scale[0])
        y=m('MULTIPLY',m('SUBTRACT',.5,m('MULTIPLY',m('DIVIDE',clip[1],clip[3]),.5)),scale[1])
        combine=nodes.new('ShaderNodeCombineXYZ');put(combine.inputs[0],x);put(combine.inputs[1],y);links.new(combine.outputs[0],out.inputs[name])
    links.new(valid,out.inputs['Valid'])
    return group
