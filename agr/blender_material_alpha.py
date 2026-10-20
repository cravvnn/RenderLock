"""Retain converted scalar/cutout/translucent opacity in reference lighting.

The original importer owns the alpha graph, including its threshold/factors.
Reusing that socket preserves its spatial coverage without making the surface
opaque. The qualified pbr additive permutation writes premultiplied RGB and
zero alpha into One/InvSrcAlpha blending: keep the destination and add the
alpha-weighted lighting response. Source depth/fog/status parity is separate.
"""
import math


def validate_alpha(material,bsdf):
    flags=material.get('vmat',{}).get('IntParams',{})
    additive=flags.get('F_ADDITIVE_BLEND')==1
    if additive and (material.get('vmat',{}).get('ShaderName')!='pbr.vfx'
                     or flags.get('F_TRANSLUCENT')!=1):
        raise ValueError('Unqualified additive material family')
    if additive and not hasattr(material,'surface_render_method'):
        raise ValueError('Source additive reference requires Blender blended surface support')
    alpha=bsdf.inputs['Alpha']
    if len(alpha.links)>1 or (not alpha.is_linked and
            (not math.isfinite(alpha.default_value) or not 0<=alpha.default_value<=1)):
        raise ValueError('Invalid converted material opacity')
    return dict(mode='source_additive_reference' if additive else
                     'converted_alpha' if alpha.is_linked or alpha.default_value!=1 else 'opaque',
                spatial=bool(alpha.is_linked),
                constant=None if alpha.is_linked else float(alpha.default_value))


def alpha_surface(material,bsdf,shader):
    """Return a shader socket with the original alpha coverage applied once."""
    receipt=validate_alpha(material,bsdf)
    if receipt['mode']=='opaque':return shader,receipt
    nodes,links=material.node_tree.nodes,material.node_tree.links
    transparent=nodes.new('ShaderNodeBsdfTransparent')
    transparent.name='AGR preserved transparent coverage'
    mixed=nodes.new('ShaderNodeMixShader');mixed.name='AGR preserved material alpha'
    alpha=bsdf.inputs['Alpha']
    if alpha.is_linked:links.new(alpha.links[0].from_socket,mixed.inputs[0])
    else:mixed.inputs[0].default_value=alpha.default_value
    if receipt['mode']=='source_additive_reference':
        # Eevee's dithered coverage discards zero-alpha additive emission.
        # Blended surfaces retain the premultiplied source RGB with alpha0.
        material.surface_render_method='BLENDED'
        # An unconnected shader is the zero closure. Weight only the emitted
        # source by coverage; the destination remains fully transmitted.
        links.new(shader,mixed.inputs[2])
        added=nodes.new('ShaderNodeAddShader');added.name='AGR Source additive response'
        links.new(transparent.outputs[0],added.inputs[0]);links.new(mixed.outputs[0],added.inputs[1])
        return added.outputs[0],receipt
    links.new(transparent.outputs[0],mixed.inputs[1]);links.new(shader,mixed.inputs[2])
    return mixed.outputs[0],receipt


def lighting_emission(material,bsdf,surface):
    """Find the active reference emission behind the preserved alpha wrapper."""
    receipt=validate_alpha(material,bsdf)
    if len(surface.links)!=1:raise ValueError('One prepared lighting surface required')
    node=surface.links[0].from_node
    if node.type=='EMISSION' and receipt['mode']=='opaque':return node
    additive=receipt['mode']=='source_additive_reference'
    if additive:
        if (node.type!='ADD_SHADER' or len(node.inputs[0].links)!=1
                or node.inputs[0].links[0].from_node.type!='BSDF_TRANSPARENT'
                or len(node.inputs[1].links)!=1):
            raise ValueError('Expected source additive lighting response')
        node=node.inputs[1].links[0].from_node
    if node.type!='MIX_SHADER' or receipt['mode']=='opaque':
        raise ValueError('Expected emission with preserved alpha coverage')
    alpha=bsdf.inputs['Alpha'];factor=node.inputs[0]
    if (factor.is_linked!=alpha.is_linked or
            (alpha.is_linked and (len(factor.links)!=1 or factor.links[0].from_socket!=alpha.links[0].from_socket))
            or (not alpha.is_linked and factor.default_value!=alpha.default_value)
            or (additive and node.inputs[1].is_linked)
            or (not additive and (len(node.inputs[1].links)!=1 or node.inputs[1].links[0].from_node.type!='BSDF_TRANSPARENT'))
            or len(node.inputs[2].links)!=1 or node.inputs[2].links[0].from_node.type!='EMISSION'):
        raise ValueError('Prepared alpha coverage binding changed')
    return node.inputs[2].links[0].from_node
