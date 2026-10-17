"""Apply explicitly partial static diffuse lighting to qualified imported draws."""
import math


def apply_map_lighting(meshes, package, images, coordinates):
    import bpy
    from .blender_source_lightmap import create_diffuse_group
    from .blender_source_material import create_diffuse_material_group

    if package.get('allow_partial') is not True:
        raise ValueError('Partial Source lighting must be explicitly selected')
    coordinates = coordinates if isinstance(coordinates,dict) else {}
    yaw = float(coordinates.get('world_yaw_degrees',0))
    if not math.isfinite(yaw):
        raise ValueError('Invalid lighting coordinate yaw')
    loaded = {}
    for name,path in images.items():
        if name == 'BRDF':
            continue
        image = bpy.data.images.load(str(path),check_existing=False)
        if tuple(image.size)!=(8192,8192):
            raise ValueError('Decoded Source lightmap has unexpected dimensions')
        image.colorspace_settings.name = 'Linear Rec.709' if name=='DC' else 'Non-Color'
        loaded[name] = image
    group = create_diffuse_group()
    material_group = create_diffuse_material_group()
    energy_group = None
    if 'BRDF' in images:
        from .source_brdf_assets import read_layer
        from .blender_indirect_energy import create_energy_group
        lut = bpy.data.images.new('AGR Source BRDF layer1',width=64,height=64,alpha=True,float_buffer=True)
        lut.colorspace_settings.name = 'Non-Color'
        lut.alpha_mode = 'CHANNEL_PACKED'
        lut.pixels.foreach_set(read_layer(images['BRDF']).reshape(-1))
        lut.pack()
        energy_group = create_energy_group(lut)
    applied, unsupported = [], []
    for obj in sorted(meshes,key=lambda o:o.name):
        metadata = obj.get('agr_source_draw')
        if metadata is None or not metadata.get('qualified'):
            unsupported.append(dict(object=obj.name,reason='No qualified baked lightmap input'))
            continue
        channel = metadata.get('uv_channel')
        if not metadata.get('uv_scale_baked') or not isinstance(channel,int) or not 0<=channel<len(obj.data.uv_layers):
            raise ValueError(f'Qualified lightmap UV binding missing on {obj.name}')
        if len(obj.material_slots)!=1 or obj.material_slots[0].material is None:
            raise ValueError('Qualified draw must import as one material slot')
        original = obj.material_slots[0].material
        nodes = original.node_tree.nodes if original.use_nodes else []
        shaders = [n for n in nodes if n.type=='BSDF_PRINCIPLED']
        outputs = [n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        if len(shaders)!=1 or len(outputs)!=1:
            unsupported.append(dict(object=obj.name,reason='Material is not a single Principled surface'))
            continue
        bsdf,output = shaders[0],outputs[0]
        surface = output.inputs['Surface']
        if len(surface.links)!=1 or surface.links[0].from_node!=bsdf:
            unsupported.append(dict(object=obj.name,reason='Layered/transparent surface not qualified'))
            continue
        if bsdf.inputs['Alpha'].is_linked or bsdf.inputs['Alpha'].default_value!=1:
            unsupported.append(dict(object=obj.name,reason='Alpha material response not qualified'))
            continue
        material = original.copy()
        material.name = original.name+' | Source diffuse'
        obj.material_slots[0].material = material
        nodes,links = material.node_tree.nodes,material.node_tree.links
        bsdf = nodes[bsdf.name]
        output = nodes[output.name]
        lighting = nodes.new('ShaderNodeGroup')
        lighting.node_tree = group
        uv = nodes.new('ShaderNodeUVMap')
        uv.uv_map = obj.data.uv_layers[channel].name
        for name,image in loaded.items():
            tex = nodes.new('ShaderNodeTexImage')
            tex.name = 'AGR lightmap '+name
            tex.image = image
            tex.interpolation = 'Linear'
            tex.extension = 'EXTEND'
            links.new(uv.outputs['UV'],tex.inputs['Vector'])
            links.new(tex.outputs['Color'],lighting.inputs[name])
        if bsdf.inputs['Normal'].is_linked:
            normal = bsdf.inputs['Normal'].links[0].from_socket
        else:
            normal = nodes.new('ShaderNodeNewGeometry').outputs['Normal']
        world_normal = normal
        if yaw:
            rotate = nodes.new('ShaderNodeVectorRotate')
            rotate.rotation_type = 'Z_AXIS'
            rotate.inputs['Angle'].default_value = -math.radians(yaw)
            links.new(normal,rotate.inputs['Vector'])
            normal = rotate.outputs['Vector']
        links.new(normal,lighting.inputs['Normal'])
        multiply = nodes.new('ShaderNodeGroup')
        multiply.name = 'AGR diffuse material response'
        multiply.node_tree = material_group
        links.new(lighting.outputs['Diffuse'],multiply.inputs['Lighting'])
        color = bsdf.inputs['Base Color']
        if color.is_linked:
            links.new(color.links[0].from_socket,multiply.inputs['Albedo'])
        else:
            multiply.inputs['Albedo'].default_value = color.default_value
        metallic = bsdf.inputs['Metallic']
        if metallic.is_linked:
            links.new(metallic.links[0].from_socket,multiply.inputs['Metallic'])
        else:
            multiply.inputs['Metallic'].default_value = metallic.default_value
        emit = nodes.new('ShaderNodeEmission')
        final_diffuse = multiply.outputs['Diffuse']
        if energy_group is not None:
            energy = nodes.new('ShaderNodeGroup')
            energy.name = 'AGR indirect energy'
            energy.node_tree = energy_group
            for old,new in [('Base Color','Albedo'),('Metallic','Metallic'),('Roughness','Roughness')]:
                socket = bsdf.inputs[old]
                if socket.is_linked:
                    links.new(socket.links[0].from_socket,energy.inputs[new])
                else:
                    energy.inputs[new].default_value = socket.default_value[:3] if old=='Base Color' else socket.default_value
            geometry = nodes.new('ShaderNodeNewGeometry')
            dot = nodes.new('ShaderNodeVectorMath'); dot.operation = 'DOT_PRODUCT'
            links.new(geometry.outputs['Incoming'],dot.inputs[0]); links.new(world_normal,dot.inputs[1])
            links.new(dot.outputs['Value'],energy.inputs['NdotV'])
            def product(name,a,b):
                node=nodes.new('ShaderNodeVectorMath');node.operation='MULTIPLY';node.name=name
                links.new(a,node.inputs[0]);links.new(b,node.inputs[1]);return node.outputs[0]
            reduced=product('AGR indirect reduced',lighting.outputs['Diffuse'],energy.outputs['Diffuse Energy'])
            corrected=product('AGR indirect corrected',reduced,energy.outputs['Correction'])
            links.new(corrected,multiply.inputs['Lighting'])
            multiple=product('AGR indirect multiple',lighting.outputs['Diffuse'],energy.outputs['Multiple'])
            total=nodes.new('ShaderNodeVectorMath');total.operation='ADD';total.name='AGR indirect final sum'
            links.new(final_diffuse,total.inputs[0]);links.new(multiple,total.inputs[1])
            final_diffuse=total.outputs[0]
        from .blender_material_emission import add_converted_emission
        final_diffuse=add_converted_emission(nodes,links,bsdf,final_diffuse)
        links.new(final_diffuse,emit.inputs['Color'])
        for link in list(output.inputs['Surface'].links):
            links.remove(link)
        links.new(emit.outputs[0],output.inputs['Surface'])
        material['agr_source_lighting_mode'] = 'partial_static_diffuse'
        obj['agr_source_lighting_applied'] = True
        applied.append(dict(object=obj.name,material=material.name,uv_layer=uv.uv_map,
            indirect_energy='source_brdf_layer1' if energy_group is not None else 'legacy_unreduced',
            diffuse_material='converted_albedo_times_one_minus_metallic',source=dict(metadata)))
    if not applied:
        raise ValueError('No qualified Source lightmapped surfaces were applied')
    return dict(status='partial',mode='static_diffuse',applied=applied,unsupported=unsupported,
        shader_sha256=package['shader_sha256'],world_sha256=package['world_sha256'],
        limits=['Static SH diffuse multiplied by converted glTF albedo and (1 - metallic), using converted normal maps.',
                'Indirect diffuse energy and multiple scatter use the Source BRDF LUT when present; reflected single scatter remains absent.',
                'Converted emission retained; full Source self illumination, materials, direct lights/shadows, probes and fog incomplete.',
                'Unqualified draws retain their converted materials; coverage remains explicitly partial.'])
