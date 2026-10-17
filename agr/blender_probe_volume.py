"""Trilinear volume sampling from a vertically stacked float image in Blender.

Caller supplies bounded pixel-centre coordinates into a decoded region. The
stack uses Source z/y/x order in Blender's bottom-up pixel buffer, deliberately
without a display-oriented vertical flip.
"""


def create_volume_group(image, size, *, atlas_origin=None):
    import bpy
    width, height, depth = size
    if any(type(n) is not int or n < 1 for n in size):
        raise ValueError('Volume stack dimensions mismatch')
    if atlas_origin is None:
        if tuple(image.size) != (width,height*depth):
            raise ValueError('Volume stack dimensions mismatch')
        offset_x=offset_y=0
    else:
        if (not isinstance(atlas_origin,(tuple,list)) or len(atlas_origin)!=2
                or any(type(n) is not int or n<0 for n in atlas_origin)):
            raise ValueError('Explicit integer volume atlas origin required')
        offset_x,offset_y=atlas_origin
        if offset_x+width>image.size[0] or offset_y+height*depth>image.size[1]:
            raise ValueError('Volume region exceeds atlas')
    if image.colorspace_settings.name != 'Non-Color' or image.alpha_mode != 'CHANNEL_PACKED':
        raise ValueError('Volume image must use unassociated data channels')
    group = bpy.data.node_groups.new('AGR volume '+image.name,'ShaderNodeTree')
    group.interface.new_socket(name='Pixel',in_out='INPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='Color',in_out='OUTPUT',socket_type='NodeSocketColor')
    group.interface.new_socket(name='Alpha',in_out='OUTPUT',socket_type='NodeSocketFloat')
    nodes,links=group.nodes,group.links
    inputs=nodes.new('NodeGroupInput');output=nodes.new('NodeGroupOutput')

    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value

    def m(op,*values):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]

    split=nodes.new('ShaderNodeSeparateXYZ');links.new(inputs.outputs['Pixel'],split.inputs[0])
    x,y,z=[m('MINIMUM',n-1,m('MAXIMUM',0,socket)) for n,socket in zip(size,split.outputs)]
    low=m('FLOOR',z);high=m('MINIMUM',depth-1,m('ADD',low,1));fraction=m('SUBTRACT',z,low)
    textures=[]
    for slice_index in (low,high):
        coords=nodes.new('ShaderNodeCombineXYZ')
        put(coords.inputs[0],m('DIVIDE',m('ADD',x,.5+offset_x),image.size[0]))
        put(coords.inputs[1],m('DIVIDE',m('ADD',m('ADD',y,.5+offset_y),m('MULTIPLY',slice_index,height)),image.size[1]))
        tex=nodes.new('ShaderNodeTexImage');tex.image=image;tex.interpolation='Linear';tex.extension='EXTEND'
        links.new(coords.outputs[0],tex.inputs['Vector']);textures.append(tex)
    difference=nodes.new('ShaderNodeVectorMath');difference.operation='SUBTRACT'
    links.new(textures[1].outputs['Color'],difference.inputs[0]);links.new(textures[0].outputs['Color'],difference.inputs[1])
    scale=nodes.new('ShaderNodeVectorMath');scale.operation='SCALE'
    links.new(difference.outputs[0],scale.inputs[0]);links.new(fraction,scale.inputs[3])
    color=nodes.new('ShaderNodeVectorMath');color.operation='ADD'
    links.new(textures[0].outputs['Color'],color.inputs[0]);links.new(scale.outputs[0],color.inputs[1]);links.new(color.outputs[0],output.inputs['Color'])
    alpha=m('ADD',textures[0].outputs['Alpha'],m('MULTIPLY',fraction,m('SUBTRACT',textures[1].outputs['Alpha'],textures[0].outputs['Alpha'])))
    links.new(alpha,output.inputs['Alpha'])
    return group
