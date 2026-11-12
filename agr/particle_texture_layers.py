"""Bounded spritecard layer composition from the pinned VRF shader.

Reference: Renderer/Shaders/common/spritecard_channels.slang and
particle_spritecard.frag.slang (MIT). This is texture composition, not Source
lighting, scene-depth interaction, original random seeds or whole-shader parity.
RGBA tuples contain numbers or scalar Blender sockets, so the same equations
can be checked independently of image loading and scene construction.
"""
from __future__ import annotations

import math
import numpy as np
from .installed_effects import UnsupportedEffect

CHANNELS = ('RGB', 'RGBA', 'A', 'RGB_A', 'RGB_ALPHAMASK', 'RGB_RGBMASK',
            'RGBA_RGBALPHA', 'A_RGBALPHA', 'RGB_A_RGBALPHA', 'R', 'G', 'B',
            'RALPHA', 'GALPHA', 'BALPHA')
BLENDS = ('MULTIPLY', 'MOD2X', 'REPLACE', 'ADD', 'SUBTRACT', 'AVERAGE', 'LUMINANCE')
MAX_TEXTURE_LAYERS = 6
LUMA_601 = (.299, .587, .114)
LUMA_709 = (.2125, .7154, .0721)
ROTATION_INPUTS = {'SPRITECARD_TEXTURE_PP_SCALE_ROLL':2,
                   'SPRITECARD_TEXTURE_PP_SCALE_YAW':0,
                   'SPRITECARD_TEXTURE_PP_SCALE_PITCH':1}


def numeric(operation, a, b):
    if operation == 'FRACT': return a-math.floor(a)
    if operation == 'ADD': return a+b
    if operation == 'SUBTRACT': return a-b
    if operation == 'MULTIPLY': return a*b
    if operation == 'MAXIMUM': return max(a,b)
    if operation == 'MINIMUM': return min(a,b)
    if operation == 'DIVIDE': return a/b if b else 0.
    if operation == 'POWER': return a**b
    if operation == 'GREATER_THAN': return float(a>b)
    raise ValueError('Unknown layer arithmetic operation')


def mix(a, b, factor, op=numeric):
    return op('ADD', a, op('MULTIPLY', op('SUBTRACT', b, a), factor))


def zoom_parameters(rate, op=numeric):
    """Installed spritecard PS modes1/4: two centred samples, eased cyclic blend."""
    phase=op('FRACT',rate,0)
    eased=op('DIVIDE',phase,op('ADD',1,op('MULTIPLY',-.4,op('SUBTRACT',1,phase))))
    return mix(4,1,eased,op),mix(1,.0625,eased,op),op('MULTIPLY',eased,eased)


def luma(rgb, weights, op=numeric):
    return op('ADD', op('ADD', op('MULTIPLY',rgb[0],weights[0]),
        op('MULTIPLY',rgb[1],weights[1])), op('MULTIPLY',rgb[2],weights[2]))


def linear_to_gamma(value, op=numeric):
    low=op('MULTIPLY',value,12.92)
    high=op('SUBTRACT',op('MULTIPLY',1.055,
        op('POWER',op('MAXIMUM',value,0),1/2.4)),.055)
    return mix(low,high,op('GREATER_THAN',value,.0031308),op)


def channel_mask(accum, layer, mode, subtract=False, op=numeric):
    if mode not in CHANNELS:
        raise UnsupportedEffect('Unsupported particle texture channel mask '+str(mode))
    rgb, alpha = layer[:3], layer[3]
    keep_rgb = (0,0,0) if subtract else accum[:3]
    keep_alpha = 0 if subtract else accum[3]
    if mode == 'RGB': return (*rgb,keep_alpha)
    if mode == 'RGBA': return tuple(layer)
    if mode == 'A': return (*keep_rgb,alpha)
    if mode == 'RGB_A': return (*rgb,op('MULTIPLY',accum[3],alpha))
    if mode in ('RGB_ALPHAMASK','RGB_RGBMASK'):
        amount=alpha if mode=='RGB_ALPHAMASK' else luma(rgb,LUMA_601,op)
        return (*(mix(a,b,amount,op) for a,b in zip(accum[:3],rgb)),accum[3])
    if mode == 'RGBA_RGBALPHA': return (*rgb,luma(rgb,LUMA_601,op))
    if mode == 'A_RGBALPHA': return (*keep_rgb,luma(rgb,LUMA_601,op))
    if mode == 'RGB_A_RGBALPHA':
        return (*accum[:3],op('MULTIPLY',accum[3],luma(rgb,LUMA_601,op)))
    channel=mode[0]
    value=rgb['RGB'.index(channel)]
    color=tuple(value if not subtract or axis==channel else 0 for axis in 'RGB')
    return (*color,value if mode.endswith('ALPHA') else keep_alpha)


