"""Explicit partial NPR preparation after generic hero lighting.

Caller must have applied validated generic lighting and evaluated surface coverage
for these objects. Primary/exposure remain reference inputs; authored albedo
and frame-dependent rim are caller stages. This is not a general graph importer.
"""
import bpy
from agr.blender_probe_light import create_probe_group
from agr.blender_probe_blend import create_blend_group
from agr.blender_npr_indirect import create_indirect_group
from agr.blender_npr_material_inputs import bind_material_inputs, validate_material_inputs
from agr.npr_material_identity import bind_evidence, material_resource
from agr.blender_material_emission import add_converted_emission

from pathlib import Path
import numpy as np
from agr.source_hero_lighting_package import validate_hero_lighting_package


def preflight_materials(originals, textures, *, objects=None):
    """Validate every material/user before constructing any NPR datablocks."""
    plans=[]
    selected=set(bpy.data.objects if objects is None else objects)
    for original in originals:
        evidence=bind_evidence(original,textures,texture_parameter='g_tNprTransmissiveColor')
        users=[o for o in bpy.data.objects if o.type=='MESH' and any(s.material==original for s in o.material_slots)]
        if not users:raise ValueError('NPR material has no mesh users: '+original.name)
        if any(o not in selected for o in users):raise ValueError('NPR material shared outside selected objects')
        uv_names=set()
        for obj in users:
            active=[u.name for u in obj.data.uv_layers if u.active_render]
            if len(active)!=1:raise ValueError('Expected one render UV layer: '+obj.name)
            uv_names.add(active[0])
        if len(uv_names)!=1:raise ValueError('Shared material users disagree on render UV layer')
        uv_name=uv_names.pop()
        validate_material_inputs(original,evidence['pixels'],uv_name=uv_name)
        nodes=original.node_tree.nodes
        for name,inputs in [('AGR hero probes',('Position','Normal')),('AGR hero reflections',('View',)),('AGR indirect energy',('NdotV',))]:
            node=nodes.get(name)
            if node is None or node.type!='GROUP' or node.node_tree is None:
                raise ValueError('Missing generic lighting group: '+name)
            if any(key not in node.inputs or len(node.inputs[key].links)!=1 for key in inputs):
                raise ValueError('Missing generic lighting input: '+name)
        energy=nodes['AGR indirect energy'].node_tree
        floors=[n for n in energy.nodes if n.type=='MATH' and n.operation=='MAXIMUM' and abs(n.inputs[0].default_value-.05)<1e-6]
        if len(floors)!=1 or len([n for n in energy.nodes if n.type=='TEX_IMAGE'])!=1 or len([n for n in energy.nodes if n.type=='GROUP_OUTPUT'])!=1:
            raise ValueError('Unqualified generic BRDF sampling graph')
        if len([n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output])!=1:
            raise ValueError('Expected one active material output')
        plans.append((original,users,uv_name,evidence))
    return plans


