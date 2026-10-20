"""Apply recorded Source material groups through stable original slot indices."""
from .asset_converter import sha
from .game_assets import safe_resource_path
from .npr_material_identity import compiled_resource
from .model_material_groups import validate_material_group_contract,material_group_summary,validate_material_group_consent


def resource(material):
    return safe_resource_path(compiled_resource(material.get('vmat',{}).get('Name'),'.vmat'))


def _vertex_color(material,obj):
    """Match glTF's COLOR_0 multiplication for a material-only import."""
    if material.get('vmat',{}).get('IntParams',{}).get('F_VERTEX_COLOR')!=1:return
    color=obj.data.color_attributes.active_color
    if color is None or not len(color.data):raise ValueError('Material-group target requires actual mesh vertex colors')
    nodes=material.node_tree.nodes;links=material.node_tree.links
    existing=[node for node in nodes if node.type=='VERTEX_COLOR']
    if existing:
        if len(existing)!=1:raise ValueError('Ambiguous converted target vertex colors')
        existing[0].layer_name=color.name
        return
    bsdfs=[node for node in nodes if node.type=='BSDF_PRINCIPLED']
    if len(bsdfs)!=1:raise ValueError('Vertex-colored target lacks a qualified converted PBR graph')
    socket=bsdfs[0].inputs['Base Color'];source=socket.links[0].from_socket if socket.is_linked else None
    value=tuple(socket.default_value)
    if socket.is_linked:links.remove(socket.links[0])
    vertex=nodes.new('ShaderNodeVertexColor');vertex.layer_name=color.name
    mix=nodes.new('ShaderNodeMixRGB');mix.blend_type='MULTIPLY';mix.inputs[0].default_value=1
    if source is None:mix.inputs[1].default_value=value
    else:links.new(source,mix.inputs[1])
    links.new(vertex.outputs['Color'],mix.inputs[2]);links.new(mix.outputs[0],socket)


def apply_material_groups(objects,contract,samples,data,fps,*,poses_requested,partial):
    import bpy
    from .blender_scene import tick_frame
    validate_material_group_contract(contract,samples)
    validate_material_group_consent(contract,poses_requested=poses_requested,partial=partial)
    meshes=[obj for obj in objects if obj.type=='MESH']
    base={}
    for obj in meshes:
        if not obj.material_slots or any(slot.material is None for slot in obj.material_slots):
            raise ValueError('Material-group mesh has empty material slots')
        for slot in obj.material_slots:base.setdefault(resource(slot.material),slot.material)
    if set(base)!=set(contract['base_materials']):raise ValueError('Imported original material coverage differs from material-group contract')
    track=contract['track']['selections']
    if not track or not any(row['replacements'] for row in track):return material_group_summary(contract)
    templates=dict(base);bundle=contract.get('bundle');new_templates=[]
    if bundle:
        if sha(bundle['path'])!=bundle['sha256']:raise ValueError('Material-group bundle identity changed')
        before=set(bpy.data.materials);objects_before=set(bpy.data.objects)
        properties=bpy.ops.import_scene.gltf.get_rna_type().properties
        if 'import_unused_materials' not in properties:raise ValueError('Material-group bundles require Blender unused-material import support')
        from .path_io import native_path
        bpy.ops.import_scene.gltf(filepath=native_path(bundle['path']),import_pack_images=True,import_unused_materials=True)
        if set(bpy.data.objects)!=objects_before:raise ValueError('Material-only bundle created unexpected scene objects')
        new_templates=list(set(bpy.data.materials)-before)
        if len(new_templates)!=len(bundle['materials']) or {resource(m) for m in new_templates}!=set(bundle['materials']):
            raise ValueError('Imported material-bundle resource coverage differs')
        from .blender_materials import repair_imported_unlit
        repair_imported_unlit(new_templates)
        templates.update({resource(m):m for m in new_templates})
    groups=contract['groups']
    # The node tree value stores a small resolved index, never a uint32 token.
    # Every comparison reads the ORIGINAL MaterialIndex field and there is only
    # one SetMaterialIndex, so a0->b1 and b1->c2 cannot cascade in the same frame.
    for obj in meshes:
        original=list(obj.data.materials);mappings={};targets={}
        for row in track:
            mapping=mappings.setdefault(row['group_index'],{})
            for index,material in enumerate(original):
                target=row['replacements'].get(resource(material))
                if target is None:continue
                if target not in templates:raise ValueError('Selected material-group target is unavailable')
                mapping[index]=target
                targets[target]=None
        if not targets:continue
        # Appended slots are actor/mesh-owned, preserving differing COLOR_0 data
        # and avoiding writes to a shared imported material or another actor.
        for target in targets:
            material=templates[target].copy();material.use_fake_user=False
            _vertex_color(material,obj)
            obj.data.materials.append(material);targets[target]=len(obj.data.materials)-1
        tree=bpy.data.node_groups.new('AGR source material slots','GeometryNodeTree')
        tree.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
        tree.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
        nodes=tree.nodes;links=tree.links
        inlet=nodes.new('NodeGroupInput');outlet=nodes.new('NodeGroupOutput')
        source=nodes.new('GeometryNodeInputMaterialIndex').outputs[0]
        selected=nodes.new('ShaderNodeValue');selected.name='Resolved Source material group'
        for row in track:
            selected.outputs[0].default_value=row['group_index']
            selected.outputs[0].keyframe_insert('default_value',frame=tick_frame(row['tick'],data['start_tick'],data['tick_rate'],fps))
        action=tree.animation_data.action
        curves=[curve for layer in action.layers for strip in layer.strips for bag in strip.channelbags for curve in bag.fcurves] if hasattr(action,'layers') else action.fcurves
        for curve in curves:
            for key in curve.keyframe_points:key.interpolation='CONSTANT'
        value=source
        for group,mapping in sorted(mappings.items()):
            for index,target in sorted(mapping.items()):
                chosen=nodes.new('ShaderNodeMath');chosen.operation='COMPARE';links.new(selected.outputs[0],chosen.inputs[0]);chosen.inputs[1].default_value=group;chosen.inputs[2].default_value=.1
                slot=nodes.new('ShaderNodeMath');slot.operation='COMPARE';links.new(source,slot.inputs[0]);slot.inputs[1].default_value=index;slot.inputs[2].default_value=.1
                both=nodes.new('ShaderNodeMath');both.operation='MULTIPLY';links.new(chosen.outputs[0],both.inputs[0]);links.new(slot.outputs[0],both.inputs[1])
                switch=nodes.new('GeometryNodeSwitch');switch.input_type='INT';links.new(both.outputs[0],switch.inputs['Switch']);links.new(value,switch.inputs['False']);switch.inputs['True'].default_value=targets[target];value=switch.outputs[0]
        setter=nodes.new('GeometryNodeSetMaterialIndex');links.new(inlet.outputs['Geometry'],setter.inputs['Geometry']);links.new(value,setter.inputs['Material Index']);links.new(setter.outputs['Geometry'],outlet.inputs['Geometry'])
        tree['agr_source_material_group_contract']=contract['content_sha256']
        modifier=obj.modifiers.new('AGR Source material group','NODES');modifier.node_group=tree
        obj['agr_source_material_group_contract']=contract['content_sha256']
    for material in new_templates:
        material.use_fake_user=False
        if material.users==0:bpy.data.materials.remove(material)
    return material_group_summary(contract)