def composite(accum, layer, channel='RGBA', blend='MULTIPLY', amount=1., op=numeric):
    if blend not in BLENDS:
        raise UnsupportedEffect('Unsupported particle texture blend '+str(blend))
    masked=channel_mask(accum,layer,channel,blend=='SUBTRACT',op)
    if blend=='REPLACE': blended=masked
    elif blend=='LUMINANCE':
        lum=luma(masked[:3],LUMA_709,op)
        blended=(*(op('MULTIPLY',c,lum) for c in accum[:3]),masked[3])
    else:
        operation={'MULTIPLY':'MULTIPLY','MOD2X':'MULTIPLY','ADD':'ADD',
                   'SUBTRACT':'SUBTRACT','AVERAGE':'ADD'}[blend]
        blended=tuple(op(operation,a,b) for a,b in zip(accum,masked))
        if blend in ('MOD2X','AVERAGE'):
            blended=tuple(op('MULTIPLY',c,2 if blend=='MOD2X' else .5) for c in blended)
    # The installed four-layer spritecard SPIR-V saturates after each layer.
    # VRF's current GLSL only applies the lower bound; keep the native upper bound.
    return tuple(op('MINIMUM',op('MAXIMUM',mix(a,b,amount,op),0),1) for a,b in zip(accum,blended))


def particle_texture_value(mode,particle,policy):
    if mode=='SPRITECARD_TEXTURE_PP_SCALE_NONE':return 1.
    if mode in ROTATION_INPUTS:
        rotation=particle.get('rotation')
        if (policy!='agr_shader_extra_fields_v1' or not isinstance(rotation,(list,tuple))
                or len(rotation)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in rotation)):
            raise UnsupportedEffect('Missing captured particle texture rotation input')
        # Installed spritecard VS selectors7/8/9 read raw roll/yaw/pitch.
        # Particle snapshots declare (yaw,pitch,roll), in radians.
        return float(rotation[ROTATION_INPUTS[mode]])
    names={'SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1':'shader_extra_data1',
           'SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA2':'shader_extra_data2'}
    if mode not in names:
        raise UnsupportedEffect('Unsupported per-particle texture input '+str(mode))
    value=particle.get(names[mode])
    if policy!='agr_shader_extra_fields_v1' or type(value) not in (int,float) or not math.isfinite(value):
        raise UnsupportedEffect('Missing qualified particle shader extra data; rebuild ParticleProbe and regenerate simulation')
    return float(value)


def alpha_window(alpha,reference,window,zero,one,op=numeric):
    """Installed PS input_4: z=1-reference, w=window; zero-width edge is sharp."""
    cutoff=op('SUBTRACT',1,reference)
    clamp=lambda v:op('MINIMUM',op('MAXIMUM',v,0),1)
    left=clamp(op('DIVIDE',op('SUBTRACT',alpha,cutoff),zero)) if zero else op('GREATER_THAN',alpha,cutoff)
    right=clamp(op('DIVIDE',op('SUBTRACT',op('ADD',cutoff,window),alpha),one)) if one else op('GREATER_THAN',op('ADD',cutoff,window),alpha)
    return op('MULTIPLY',alpha,op('MINIMUM',left,right))


def gradient_source(accum, channel, op=numeric):
    if channel not in CHANNELS:
        raise UnsupportedEffect('Unsupported particle gradient channel mask')
    index=CHANNELS.index(channel)
    alpha_as_luma=index in (2,7)
    premultiply=index not in (0,4,5,9,10,11)  # MASK_RAMP_FLAGS.x == 0
    rgb=(accum[3],)*3 if alpha_as_luma else tuple(
        op('MULTIPLY',c,accum[3]) if premultiply else c for c in accum[:3])
    return luma(tuple(linear_to_gamma(c,op) for c in rgb),LUMA_709,op),alpha_as_luma


