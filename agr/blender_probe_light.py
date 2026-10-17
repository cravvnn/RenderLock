"""Raw six-direction probe evaluation, before volume blend/material response."""
from agr.source_probe_assets import vector, rotation
from agr.blender_probe_volume import create_volume_group
from agr.source_lighting_profiles import WORLD_6774,probe_layout


def create_probe_group(properties, regions,*,world_sha256=WORLD_6774):
    """regions maps irradiance-0..5/shadow to (image, decoded metadata)."""
    import bpy
    p=properties
    dimensions,_=probe_layout(world_sha256)
    if p.get('parentname') or p.get('uselocaloffset'):raise ValueError('Parented probe unqualified')
    group=bpy.data.node_groups.new('AGR raw probe '+str(p['hammeruniqueid']),'ShaderNodeTree')
    for name in ('Position','Normal'):group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Irradiance','Shadow'):group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketColor')
    group.interface.new_socket(name='Shadow Alpha',in_out='OUTPUT',socket_type='NodeSocketFloat')
    for name in ('+X','+Y','+Z','-X','-Y','-Z'):
        group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketColor')
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

    def combine(values):
        node=nodes.new('ShaderNodeCombineXYZ')
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]

    local=v('SUBTRACT',inputs.outputs['Position'],vector(p['origin']))
    low,high=vector(p['box_mins']),vector(p['box_maxs'])
    coordinate=[m('DIVIDE',m('SUBTRACT',v('DOT_PRODUCT',local,axis),lo),hi-lo)
                for axis,lo,hi in zip(rotation(p['angles']),low,high)]
    size=[int(p['light_probe_size_'+a]) for a in 'xyz'];offset=[int(p['light_probe_atlas_'+a]) for a in 'xyz']
    normal_split=nodes.new('ShaderNodeSeparateXYZ');links.new(inputs.outputs['Normal'],normal_split.inputs[0])
    result=(0,0,0)
    for slab in range(6):
        image,metadata=regions['irradiance-'+str(slab)]
        pixel=[]
        for axis,dim in enumerate(dimensions):
            bounded=m('MINIMUM',1-.5/dim,m('MAXIMUM',.5/dim,coordinate[axis]))
            translation=offset[axis]-.5-metadata['start'][axis]+(dimensions[2]*slab if axis==2 else 0)
            pixel.append(m('ADD',m('MULTIPLY',bounded,size[axis]),translation))
        sample=nodes.new('ShaderNodeGroup');sample.node_tree=create_volume_group(image,metadata['size'])
        links.new(combine(pixel),sample.inputs['Pixel'])
        links.new(sample.outputs['Color'],output.inputs[('+X','+Y','+Z','-X','-Y','-Z')[slab]])
        normal=normal_split.outputs[slab%3]
        positive=m('GREATER_THAN',normal,0)
        sign=positive if slab<3 else m('SUBTRACT',1,positive)
        weight=m('MULTIPLY',m('MULTIPLY',normal,normal),sign)
        result=v('ADD',result,v('SCALE',sample.outputs['Color'],weight))
    links.new(result,output.inputs['Irradiance'])
    image,metadata=regions['shadow']
    pixel=[m('ADD',m('MULTIPLY',m('MINIMUM',1,m('MAXIMUM',0,coordinate[i])),size[i]),offset[i]-.5-metadata['start'][i]) for i in range(3)]
    sample=nodes.new('ShaderNodeGroup');sample.node_tree=create_volume_group(image,metadata['size'])
    links.new(combine(pixel),sample.inputs['Pixel']);links.new(sample.outputs['Color'],output.inputs['Shadow']);links.new(sample.outputs['Alpha'],output.inputs['Shadow Alpha'])
    return group
