"""Ideal seamless cube lookup through padded face stacks and explicit mip blend."""


def create_cube_group(images, *, pack_mips=False):
    import bpy
    if len(images)!=7:raise ValueError('Expected seven qualified cube mip images')
    # Keep each mip's seamless gutters while sharing one GPU texture binding.
    # Pixel-space coordinates stay identical; only their atlas normalization changes.
    for level,image in enumerate(images):
        padded=(256>>level)+2
        if tuple(image.size)!=(padded,6*padded) or image.colorspace_settings.name!='Non-Color':
            raise ValueError('Invalid padded cube mip')
    atlas=None
    offsets=[]
    if pack_mips:
        import numpy as np
        width=258;height=sum(image.size[1] for image in images)
        pixels=np.zeros((height,width,4),dtype=np.float32)
        offset=0
        for image in images:
            w,h=image.size
            values=np.empty(w*h*4,dtype=np.float32);image.pixels.foreach_get(values)
            if not np.isfinite(values).all() or np.any(values<0):
                raise ValueError('Invalid cube pixels for mip atlas')
            pixels[offset:offset+h,:w]=values.reshape(h,w,4)
            offsets.append(offset);offset+=h
        atlas=bpy.data.images.new('AGR packed cube mips',width=width,height=height,
                                  alpha=True,float_buffer=True)
        atlas.colorspace_settings.name='Non-Color';atlas.alpha_mode='CHANNEL_PACKED'
        atlas.pixels.foreach_set(pixels.reshape(-1));atlas.pack()
        atlas['agr_cube_mip_offsets']=offsets
    group=bpy.data.node_groups.new('AGR seamless cube','ShaderNodeTree')
    group['agr_cube_mips_packed']=bool(pack_mips)
    group.interface.new_socket(name='Direction',in_out='INPUT',socket_type='NodeSocketVector')
    group.interface.new_socket(name='LOD',in_out='INPUT',socket_type='NodeSocketFloat')
    group.interface.new_socket(name='Color',in_out='OUTPUT',socket_type='NodeSocketColor')
    nodes,links=group.nodes,group.links;inputs=nodes.new('NodeGroupInput');output=nodes.new('NodeGroupOutput')

    def put(socket,value):
        if hasattr(value,'node'):links.new(value,socket)
        else:socket.default_value=value

    def m(op,*values):
        node=nodes.new('ShaderNodeMath');node.operation=op
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]

    def v(op,a,b):
        node=nodes.new('ShaderNodeVectorMath');node.operation=op;put(node.inputs[0],a);put(node.inputs[3] if op=='SCALE' else node.inputs[1],b);return node.outputs[0]

    def add3(a,b,c):return m('ADD',m('ADD',a,b),c)
    def one_minus(x):return m('SUBTRACT',1,x)
    split=nodes.new('ShaderNodeSeparateXYZ');links.new(inputs.outputs['Direction'],split.inputs[0]);x,y,z=split.outputs
    ax,ay,az=[m('ABSOLUTE',q) for q in (x,y,z)]
    choose_x=m('MULTIPLY',one_minus(m('LESS_THAN',ax,ay)),one_minus(m('LESS_THAN',ax,az)))
    choose_y=m('MULTIPLY',one_minus(choose_x),one_minus(m('LESS_THAN',ay,az)))
    choose_z=one_minus(m('ADD',choose_x,choose_y))
    negative=[m('LESS_THAN',q,0) for q in (x,y,z)]
    signs=[m('SUBTRACT',1,m('MULTIPLY',2,q)) for q in negative]
    denominators=[m('MAXIMUM',1e-20,q) for q in (ax,ay,az)]
    sc=[m('DIVIDE',m('MULTIPLY',m('MULTIPLY',-1,signs[0]),z),denominators[0]),m('DIVIDE',x,denominators[1]),m('DIVIDE',m('MULTIPLY',signs[2],x),denominators[2])]
    tc=[m('DIVIDE',m('MULTIPLY',-1,y),denominators[0]),m('DIVIDE',m('MULTIPLY',signs[1],z),denominators[1]),m('DIVIDE',m('MULTIPLY',-1,y),denominators[2])]
    choices=[choose_x,choose_y,choose_z]
    u=m('MULTIPLY',.5,m('ADD',1,add3(*(m('MULTIPLY',a,b) for a,b in zip(sc,choices)))))
    t=m('MULTIPLY',.5,m('ADD',1,add3(*(m('MULTIPLY',a,b) for a,b in zip(tc,choices)))))
    face=add3(*(m('MULTIPLY',weight,m('ADD',2*i,negative[i])) for i,weight in enumerate(choices)))
    lod=m('MINIMUM',6,m('MAXIMUM',0,inputs.outputs['LOD']));result=(0,0,0)
    for level,image in enumerate(images):
        n=256>>level;padded=n+2
        if tuple(image.size)!=(padded,6*padded) or image.colorspace_settings.name!='Non-Color':raise ValueError('Invalid padded cube mip')
        coords=nodes.new('ShaderNodeCombineXYZ')
        put(coords.inputs[0],m('DIVIDE',m('ADD',m('MULTIPLY',u,n),1),width if atlas else padded))
        vertical=m('ADD',m('ADD',m('MULTIPLY',t,n),1),m('MULTIPLY',face,padded))
        if atlas:vertical=m('ADD',vertical,offsets[level])
        put(coords.inputs[1],m('DIVIDE',vertical,height if atlas else 6*padded))
        tex=nodes.new('ShaderNodeTexImage');tex.image=atlas or image;tex.interpolation='Linear';tex.extension='EXTEND';links.new(coords.outputs[0],tex.inputs['Vector'])
        weight=m('MAXIMUM',0,one_minus(m('ABSOLUTE',m('SUBTRACT',lod,level))))
        result=v('ADD',result,v('SCALE',tex.outputs['Color'],weight))
    links.new(result,output.inputs['Color']);return group