def prepare(package, textures, primary, *, materials, objects, world_sha256,
            manifest_dir, allow_partial=False, albedo_controls=None):
    if allow_partial is not True:raise ValueError('Explicit partial NPR preparation required')
    originals=list(materials);objects=list(objects)
    if not originals or len(set(originals))!=len(originals) or not objects:
        raise ValueError('Explicit distinct materials and mesh objects required')
    if any(o.type!='MESH' for o in objects):raise ValueError('Only selected mesh objects allowed')
    if {s.material for o in objects for s in o.material_slots}!=set(originals):
        raise ValueError('Selected material/object coverage differs')
    if any(not m.use_nodes or m.get('agr_source_hero_lighting')!='partial_indirect_reference' for m in originals):
        raise ValueError('Expected prepared generic hero materials')
    if any('AGR NPR indirect preview' in m.node_tree.nodes for m in originals):
        raise ValueError('NPR preparation already applied')
    primary=np.asarray(primary,dtype=float)
    if primary.shape!=(3,) or not np.isfinite(primary).all() or abs(np.linalg.norm(primary)-1)>1e-5:
        raise ValueError('Explicit normalized primary direction required')
    plans=preflight_materials(originals,textures,objects=objects)
    if albedo_controls is not None and set(albedo_controls)!={material_resource(m) for m in originals}:
        raise ValueError('Exact authored albedo control coverage required')
    resolved=validate_hero_lighting_package(package,world_sha256,manifest_dir)
    reflection_trees={m.node_tree.nodes['AGR hero reflections'].node_tree for m in originals}
    coordinate_plans={}
    for tree in reflection_trees:
        coordinates=[n for n in tree.nodes if n.type=='GROUP' and n.node_tree.name.startswith('AGR reflection coordinates')]
        if len(coordinates)!=len(package['plan']['probes']):
            raise ValueError('Reflection coordinate/probe coverage differs')
        for node in coordinates:
            floors=[n for n in node.node_tree.nodes if n.type=='MATH' and n.operation=='MAXIMUM' and abs(n.inputs[0].default_value-.05)<1e-6]
            if len(floors)!=1:raise ValueError('Unqualified generic reflection roughness floor')
        outputs={socket.name for socket in tree.interface.items_tree if socket.item_type=='SOCKET' and socket.in_out=='OUTPUT'}
        if not {'Radiance','Denominator'}<=outputs:
            raise ValueError('Raw reflection outputs required')
        coordinate_plans[tree]=coordinates
    # Do not trust image display names alone when the saved-scene pin is absent.
    for row in package['atlas']:
        image=bpy.data.images.get('AGR probe '+row['name'])
        width,height,depth=row['size']
        if image is None or list(image.size)!=[width,height*depth] or image.colorspace_settings.name!='Non-Color' or image.alpha_mode!='CHANNEL_PACKED':
            raise ValueError('Prepared atlas image layout/sampling differs')
        actual=np.empty(len(image.pixels),dtype=np.float32);image.pixels.foreach_get(actual)
        expected=np.fromfile(resolved['atlas'][row['name']],dtype='<f4')
        if not np.array_equal(actual,expected):raise ValueError('Prepared atlas pixels differ from validated package')
    corrected_reflections={}
    for tree in coordinate_plans:
        corrected=tree.copy()
        for node in corrected.nodes:
            if node.type=='GROUP' and node.node_tree.name.startswith('AGR reflection coordinates'):
                node.node_tree=node.node_tree.copy()
                floor=next(n for n in node.node_tree.nodes if n.type=='MATH' and n.operation=='MAXIMUM' and abs(n.inputs[0].default_value-.05)<1e-6)
                floor.inputs[0].default_value=0
        corrected_reflections[tree]=corrected
    raw={}
    for prop in package['plan']['probes']:
     identity=str(prop['hammeruniqueid']);prefix=identity.replace(':','_')+'-'
     regions={r['name'][len(prefix):]:(bpy.data.images['AGR probe '+r['name']],r) for r in package['atlas'] if r['name'].startswith(prefix)}
     raw[identity]=create_probe_group(prop,regions,world_sha256=package['world_sha256'])
    probe_tree=create_blend_group(package['plan']['probes'],raw)
    npr_tree=create_indirect_group(sharpness=.9,influence_range=[.4,1],exposure_enabled=True,exposure_targets=[1,.5,.1],exposure_pbr_blend=0,diffuse_pbr_blend=.25,no_specular_at_full_roughness=False,reflection_ceiling_parameters=[2,1])
    rows=[];replacements=[]
    for original,users,uv_name,evidence in plans:
     mat=original.copy();mat.name=original.name+' | NPR preview';nodes,links=mat.node_tree.nodes,mat.node_tree.links
     oldprobe=nodes['AGR hero probes'];reflection=nodes['AGR hero reflections'];energy=nodes['AGR indirect energy']
     reflection.node_tree=corrected_reflections[reflection.node_tree]
     probe=nodes.new('ShaderNodeGroup');probe.node_tree=probe_tree;probe.name='AGR NPR actual probes'
     for name in ('Position','Normal'):links.new(oldprobe.inputs[name].links[0].from_socket,probe.inputs[name])
     npr=nodes.new('ShaderNodeGroup');npr.node_tree=npr_tree;npr.name='AGR NPR indirect preview'
     for name in ('+X','+Y','+Z','-X','-Y','-Z'):links.new(probe.outputs[name],npr.inputs[name])
     links.new(oldprobe.inputs['Normal'].links[0].from_socket,npr.inputs['Normal']);links.new(reflection.inputs['View'].links[0].from_socket,npr.inputs['View'])
     links.new(reflection.outputs['Radiance'],npr.inputs['Radiance']);links.new(reflection.outputs['Denominator'],npr.inputs['Denominator'])
     dot=nodes.new('ShaderNodeMath');dot.operation='MAXIMUM';dot.inputs[1].default_value=0;links.new(energy.inputs['NdotV'].links[0].from_socket,dot.inputs[0]);links.new(dot.outputs[0],npr.inputs['NdotV'])
     for name,value in [('Primary',primary),('Pre Exposure',1.0),('Distance Field AO',1.0),('Screen Factor',1.0)]:npr.inputs[name].default_value=value
     row=bind_material_inputs(mat,npr,evidence['pixels'],uv_name=uv_name)
     # Reuse the qualified LUT sampling graph, removing the generic .05 floor.
     lut_tree=energy.node_tree.copy();lut_tree.name='AGR NPR BRDF sampling'
     lut_tree.interface.new_socket(name='BRDF',in_out='OUTPUT',socket_type='NodeSocketVector')
     floor=[n for n in lut_tree.nodes if n.type=='MATH' and n.operation=='MAXIMUM' and abs(n.inputs[0].default_value-.05)<1e-6];assert len(floor)==1;floor[0].inputs[0].default_value=0
     tex=next(n for n in lut_tree.nodes if n.type=='TEX_IMAGE');output=next(n for n in lut_tree.nodes if n.type=='GROUP_OUTPUT');lut_tree.links.new(tex.outputs['Color'],output.inputs['BRDF'])
     lut=nodes.new('ShaderNodeGroup');lut.node_tree=lut_tree
     for name in ('Roughness','NdotV'):links.new(npr.inputs[name].links[0].from_socket,lut.inputs[name])
     links.new(lut.outputs['BRDF'],npr.inputs['BRDF'])
     bsdf=next(n for n in nodes if n.type=='BSDF_PRINCIPLED')
     if albedo_controls is not None:
      from agr.blender_npr_albedo_inputs import bind_albedo
      source=bsdf.inputs['Base Color'].links[0].from_node
      row['authored_albedo']=bind_albedo(mat,npr,expected_image=source.image,
          color_resource=mat['vmat']['TextureParams']['g_tColor'],uv_name=uv_name,
          **albedo_controls[material_resource(mat)])
     final=add_converted_emission(nodes,links,bsdf,npr.outputs['Total'])
     emit=nodes.new('ShaderNodeEmission');links.new(final,emit.inputs['Color']);surface=next(n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output);links.new(emit.outputs[0],surface.inputs['Surface'])
     replacements.append((original,mat,users))
     rows.append(dict(material=mat.name,source_material=material_resource(mat),converted_emission_preserved=True,reflection_normalization='npr_adjusted_irradiance',reflection_roughness_floor=0,**row))
    # Construction failures leave original mesh bindings intact. Unused allocated
    # datablocks may remain in memory; the caller must not save a failed scene.
    for original,mat,users in replacements:
     for obj in users:
      for slot in obj.material_slots:
       if slot.material==original:slot.material=mat
    return rows
