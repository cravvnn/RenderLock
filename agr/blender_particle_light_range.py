"""Finite Cycles range for generic simulated lights under the package owner.

Shader arithmetic has no clock/cache/handler/cleanup owner. The existing bake
authors radius samples on a caller-owned light's embedded node tree, and
build_package_effects owns all new light/action data (ModifierMaskRender.tla).
Radius interpolates linearly; it is squared at shader evaluation, not in keys.
This boundary preserves existing interior power/color. Source edge fade and
photometric equivalence remain unqualified.
"""
import math

POLICY='agr_cycles_particle_hard_range_v1'
SCOPE='Cycles finite hard range; existing calibrated energy and Source edge fade/photometry remain unqualified.'


def configure_point_range(light):
    """Create the tested graph once; return its animated radius socket."""
    import bpy
    if light.type!='POINT':
        raise ValueError('Cycles particle range requires a point light')
    light.use_nodes=True
    tree=light.node_tree
    if tree.animation_data and tree.animation_data.action:
        raise ValueError('Cycles particle range cannot replace an animated shader')
    # Blender may otherwise append an embedded node-tree slot to an existing
    # light/object action. Keep old action contents and slot bindings intact.
    tree.animation_data_create().action=bpy.data.actions.new(light.name+' | particle range')
    nodes,links=light.node_tree.nodes,light.node_tree.links
    nodes.clear()
    output=nodes.new('ShaderNodeOutputLight')
    emission=nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value=(1.,1.,1.,1.)
    links.new(emission.outputs[0],output.inputs['Surface'])
    distance=nodes.new('ShaderNodeLightFalloff')
    distance.inputs['Strength'].default_value=1.
    distance.inputs['Smooth'].default_value=0.
    value=nodes.new('ShaderNodeValue');value.name='AGR particle range'
    value.outputs[0].default_value=.01
    square=nodes.new('ShaderNodeMath');square.operation='MULTIPLY'
    links.new(value.outputs[0],square.inputs[0]);links.new(value.outputs[0],square.inputs[1])
    inside=nodes.new('ShaderNodeMath');inside.operation='LESS_THAN'
    links.new(distance.outputs['Constant'],inside.inputs[0]);links.new(square.outputs[0],inside.inputs[1])
    links.new(inside.outputs[0],emission.inputs['Strength'])
    light['agr_cycles_particle_range_policy']=POLICY
    light['agr_cycles_particle_range_scope']=SCOPE
    return value.outputs[0]


def keyframe_radius(socket,radius,frame):
    if (type(radius) not in (int,float) or not math.isfinite(radius) or not 0<radius<=1e12
            or type(frame) not in (int,float) or not math.isfinite(frame)):
        raise ValueError('Cycles particle range requires finite radius/frame')
    socket.default_value=radius
    if not socket.keyframe_insert('default_value',frame=frame):
        raise RuntimeError('Cycles particle range keyframe insertion failed')


FALLOFF_POLICY='agr_cycles_default_particle_falloff_v1'


def configure_standard_light(light,unit_scale,attenuation):
    """Animate radius, deriving the source-space falloff at shader evaluation.

    The existing package owner and isolated node Action own these allocations.
    No new animation handler or lifetime/cache owner is introduced. Radius is
    keyed before squaring, preserving its interpolation at fractional frames.
    """
    if (type(unit_scale)not in(int,float)or not math.isfinite(unit_scale)
            or not 1e-9<=unit_scale<=1 or attenuation not in('quadratic','linear')):
        raise ValueError('Invalid standard particle light falloff profile')
    radius=configure_point_range(light)
    nodes,links=light.node_tree.nodes,light.node_tree.links
    emission=next(n for n in nodes if n.type=='EMISSION')
    distance=next(n for n in nodes if n.type=='LIGHT_FALLOFF')
    inside=next(n for n in nodes if n.type=='MATH'and n.operation=='LESS_THAN')
    square=inside.inputs[1].links[0].from_socket
    def operation(kind,*values):
        node=nodes.new('ShaderNodeMath');node.operation=kind
        for index,value in enumerate(values):
            if type(value)in(int,float):node.inputs[index].default_value=value
            else:links.new(value,node.inputs[index])
        return node.outputs[0]
    distance_squared=operation('DIVIDE',distance.outputs['Constant'],unit_scale**2)
    range_squared=operation('DIVIDE',square,unit_scale**2)
    distance_power=operation('SQRT',distance_squared)if attenuation=='linear'else distance_squared
    range_power=operation('SQRT',range_squared)if attenuation=='linear'else range_squared
    inverse_distance=operation('DIVIDE',1.,operation('MAXIMUM',distance_power,1.))
    from .particle_standard_light import FALLOFF_EPSILON
    offset=operation('DIVIDE',1.,operation('ADD',range_power,FALLOFF_EPSILON))
    falloff=operation('MINIMUM',operation('MAXIMUM',operation('SUBTRACT',inverse_distance,offset),0.),1.)
    multiplier=operation('MULTIPLY',operation('MULTIPLY',distance_squared,falloff),inside.outputs[0])
    links.new(multiplier,emission.inputs['Strength'])
    light['agr_cycles_particle_range_policy']=FALLOFF_POLICY
    light['agr_cycles_particle_range_scope']='Native default finite '+attenuation+'-minus-boundary falloff; Blender photometry remains approximate.'
    return radius
