"""Explicit-input installed NPR indirect response; no runtime-state inference."""
import math


def create_material_group(*,diffuse_pbr_blend,no_specular_at_full_roughness):
    import bpy
    if not math.isfinite(diffuse_pbr_blend) or not 0<=diffuse_pbr_blend<=1 or type(no_specular_at_full_roughness) is not bool:
        raise ValueError('Explicit bounded NPR material controls required')
    group=bpy.data.node_groups.new('AGR research NPR material','ShaderNodeTree')
    group['scope']='Qualified albedo, BRDF XY, normalized reflection, combined AO; excludes direct/rim/selfillum/fog'
    for name in ('Irradiance','Reflection','Albedo','BRDF'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Metallic','Roughness','NdotV','AO'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    for name in ('Diffuse','Specular','Total'):
        group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketVector')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value
    def m(op,*values):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]
    def v(op,a,b):
        node=nodes.new('ShaderNodeVectorMath');node.operation=op;put(node.inputs[0],a)
        put(node.inputs[3] if op=='SCALE' else node.inputs[1],b)
        return node.outputs['Vector']
    def split(value):
        node=nodes.new('ShaderNodeSeparateXYZ');put(node.inputs[0],value);return node.outputs
    def sat(value):return m('MINIMUM',1,m('MAXIMUM',0,value))
    c=inp.outputs['Albedo'];a=inp.outputs['AO'];r=inp.outputs['Roughness'];metal=inp.outputs['Metallic']
    gate=sat(m('ADD',m('MULTIPLY',m('SUBTRACT',r,.9960784316062927),-255.00001525878906),1)) if no_specular_at_full_roughness else 1
    a2=m('MULTIPLY',a,a);a3=m('MULTIPLY',a2,a)
    first=v('SCALE',v('SUBTRACT',v('SCALE',c,2.0404000282287598),(.33239999413490295,)*3),a3)
    second=v('SCALE',v('SUBTRACT',v('SCALE',c,4.795100212097168),(.6417000293731689,)*3),a2)
    third=v('SCALE',v('ADD',v('SCALE',c,2.755199909210205),(.6902999877929688,)*3),a)
    poly=v('ADD',v('SUBTRACT',first,second),third)
    factor=v('ADD',v('SCALE',poly,diffuse_pbr_blend),(1-diffuse_pbr_blend,)*3)
    diffuse=v('SCALE',v('MULTIPLY',v('MULTIPLY',inp.outputs['Irradiance'],factor),c),m('SUBTRACT',1,m('MULTIPLY',metal,gate)))
    channels=split(c);boost=sat(m('MULTIPLY',m('MAXIMUM',m('MAXIMUM',channels[0],channels[1]),channels[2]),25))
    f0=v('SCALE',v('ADD',v('SCALE',(.03999999910593033,)*3,m('SUBTRACT',1,metal)),v('SCALE',c,metal)),boost)
    lut=split(inp.outputs['BRDF'])
    single=v('ADD',v('SCALE',v('SUBTRACT',(1,1,1),f0),lut[0]),v('SCALE',f0,lut[1]))
    specao=sat(m('ADD',m('SUBTRACT',m('POWER',m('ADD',inp.outputs['NdotV'],a),m('MULTIPLY',r,r)),1),a))
    specular=v('SCALE',v('MULTIPLY',single,inp.outputs['Reflection']),m('MULTIPLY',gate,specao))
    for name,value in [('Diffuse',diffuse),('Specular',specular),('Total',v('ADD',diffuse,specular))]:links.new(value,out.inputs[name])
    return group
