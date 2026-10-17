"""Explicit-matrix NPR albedo stage; runtime matrix sourcing remains external."""
import numpy as np
from agr.npr_albedo import albedo_color


def create_albedo_group(*, correction_matrix, tint_matrix, vertex_strength,
                        apply_tint_to_vertex_colors, mask_vertex_tint, mask_color_tint):
    # Validate all construction controls before allocating Blender datablocks.
    albedo_color([[0,0,0]],[[1,1,1]],[0],correction_matrix=correction_matrix,
                 tint_matrix=tint_matrix,vertex_strength=vertex_strength,
                 apply_tint_to_vertex_colors=apply_tint_to_vertex_colors,
                 mask_vertex_tint=mask_vertex_tint,mask_color_tint=mask_color_tint)
    import bpy
    group=bpy.data.node_groups.new('AGR research NPR albedo','ShaderNodeTree')
    group['scope']='Explicit row-vector matrices and linear instance color; no engine matrix or runtime binding inference'
    for name in ['Sampled Color','Instance Color']:
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Tint Mask',in_out='INPUT',socket_type='NodeSocketFloat')
    group.interface.new_socket(name='Albedo',in_out='OUTPUT',socket_type='NodeSocketVector')
    nodes,links=group.nodes,group.links
    inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def vector(op,a,b):
        n=nodes.new('ShaderNodeVectorMath');n.operation=op;put(n.inputs[0],a)
        put(n.inputs[3] if op=='SCALE' else n.inputs[1],b)
        return n.outputs['Vector']
    def multiply(a,b):
        n=nodes.new('ShaderNodeMath');n.operation='MULTIPLY';put(n.inputs[0],a);put(n.inputs[1],b)
        return n.outputs[0]
    def mix(a,b,w):
        return vector('ADD',a,vector('SCALE',vector('SUBTRACT',b,a),w))
    def transform(color,matrix):
        matrix=np.asarray(matrix,dtype=float)
        combined=nodes.new('ShaderNodeCombineXYZ')
        for i in range(3):
            dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT'
            put(dot.inputs[0],color);put(dot.inputs[1],matrix[:3,i])
            add=nodes.new('ShaderNodeMath');add.operation='ADD'
            links.new(dot.outputs['Value'],add.inputs[0]);add.inputs[1].default_value=matrix[3,i]
            links.new(add.outputs[0],combined.inputs[i])
        return combined.outputs[0]
    mask=inp.outputs['Tint Mask'];color=inp.outputs['Sampled Color']
    weight=multiply(mask,vertex_strength) if mask_vertex_tint else vertex_strength
    def vertex(c):return mix(c,vector('MULTIPLY',c,inp.outputs['Instance Color']),weight)
    if apply_tint_to_vertex_colors:color=vertex(color)
    corrected=transform(color,correction_matrix)
    color=mix(corrected,transform(corrected,tint_matrix),mask if mask_color_tint else 1)
    if not apply_tint_to_vertex_colors:color=vertex(color)
    links.new(color,out.inputs['Albedo'])
    return group
