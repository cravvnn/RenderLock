"""Connected NPR indirect stages, with explicit qualified caller inputs."""
from agr.blender_npr_bounce import create_bounce_group
from agr.blender_npr_exposure import create_exposure_group
from agr.blender_npr_material import create_material_group


def create_indirect_group(*,sharpness,influence_range,exposure_enabled,
                          exposure_targets,exposure_pbr_blend,
                          diffuse_pbr_blend,no_specular_at_full_roughness,
                          reflection_ceiling_parameters=None):
    import bpy
    bounce=create_bounce_group(sharpness=sharpness,influence_range=influence_range)
    exposure=create_exposure_group(enabled=exposure_enabled,targets=exposure_targets,pbr_blend=exposure_pbr_blend)
    material=create_material_group(diffuse_pbr_blend=diffuse_pbr_blend,no_specular_at_full_roughness=no_specular_at_full_roughness)
    group=bpy.data.node_groups.new('AGR research NPR indirect','ShaderNodeTree')
    group['scope']='Explicit completed probe faces, normalized reflection, camera and exposure; caller validation required'
    vectors=('+X','+Y','+Z','-X','-Y','-Z','Normal','Primary','View','Transmissive','Reflection','Albedo','BRDF')
    scalars=('Material AO','Distance Field AO','Screen Factor','Pre Exposure','Metallic','Roughness','NdotV')
    if reflection_ceiling_parameters is not None:
        vectors=tuple('Radiance' if n=='Reflection' else n for n in vectors)
        scalars+=('Denominator',)
        group['scope']='Completed probe faces and raw radiance, positive SH denominator, explicit camera/exposure; caller validation required'
    for names,kind in [(vectors,'NodeSocketVector'),(scalars,'NodeSocketFloat')]:
        for name in names:group.interface.new_socket(name=name,in_out='INPUT',socket_type=kind)
    for name in ('Irradiance','Diffuse','Specular','Total'):
        group.interface.new_socket(name=name,in_out='OUTPUT',socket_type='NodeSocketVector')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    stages=[]
    for tree in (bounce,exposure,material):
        node=nodes.new('ShaderNodeGroup');node.node_tree=tree;stages.append(node)
        for socket in node.inputs:
            if socket.name in vectors+scalars:links.new(inp.outputs[socket.name],socket)
    b,e,m=stages
    links.new(b.outputs['Direction'],e.inputs['Direction']);links.new(b.outputs['Blend'],e.inputs['Blend'])
    links.new(e.outputs['Irradiance'],m.inputs['Irradiance']);links.new(e.outputs['Irradiance'],out.inputs['Irradiance'])
    if reflection_ceiling_parameters is not None:
        from agr.blender_npr_reflection import create_normalization_group
        norm=nodes.new('ShaderNodeGroup');norm.node_tree=create_normalization_group(ceiling_parameters=reflection_ceiling_parameters,exposure_enabled=exposure_enabled)
        for name in ('Radiance','Roughness','Denominator'):links.new(inp.outputs[name],norm.inputs[name])
        links.new(e.outputs['Irradiance'],norm.inputs['Irradiance']);links.new(norm.outputs['Reflection'],m.inputs['Reflection'])
    ao=nodes.new('ShaderNodeMath');ao.operation='MULTIPLY'
    links.new(inp.outputs['Material AO'],ao.inputs[0]);links.new(inp.outputs['Distance Field AO'],ao.inputs[1]);links.new(ao.outputs[0],m.inputs['AO'])
    for name in ('Diffuse','Specular','Total'):links.new(m.outputs[name],out.inputs[name])
    return group
