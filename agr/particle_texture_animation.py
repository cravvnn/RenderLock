"""Frame selection and original-atlas UVs for bounded flat particle sheets."""
from __future__ import annotations
import math
import re
import struct
from .installed_effects import UnsupportedEffect


def validate_sheet(sheet):
    if not isinstance(sheet,dict) or type(sheet.get('schema_version')) is not int or sheet['schema_version']!=1 or sheet.get('policy')!='agr_source_particle_sheet_v1':
        raise UnsupportedEffect('Unqualified original particle texture sheet')
    sequences=sheet.get('sequences')
    if not isinstance(sequences,list) or not 1<=len(sequences)<=256:
        raise UnsupportedEffect('Invalid particle sheet sequence inventory')
    total_frames=0
    for index,sequence in enumerate(sequences):
        if not isinstance(sequence,dict):raise UnsupportedEffect('Invalid particle sheet sequence')
        frames=sequence.get('frames')
        if (type(sequence.get('index')) is not int or sequence['index']!=index or type(sequence.get('clamp')) is not bool
                or type(sequence.get('total_time')) not in (int,float)
                or not math.isfinite(sequence['total_time']) or sequence['total_time']<=0
                or not isinstance(frames,list) or not 1<=len(frames)<=4096):
            raise UnsupportedEffect('Invalid particle sheet timing or order')
        total_frames+=len(frames)
        if total_frames>65536:raise UnsupportedEffect('Particle sheet total frame bound exceeded')
        for frame in frames:
            if not isinstance(frame,dict):raise UnsupportedEffect('Invalid particle sheet frame')
            duration=frame.get('duration')
            low,high=frame.get('min'),frame.get('max')
            if (type(duration) not in (int,float) or not math.isfinite(duration) or duration<0
                    or not isinstance(low,list) or not isinstance(high,list) or len(low)!=2 or len(high)!=2
                    or any(type(v) not in (int,float) or not math.isfinite(v) for v in low+high)
                    or any(not 0<=a<b<=1 for a,b in zip(low,high))):
                raise UnsupportedEffect('Invalid or empty particle sheet frame')
    return sheet


def validate_asset_sheet(texture, input_hashes):
    """Bind typed timing to the exact compiled texture, not only a policy name."""
    sheet=validate_sheet(texture.get('source_sheet'))
    resource=texture.get('resource')
    if (not isinstance(resource,str) or not resource.endswith('.vtex_c')
            or not isinstance(sheet.get('input_sha256'),str)
            or re.fullmatch('[0-9a-f]{64}',sheet['input_sha256']) is None
            or sheet['input_sha256']!=input_hashes.get(resource.removesuffix('_c'))):
        raise UnsupportedEffect('Original particle sheet source identity differs from its asset packet')
    return sheet


def _float32(value):
    if type(value) not in (int,float):
        raise UnsupportedEffect('Invalid particle animation float32 input')
    try: result=struct.unpack('<f',struct.pack('<f',value))[0]
    except (OverflowError,struct.error) as exc:
        raise UnsupportedEffect('Particle animation input exceeds float32') from exc
    if not math.isfinite(result):raise UnsupportedEffect('Particle animation input exceeds float32')
    return result


def renderer_settings(renderer,definition):
    """Resolve the reference sprite constructor's authored behavior-version gate."""
    version=definition.get('m_nBehaviorVersion',0)
    if type(version) is not int or not 0<=version<2**31:
        raise UnsupportedEffect('Invalid particle sheet behavior version')
    result=dict(renderer)
    if renderer.get('_class')=='C_OP_RenderSprites' and version<10:
        result['m_nAnimationType']='ANIMATION_TYPE_FIXED_RATE'
    return result


def sheet_frame(sheet,sequence,particle,renderer):
    if type(sequence) is not int or sequence<0:
        raise UnsupportedEffect('Invalid particle sheet sequence index')
    if type(renderer.get('m_bAnimateInFPS',False)) is not bool:
        raise UnsupportedEffect('Invalid particle animation FPS mode')
    seq=sheet['sequences'][sequence%len(sheet['sequences'])]
    frames=seq['frames']
    if len(frames)==1:return frames[0],frames[0],0.
    mode=renderer.get('m_nAnimationType','ANIMATION_TYPE_FIXED_RATE')
    rate=_float32(renderer.get('m_flAnimationRate',.1))
    total=_float32(seq['total_time'])
    if total<=0:raise UnsupportedEffect('Particle sheet total time underflows float32')
    if mode=='ANIMATION_TYPE_MANUAL_FRAMES':
        passes=_float32(particle.get('manual_frame'))
    elif mode in ('ANIMATION_TYPE_FIXED_RATE','ANIMATION_TYPE_FIT_LIFETIME'):
        age=_float32(particle.get('age'))
        if age<0:
            raise UnsupportedEffect('Invalid particle animation age')
        if mode=='ANIMATION_TYPE_FIT_LIFETIME':
            life=_float32(particle.get('life'))
            age=_float32(age/max(_float32(.0001),life))
        passes=_float32(age*rate)
        if renderer.get('m_bAnimateInFPS',False):passes=_float32(passes/total)
    else:raise UnsupportedEffect('Unsupported particle animation type '+str(mode))
    if type(passes) not in (int,float) or not math.isfinite(passes):
        raise UnsupportedEffect('Missing or invalid manual particle animation frame')
    position=_float32(total*(max(0.,min(1.,passes)) if seq['clamp'] else _float32(passes-math.floor(passes))))
    start=0.
    for index,frame in enumerate(frames[:-1]):
        duration=_float32(frame['duration']);following=_float32(start+duration)
        if following>position:
            return frame,frames[index+1],_float32(_float32(position-start)/duration) if duration>0 else 0.
        start=following
    if seq['clamp']:return frames[-1],frames[-1],0.
    span=_float32(total-start)
    return frames[-1],frames[0],_float32(_float32(position-start)/span) if span>0 else 0.


