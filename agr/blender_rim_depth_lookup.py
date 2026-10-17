"""Point-clamped lookup of a frame-specific axial Source-depth data image."""


def create_depth_lookup_group(image):
    import bpy
    if image.colorspace_settings.name!='Non-Color' or image.alpha_mode!='CHANNEL_PACKED':
        raise ValueError('Independent linear depth data channels required')
    group=bpy.data.node_groups.new('AGR frame rim depth lookup','ShaderNodeTree')
    group['scope']='Top-down UV inputs; packed image rows are Blender bottom-up; explicit frame-specific depth'
    for name in ('Current UV','Sample UV'):group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Current Depth','Sampled Depth'):group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketFloat')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    for source,target in [('Current UV','Current Depth'),('Sample UV','Sampled Depth')]:
        split=nodes.new('ShaderNodeSeparateXYZ');links.new(inp.outputs[source],split.inputs[0])
        flip=nodes.new('ShaderNodeMath');flip.operation='SUBTRACT';flip.inputs[0].default_value=1;links.new(split.outputs['Y'],flip.inputs[1])
        uv=nodes.new('ShaderNodeCombineXYZ');links.new(split.outputs['X'],uv.inputs['X']);links.new(flip.outputs[0],uv.inputs['Y'])
        tex=nodes.new('ShaderNodeTexImage');tex.image=image;tex.interpolation='Closest';tex.extension='EXTEND';links.new(uv.outputs[0],tex.inputs['Vector'])
        rgb=nodes.new('ShaderNodeSeparateXYZ');links.new(tex.outputs['Color'],rgb.inputs[0]);links.new(rgb.outputs['X'],out.inputs[target])
    return group
