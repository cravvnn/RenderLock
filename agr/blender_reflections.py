"""Research cubemap blend with explicit tint/normalization assumptions.

Requires fully covered positions and positive blended normalization. The caller
must separately qualify probe coverage; no residual environment is fabricated.
"""
import numpy as np
from agr.source_probe_assets import vector,rotation
from agr.source_probe_blending import probe_weights
from agr.blender_reflection_coordinates import create_coordinates_group
from agr.blender_reflection_normalization import create_normalization_group


def create_reflections_group(probes,cube_groups,coefficients,tints,ceiling_parameters,*,roughness_floor=.05,mip_scale=6):
    import bpy
    probe_weights(probes,np.empty((0,3)))
    identities={str(p['hammeruniqueid']) for p in probes}
    if any(set(d)!=identities for d in (cube_groups,coefficients,tints)):
        raise ValueError('Exact probe assets and explicit tints required')
    for identity in identities:
        sh=np.asarray(coefficients[identity],dtype=float);tint=vector(tints[identity])
        if sh.shape!=(4,) or not np.isfinite(sh).all() or sh[3]<=np.linalg.norm(sh[:3]) or np.any(tint<0):
            raise ValueError('Unqualified normalization or tint')
    group=bpy.data.node_groups.new('AGR research reflections','ShaderNodeTree')
    for name in ('Position','Normal','View','Irradiance'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Roughness',in_out='INPUT',socket_type='NodeSocketFloat')
    for name in ('Radiance','Reflection'):
        group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketColor')
    for name in ('Residual','Normalization','Denominator'):
        group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketFloat')
    group['agr_scope']='Fully covered research probes; explicit tint and ceiling, ideal cube filtering; no engine parity claim'
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def m(op,*values):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]
    def v(op,a,b):
        node=nodes.new('ShaderNodeVectorMath');node.operation=op
        put(node.inputs[0],a);put(node.inputs[3] if op=='SCALE' else node.inputs[1],b)
        return node.outputs['Value' if op=='DOT_PRODUCT' else 'Vector']
    remaining=1;rgb=(0,0,0);sh_vector=(0,0,0);sh_constant=0
    for p in sorted(probes,key=lambda p:(-p['indoor_outdoor_level'],p['array_index'])):
        identity=str(p['hammeruniqueid']);local=v('SUBTRACT',inp.outputs['Position'],vector(p['origin']));edge=1
        for axis,lo,hi,fade in zip(rotation(p['angles']),vector(p['box_mins']),vector(p['box_maxs']),vector(p['edge_fade_dists'])):
            coordinate=v('DOT_PRODUCT',local,axis)
            edge=m('MINIMUM',edge,m('MINIMUM',m('DIVIDE',m('SUBTRACT',coordinate,lo-.02),fade+.02),m('DIVIDE',m('SUBTRACT',hi+.02,coordinate),fade+.02)))
        edge=m('MAXIMUM',0,edge);alpha=m('MULTIPLY',m('MULTIPLY',edge,edge),m('SUBTRACT',3,m('MULTIPLY',2,edge)))
        weight=m('MULTIPLY',alpha,remaining);remaining=m('MULTIPLY',remaining,m('SUBTRACT',1,alpha))
        coords=nodes.new('ShaderNodeGroup');coords.node_tree=create_coordinates_group(p,roughness_floor=roughness_floor,mip_scale=mip_scale)
        for name in ('Position','Normal','View','Roughness'):links.new(inp.outputs[name],coords.inputs[name])
        cube=nodes.new('ShaderNodeGroup');cube.node_tree=cube_groups[identity]
        links.new(coords.outputs['Direction'],cube.inputs['Direction']);links.new(coords.outputs['LOD'],cube.inputs['LOD'])
        rgb=v('ADD',rgb,v('SCALE',v('MULTIPLY',cube.outputs['Color'],tints[identity]),weight))
        sh_vector=v('ADD',sh_vector,v('SCALE',coefficients[identity][:3],weight))
        sh_constant=m('ADD',sh_constant,m('MULTIPLY',coefficients[identity][3],weight))
    norm=nodes.new('ShaderNodeGroup');norm.node_tree=create_normalization_group(ceiling_parameters)
    for name in ('Normal','Irradiance','Roughness'):links.new(inp.outputs[name],norm.inputs[name])
    put(norm.inputs['SH Vector'],sh_vector);put(norm.inputs['SH Constant'],sh_constant)
    put(out.inputs['Radiance'],rgb);put(out.inputs['Reflection'],v('SCALE',rgb,norm.outputs['Factor']))
    put(out.inputs['Residual'],remaining);links.new(norm.outputs['Factor'],out.inputs['Normalization']);links.new(norm.outputs['Denominator'],out.inputs['Denominator'])
    return group
