"""Explicit-input Blender NPR exposure/color reference node group."""
import math
from agr.npr_exposure import RGB_TO_XYZ,XYZ_TO_RGB


def create_exposure_group(*,enabled,targets,pbr_blend):
    import bpy
    if type(enabled) is not bool or len(targets)!=3 or not all(math.isfinite(x) and x>=0 for x in targets) or not math.isfinite(pbr_blend) or not 0<=pbr_blend<=1:
        raise ValueError('Explicit NPR exposure controls required')
    group=bpy.data.node_groups.new('AGR research NPR exposure','ShaderNodeTree')
    group['scope']='Completed six probe faces, unit direction, positive pre-exposure/neutral luminance; runtime gates supplied explicitly'
    for name in ('+X','+Y','+Z','-X','-Y','-Z','Direction','Transmissive'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketVector')
    for name in ('Blend','Pre Exposure'):
        group.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat')
    group.interface.new_socket(name='Irradiance',in_out='OUTPUT',socket_type='NodeSocketVector')
    nodes,links=group.nodes,group.links;inp=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
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
    def combine(*values):
        node=nodes.new('ShaderNodeCombineXYZ')
        for socket,value in zip(node.inputs,values):put(socket,value)
        return node.outputs[0]
    def split(value):
        node=nodes.new('ShaderNodeSeparateXYZ');put(node.inputs[0],value);return node.outputs
    def sat(value):return m('MINIMUM',1,m('MAXIMUM',0,value))
    def xyy(rgb):
        xyz=[v('DOT_PRODUCT',rgb,row) for row in RGB_TO_XYZ]
        total=m('ADD',m('ADD',xyz[0],xyz[1]),xyz[2])
        return [m('DIVIDE',xyz[0],total),m('DIVIDE',xyz[1],total),xyz[1]]
    faces=[inp.outputs[name] for name in ('+X','+Y','+Z','-X','-Y','-Z')]
    direction=split(inp.outputs['Direction']);raw=(0,0,0);average=(0,0,0)
    for i,face in enumerate(faces):
        component=direction[i%3]
        if i>=3:component=m('MULTIPLY',-1,component)
        positive=m('MAXIMUM',0,component)
        raw=v('ADD',raw,v('SCALE',face,m('MULTIPLY',positive,positive)))
        average=v('ADD',average,face)
    up=faces[2]
    if enabled:
        neutral=xyy(v('SCALE',average,1/6))
        def retarget(value,target):
            current=xyy(value);weight=sat(m('DIVIDE',current[2],neutral[2]))
            xy=[m('ADD',a,m('MULTIPLY',m('SUBTRACT',b,a),weight)) for a,b in zip(neutral[:2],current[:2])]
            # Qualified positive neutral RGB ensures nonzero xy. A zero target
            # produces zero XYZ naturally, matching the compiled branch.
            scale=m('DIVIDE',target,xy[1])
            xyz=combine(m('MULTIPLY',scale,xy[0]),target,m('MULTIPLY',scale,m('SUBTRACT',m('SUBTRACT',1,xy[0]),xy[1])))
            rgb=combine(*(v('DOT_PRODUCT',xyz,row) for row in XYZ_TO_RGB))
            return v('ADD',v('SCALE',rgb,1-pbr_blend),v('SCALE',value,pbr_blend))
        vertical=m('ADD',targets[2],m('MULTIPLY',targets[0]-targets[2],m('GREATER_THAN',direction[2],0)))
        desired=m('ADD',m('MULTIPLY',targets[1],m('ADD',m('MULTIPLY',direction[0],direction[0]),m('MULTIPLY',direction[1],direction[1]))),m('MULTIPLY',vertical,m('MULTIPLY',direction[2],direction[2])))
        raw=retarget(raw,m('DIVIDE',desired,inp.outputs['Pre Exposure']))
        up=retarget(up,m('DIVIDE',targets[0],inp.outputs['Pre Exposure']))
    final=v('ADD',raw,v('SCALE',v('MULTIPLY',up,inp.outputs['Transmissive']),m('SUBTRACT',1,inp.outputs['Blend'])))
    links.new(final,out.inputs['Irradiance'])
    return group