def gradient_pixels(gradient):
    """The reference's 256 RGBA8 sRGB ramp, decoded to linear for Blender."""
    if not isinstance(gradient,dict):raise UnsupportedEffect('Invalid particle gradient')
    stops=gradient.get('m_Stops',[])
    if not isinstance(stops,list) or len(stops)>256: raise UnsupportedEffect('Invalid or unbounded particle gradient stops')
    parsed=[]
    for stop in stops:
        if not isinstance(stop,dict): raise UnsupportedEffect('Invalid particle gradient stop')
        position=stop.get('m_flPosition',0)
        color=stop.get('m_Color',[255,255,255])
        if (type(position) not in (int,float) or not math.isfinite(position) or abs(position)>float(np.finfo(np.float32).max)
                or not isinstance(color,list) or len(color) not in (3,4)
                or any(type(c) is not int or not 0<=c<=255 for c in color)):
            raise UnsupportedEffect('Invalid particle gradient color or position')
        parsed.append((float(np.float32(position)),color[:3]))
    pixels=[]
    for x in range(256):
        position=float(np.float32(x)/np.float32(255))
        lower=upper=None
        for p,c in parsed:
            if p<=position and (lower is None or p>=lower[0]): lower=(p,c)
            if p>=position and (upper is None or p<=upper[0]): upper=(p,c)
        if lower is None: lower=upper
        if upper is None: upper=lower
        if lower is None: color=[255]*3
        else:
            span=np.float32(upper[0])-np.float32(lower[0])
            t=(np.float32(position)-np.float32(lower[0]))/span if span>0 else np.float32(0)
            color=[int(np.rint(np.float32(a)+t*(np.float32(b)-np.float32(a)))) for a,b in zip(lower[1],upper[1])]
        rgb=[v/255 for v in color]
        pixels.extend([c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in rgb]+[1.])
    return pixels


class LayerNodes:
    def __init__(self,nodes,links): self.nodes,self.links=nodes,links

    def op(self,operation,a,b):
        if isinstance(a,(int,float)) and isinstance(b,(int,float)):
            return numeric(operation,a,b)
        node=self.nodes.new('ShaderNodeMath');node.operation=operation
        self.connect(a,node.inputs[0]);self.connect(b,node.inputs[1])
        return node.outputs[0]

    def connect(self,value,target):
        if isinstance(value,(int,float)): target.default_value=value
        else: self.links.new(value,target)

    def vector(self,values):
        node=self.nodes.new('ShaderNodeCombineXYZ')
        for v,target in zip(values,node.inputs): self.connect(v,target)
        return node.outputs[0]

    def rgba(self,image):
        split=self.nodes.new('ShaderNodeSeparateXYZ');self.links.new(image.outputs['Color'],split.inputs[0])
        return (*tuple(split.outputs),image.outputs['Alpha'])

    def resample(self,source,uv):
        image=self.nodes.new('ShaderNodeTexImage');image.image=source.image
        image.interpolation=source.interpolation;image.extension=source.extension
        self.links.new(uv,image.inputs['Vector'])
        return self.rgba(image)

    def zoom(self,image,uv,rate):
        source=getattr(image,'distortion_source',image)
        if getattr(source,'animated',False):
            raise UnsupportedEffect('Animated zoom requires its current-frame atlas sampler')
        split=self.nodes.new('ShaderNodeSeparateXYZ');self.links.new(uv,split.inputs[0])
        wide,tight,blend=zoom_parameters(rate,self.op)
        def sample(scale):
            centred=[self.op('ADD',self.op('MULTIPLY',self.op('SUBTRACT',v,.5),scale),.5)
                     for v in tuple(split.outputs)[:2]]
            return self.resample(source,self.vector((*centred,0)))
        a,b=sample(tight),sample(wide)
        return tuple(mix(x,y,blend,self.op) for x,y in zip(a,b))


