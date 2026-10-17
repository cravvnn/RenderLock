"""Blender adaptations for explicitly AGR-repaired materials."""

UNLIT_VISIBILITY = 'camera_reflection_transmission_v1'


def repair_imported_unlit(materials):
    """Keep repaired unlit surfaces visible through glass, without diffuse GI.

    Blender's glTF importer gates emission with Is Camera Ray. Preserve its
    color and alpha graph, extending only that gate to glossy/transmission.
    Unknown importer graphs fail explicitly instead of silently misrendering.
    """
    repaired = []
    for material in materials:
        metadata = material.get('agr_material_repairs', {})
        if metadata.get('unlit_visibility') != UNLIT_VISIBILITY:
            continue
        if material.get('agr_unlit_visibility_applied') == UNLIT_VISIBILITY:
            continue
        tree = material.node_tree
        candidates = [] if tree is None else [link for link in tree.links
            if link.from_node.type == 'LIGHT_PATH'
            and link.from_socket.name == 'Is Camera Ray'
            and link.to_node.type == 'MIX_SHADER'
            and link.to_socket == link.to_node.inputs[0]]
        if len(candidates) != 1:
            raise ValueError(f'Unsupported imported unlit graph: {material.name}')
        link = candidates[0]
        mixer = link.to_node
        if any(len(mixer.inputs[index].links) != 1 or
               mixer.inputs[index].links[0].from_node.type != kind
               for index, kind in ((1, 'BSDF_TRANSPARENT'), (2, 'EMISSION'))):
            raise ValueError(f'Unsupported imported unlit shader branches: {material.name}')
        path, destination = link.from_node, link.to_socket
        first = tree.nodes.new('ShaderNodeMath')
        first.operation = 'MAXIMUM'
        first.label = 'AGR camera or reflection'
        second = tree.nodes.new('ShaderNodeMath')
        second.operation = 'MAXIMUM'
        second.label = 'AGR include transmission'
        first.location = (path.location.x + 180, path.location.y)
        second.location = (path.location.x + 360, path.location.y)
        tree.links.new(path.outputs['Is Camera Ray'], first.inputs[0])
        tree.links.new(path.outputs['Is Glossy Ray'], first.inputs[1])
        tree.links.new(first.outputs[0], second.inputs[0])
        tree.links.new(path.outputs['Is Transmission Ray'], second.inputs[1])
        tree.links.new(second.outputs[0], destination)
        material['agr_unlit_visibility_applied'] = UNLIT_VISIBILITY
        repaired.append(material.name)
    return repaired
