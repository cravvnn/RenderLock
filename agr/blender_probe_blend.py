"""Blend existing raw probe groups using the authored priority reference."""
from agr.source_probe_assets import vector,rotation
from agr.source_probe_blending import probe_weights


def create_blend_group(probes,raw_groups):
    import bpy
    import numpy as np
    probe_weights(probes,np.empty((0,3)))  # Fail closed on unsupported metadata.
    ordered=sorted(probes,key=lambda p:(-p['indoor_outdoor_level'],p['array_index']))
    group=bpy.data.node_groups.new('AGR research blended probes','ShaderNodeTree')
    group['agr_scope']='Raw irradiance with local VRF priority/box constants; engine scales/material response separate'
    for name in ('Position','Normal'):group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Irradiance',in_out='OUTPUT',socket_type='NodeSocketColor')
    group.interface.new_socket(name='Residual',in_out='OUTPUT',socket_type='NodeSocketFloat')
    faces=('+X','+Y','+Z','-X','-Y','-Z')
    for name in faces:group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketColor')
    nodes,links=group.nodes,group.links;inputs=nodes.new('NodeGroupInput');output=nodes.new('NodeGroupOutput')

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

    remaining=1;result=(0,0,0);face_results={name:(0,0,0) for name in faces}
    for p in ordered:
        identity=str(p['hammeruniqueid']);local=v('SUBTRACT',inputs.outputs['Position'],vector(p['origin']))
        edge=1
        for axis,lo,hi,fade in zip(rotation(p['angles']),vector(p['box_mins']),vector(p['box_maxs']),vector(p['edge_fade_dists'])):
            coordinate=v('DOT_PRODUCT',local,axis)
            left=m('DIVIDE',m('SUBTRACT',coordinate,lo-.02),fade+.02)
            right=m('DIVIDE',m('SUBTRACT',hi+.02,coordinate),fade+.02)
            edge=m('MINIMUM',edge,m('MINIMUM',left,right))
        edge=m('MAXIMUM',0,edge)
        alpha=m('MULTIPLY',m('MULTIPLY',edge,edge),m('SUBTRACT',3,m('MULTIPLY',2,edge)))
        weight=m('MULTIPLY',alpha,remaining)
        remaining=m('MULTIPLY',remaining,m('SUBTRACT',1,alpha))
        sample=nodes.new('ShaderNodeGroup');sample.node_tree=raw_groups[identity]
        for name in ('Position','Normal'):links.new(inputs.outputs[name],sample.inputs[name])
        result=v('ADD',result,v('SCALE',sample.outputs['Irradiance'],weight))
        for name in faces:face_results[name]=v('ADD',face_results[name],v('SCALE',sample.outputs[name],weight))
    put(output.inputs['Irradiance'],result);put(output.inputs['Residual'],remaining)
    for name in faces:put(output.inputs[name],face_results[name])
    return group