def enabled_texture_layers(layers):
    """Retain original indices and every enabled authored layer, within bounds.

    The installed crossbow beam authors six meaningful layers (two alternative
    team colors). The pinned VRF viewer caps five; our node composition applies
    the same per-layer equations to all six rather than silently dropping one.
    """
    if not isinstance(layers,list) or len(layers)>64:
        raise UnsupportedEffect('Invalid or unbounded particle texture stack')
    result=[]
    for index,layer in enumerate(layers):
        if not isinstance(layer,dict) or type(layer.get('m_bEnabled',True)) is not bool:
            raise UnsupportedEffect('Invalid particle texture layer or enabled flag')
        if layer.get('m_bEnabled',True):result.append((index,layer))
    if len(result)>MAX_TEXTURE_LAYERS:
        raise UnsupportedEffect('More than six enabled particle texture layers')
    if layers and not result:
        raise UnsupportedEffect('No enabled texture layers; the renderer fallback texture is not captured')
    return result


def needs_extended_sprite_layers(layers):
    """Keep the released static/multiplied and explicit replacement graphs.

    Additional authored composition goes through the shared layer interpreter;
    recognized old profiles retain their existing graph and sampling behavior.
    """
    enabled=enabled_texture_layers(layers)
    if len(enabled)!=len(layers):return True
    for index,layer in enabled:
        kind=layer.get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')
        blend=layer.get('m_nTextureBlendMode','SPRITECARD_TEXTURE_BLEND_MULTIPLY')
        if layer.get('m_bReplaceTextureWithGradient'):return True
        if kind=='SPRITECARD_TEXTURE_UVDISTORTION':
            if (len(layers)!=2 or index!=1 or blend!='SPRITECARD_TEXTURE_BLEND_REPLACE'
                    or layers[0].get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')!='SPRITECARD_TEXTURE_COLOR'):
                return True
        elif kind!='SPRITECARD_TEXTURE_COLOR' or blend!='SPRITECARD_TEXTURE_BLEND_MULTIPLY':return True
        if layer.get('m_nTextureChannels','SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA') not in (
                'SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA','SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA_RGBALPHA'):
            return True
    return False


def build_layer_stack(nodes,links,layers,sample_layer,parameter,*,particle_input=None,motion_vectors=False,uv_y_sign=1):
    """sample_layer supplies (image node, UV socket); no enabled layer is dropped.

    Supported here: static color textures, cyclic zoom, replacing UV distortion
    (including zoomed distortion) of an immediately preceding color texture,
    and generated/external gradient ramps.
    Other modes, missing per-particle scale inputs and unknown enums fail.
    uv_y_sign=-1 is explicit Source top-left -> Blender bottom-left conversion
    for already placed native UVs; older adapters retain their original basis.
    """
    if type(uv_y_sign) not in (int,float) or uv_y_sign not in (-1,1):
        raise UnsupportedEffect('Invalid particle UV coordinate orientation')
    enabled=enabled_texture_layers(layers)
    graph=LayerNodes(nodes,links);op=graph.op
    accum=(1.,1.,1.,1.);previous=None
    for active_index,(index,layer) in enumerate(enabled):
        if layer.get('m_nTextureType')=='SPRITECARD_TEXTURE_ANIMMOTIONVEC':
            if not motion_vectors:raise UnsupportedEffect('Unsupported texture type SPRITECARD_TEXTURE_ANIMMOTIONVEC')
            continue # Independently prepared paired flow, never a color layer.
        controls=layer.get('m_TextureControls',{})
        if not isinstance(controls,dict):raise UnsupportedEffect('Invalid particle texture controls')
        scale=controls.get('m_nPerParticleScale','SPRITECARD_TEXTURE_PP_SCALE_NONE')
        if scale not in ('SPRITECARD_TEXTURE_PP_SCALE_NONE','SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA1','SPRITECARD_TEXTURE_PP_SCALE_SHADER_EXTRA_DATA2',*ROTATION_INPUTS):
            raise UnsupportedEffect('Unsupported per-particle texture input '+str(scale))
        kind=layer.get('m_nTextureType','SPRITECARD_TEXTURE_COLOR')
        zoom=kind in ('SPRITECARD_TEXTURE_ZOOM','SPRITECARD_TEXTURE_UVDISTORTION_ZOOM')
        distortion=kind in ('SPRITECARD_TEXTURE_UVDISTORTION','SPRITECARD_TEXTURE_UVDISTORTION_ZOOM')
        if kind not in ('SPRITECARD_TEXTURE_COLOR','SPRITECARD_TEXTURE_UVDISTORTION','SPRITECARD_TEXTURE_1D_COLOR_LOOKUP') and not zoom:
            raise UnsupportedEffect('Unsupported texture type '+str(kind))
        channel=layer.get('m_nTextureChannels','SPRITECARD_TEXTURE_CHANNEL_MIX_RGBA')
        blend=layer.get('m_nTextureBlendMode','SPRITECARD_TEXTURE_BLEND_MULTIPLY')
        if not isinstance(channel,str) or not isinstance(blend,str):
            raise UnsupportedEffect('Invalid particle texture mask or blend')
        channel=channel.removeprefix('SPRITECARD_TEXTURE_CHANNEL_MIX_')
        blend=blend.removeprefix('SPRITECARD_TEXTURE_BLEND_')
        # Validate enums before constructing potentially expensive image graphs.
        if channel not in CHANNELS or blend not in BLENDS:
            raise UnsupportedEffect('Unsupported particle texture mask or blend')
        image,uv=sample_layer(layer,index,raw=distortion)
        rgba=graph.rgba(image)
        if zoom:
            # TextureControls_t's native constructor supplies1, not the
            # reference viewer's zero, when the authored provider is absent.
            rate=parameter(controls.get('m_flZoomScale'),1)
            mode=controls.get('m_nPerParticleZoom','SPRITECARD_TEXTURE_PP_SCALE_NONE')
            if mode!='SPRITECARD_TEXTURE_PP_SCALE_NONE':
                if particle_input is None:raise UnsupportedEffect('Missing per-particle texture zoom input')
                rate=op('MULTIPLY',rate,particle_input(mode))
            rgba=graph.zoom(image,uv,rate)
        if distortion:
            if previous is None or previous[2]!='SPRITECARD_TEXTURE_COLOR':
                raise UnsupportedEffect('UV distortion requires a preceding color texture')
            source=getattr(previous[0],'distortion_source',previous[0])
            if getattr(source,'animated',False):
                raise UnsupportedEffect('Animated distortion requires its current-frame atlas sampler')
            strength=parameter(controls.get('m_flDistortion'),1)
            mode=controls.get('m_nPerParticleDistortion','SPRITECARD_TEXTURE_PP_SCALE_NONE')
            if mode!='SPRITECARD_TEXTURE_PP_SCALE_NONE':
                if particle_input is None:
                    raise UnsupportedEffect('Missing per-particle texture distortion input')
                strength=op('MULTIPLY',strength,particle_input(mode))
            factor=op('MULTIPLY',op('MULTIPLY',strength,-.25),rgba[3])
            offset=graph.vector([op('MULTIPLY',op('SUBTRACT',rgba[c],.5),
                factor if c==0 or uv_y_sign==1 else op('MULTIPLY',factor,-1)) for c in (0,1)]+[0])
            add=nodes.new('ShaderNodeVectorMath');add.operation='ADD'
            links.new(previous[1],add.inputs[0]);links.new(offset,add.inputs[1])
            rgba=graph.resample(source,add.outputs['Vector'])
        elif kind=='SPRITECARD_TEXTURE_1D_COLOR_LOOKUP':
            if getattr(image,'animated',False):
                raise UnsupportedEffect('Animated gradient ramps require separate qualification')
            coordinate,alpha_luma=gradient_source(rgba if active_index==0 else accum,channel,op)
            ramp=nodes.new('ShaderNodeTexImage');ramp.image=image.image;ramp.extension='EXTEND'
            links.new(graph.vector((coordinate,.5,0)),ramp.inputs['Vector'])
            ramp_rgba=graph.rgba(ramp)
            rgba=(*ramp_rgba[:3],luma(ramp_rgba[:3],LUMA_601,op) if alpha_luma else (rgba if active_index==0 else accum)[3])
        previous=image,uv,kind
        accum=composite(accum,rgba,channel,blend,parameter(layer.get('m_flTextureBlend'),1),op)
    return graph.vector(accum[:3]),accum[3]
