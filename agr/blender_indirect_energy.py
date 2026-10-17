"""Blender implementation of the qualified BRDF LUT indirect energy terms."""


def create_energy_group(image):
    import bpy
    if tuple(image.size)!=(64,64) or image.colorspace_settings.name!='Non-Color':
        raise ValueError('Expected linear data image of BRDF layer1')
    group=bpy.data.node_groups.new('AGR Source indirect energy','ShaderNodeTree')
    group.interface.new_socket(name='Albedo',in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Metallic','Roughness','NdotV'):group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    for name in ('Single','Multiple','Diffuse Energy','Correction'):group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketVector')
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
        return node.outputs[0]

    def split(value):
        node=nodes.new('ShaderNodeSeparateXYZ');put(node.inputs[0],value);return list(node.outputs)

    def combine(values):
        node=nodes.new('ShaderNodeCombineXYZ')
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]

    def clamp(x):return m('MINIMUM',1,m('MAXIMUM',0,x))

    color=inputs.outputs['Albedo'];metal=inputs.outputs['Metallic']
    rough=m('MAXIMUM',.05,inputs.outputs['Roughness']);view=clamp(inputs.outputs['NdotV'])
    uv=combine([m('ADD',m('MULTIPLY',rough,.984375),.0078125),m('ADD',m('MULTIPLY',m('SQRT',m('SUBTRACT',1,view)),.984375),.0078125),0])
    tex=nodes.new('ShaderNodeTexImage');tex.image=image;tex.interpolation='Linear';tex.extension='EXTEND';links.new(uv,tex.inputs['Vector'])
    x,y,_=split(tex.outputs['Color']);r,g,b=split(color)
    dark=clamp(m('MULTIPLY',m('MAXIMUM',r,m('MAXIMUM',g,b)),25))
    f0=v('SCALE',v('ADD',(.04,.04,.04),v('SCALE',v('SUBTRACT',color,(.04,.04,.04)),metal)),dark)
    single=v('ADD',combine([x,x,x]),v('SCALE',f0,m('SUBTRACT',y,x)))
    missing=m('SUBTRACT',1,y)
    average=v('ADD',f0,v('SCALE',v('SUBTRACT',(1,1,1),f0),.0476190485060215))
    multiple=v('SCALE',v('DIVIDE',v('MULTIPLY',single,average),v('SUBTRACT',(1,1,1),v('SCALE',average,missing))),missing)
    diffuse=v('SUBTRACT',(1,1,1),v('ADD',single,multiple))
    correction=v('ADD',v('SUBTRACT',v('SUBTRACT',v('SCALE',color,2.04040002822876),(.33239999413490295,)*3),
        v('SUBTRACT',v('SCALE',color,4.795100212097168),(.6417000293731689,)*3)),v('ADD',v('SCALE',color,2.755199909210205),(.6902999877929688,)*3))
    for name,socket in [('Single',single),('Multiple',multiple),('Diffuse Energy',diffuse),('Correction',correction)]:links.new(socket,output.inputs[name])
    return group