def blender_rect(frame):
    # Source sheet bounds and PNG crop rectangles start at the upper left.
    # Blender's image UV v=0 addresses the lower edge of that same PNG.
    low,high=frame['min'],frame['max']
    return (low[0],1-high[1]),(high[0]-low[0],high[1]-low[1])


def build_sheet_nodes(nodes,links,image,uv,sheet,sequence,renderer,samples,frame_number,constant_paths,*,motion=None):
    from types import SimpleNamespace
    from .particle_texture_layers import LayerNodes,mix
    validate_sheet(sheet)
    if type(renderer.get('m_bBlendFramesSeq0',True)) is not bool or type(renderer.get('m_bMaxLuminanceBlendingSequence0',False)) is not bool:
        raise UnsupportedEffect('Invalid particle animation blend mode')
    if renderer.get('m_bMaxLuminanceBlendingSequence0'):
        raise UnsupportedEffect('Luminance-weighted animated sheet blending requires separate qualification')
    graph=LayerNodes(nodes,links)
    def sampler(displacement=None,operation='ADD'):
        scale=nodes.new('ShaderNodeVectorMath');scale.operation='MULTIPLY';links.new(uv,scale.inputs[0])
        offset=nodes.new('ShaderNodeVectorMath');offset.operation='ADD';links.new(scale.outputs['Vector'],offset.inputs[0])
        coordinate=offset.outputs['Vector']
        if displacement is not None:
            warp=nodes.new('ShaderNodeVectorMath');warp.operation=operation
            links.new(coordinate,warp.inputs[0]);links.new(displacement,warp.inputs[1]);coordinate=warp.outputs['Vector']
        tex=nodes.new('ShaderNodeTexImage');tex.image=image;links.new(coordinate,tex.inputs['Vector'])
        return tex,scale.inputs[1],offset.inputs[1],coordinate
    if motion is not None and renderer.get('m_bBlendFramesSeq0',True)is not True:
        raise UnsupportedEffect('Motion-vector sheet requires separate blended frames')
    first,first_scale,first_offset,first_uv=sampler(None if motion is None else motion[0],'SUBTRACT')
    blend=renderer.get('m_bBlendFramesSeq0',True)
    second,second_scale,second_offset,next_uv=sampler(None if motion is None else motion[1]) if blend else (None,None,None,None)
    amount=nodes.new('ShaderNodeValue') if blend else None
    for tick,_,particle in samples:
        a,b,fraction=sheet_frame(sheet,sequence,particle,renderer)
        frame=frame_number(tick)
        for source,scale,offset in [(a,first_scale,first_offset)]+([(b,second_scale,second_offset)] if blend else []):
            low,span=blender_rect(source)
            scale.default_value=(*span,1);offset.default_value=(*low,0)
            for socket in (scale,offset):
                socket.keyframe_insert('default_value',frame=frame)
                constant_paths.add(socket.path_from_id('default_value'))
        if blend:
            amount.outputs[0].default_value=fraction;amount.outputs[0].keyframe_insert('default_value',frame=frame)
            constant_paths.add(amount.outputs[0].path_from_id('default_value'))
    if blend:
        color=nodes.new('ShaderNodeMixRGB');links.new(amount.outputs[0],color.inputs[0])
        links.new(first.outputs['Color'],color.inputs[1]);links.new(second.outputs['Color'],color.inputs[2])
        alpha=mix(first.outputs['Alpha'],second.outputs['Alpha'],amount.outputs[0],graph.op)
        outputs={'Color':color.outputs[0],'Alpha':alpha}
    else:outputs=first.outputs
    # DistortPrevious in the reference shader re-samples the previous layer at
    # its current-frame atlas UV, without repeating its frame cross-fade.
    # Retain that actual texture sampler and its keyed atlas mapping.
    return SimpleNamespace(outputs=outputs,animated=True,distortion_source=first,current_sampler=first,next_sampler=second,
        current_uv=first_uv,next_uv=next_uv,blend_fraction=amount.outputs[0]if blend else None),first_uv
