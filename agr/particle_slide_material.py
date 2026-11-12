"""Numeric UV and opacity factors from the qualified slide pixel-shader variant.

PS static16960/dynamic24576/file2 is selected by both current slide models.
These are arithmetic leaves, not renderer admission or Source lighting/GPU
equivalence. Callers must supply current evaluated material properties, derived
transforms and the qualified render GameTime explicitly; no Age/tick fallback.
"""
import math
import struct
from .particle_material_expressions import ParticleExpressionRefused

PIXEL_SHADER_SHA256='bcceb3d4194f9614efe24efbedee02d81be537a633d7a447e06088758f972142'
VERTEX_SHADER_SHA256='c59ef8fe60ef137ddeb8745ff96bb9b4f42f24aa69014eb0373236e40649c105'

def _f32(value):
    if type(value)not in(int,float):
        raise ParticleExpressionRefused('Finite slide material numeric input required')
    try:finite=math.isfinite(value)
    except OverflowError as error:raise ParticleExpressionRefused('Slide material float32 overflow')from error
    if not finite:raise ParticleExpressionRefused('Finite slide material numeric input required')
    try:result=struct.unpack('<f',struct.pack('<f',value))[0]
    except (OverflowError,struct.error)as error:
        raise ParticleExpressionRefused('Slide material float32 overflow')from error
    if not math.isfinite(result):raise ParticleExpressionRefused('Slide material float32 overflow')
    return result

def _vector(value,n):
    if type(value)not in(list,tuple)or len(value)!=n:
        raise ParticleExpressionRefused('Complete slide material vector required')
    return tuple(_f32(v)for v in value)

def _qualified(shader_sha256):
    if shader_sha256!=PIXEL_SHADER_SHA256:
        raise ParticleExpressionRefused('Unqualified slide pixel shader')

def slide_texture_uv(uv,game_time,scroll,quantize,xform,offset,*,shader_sha256):
    """FRC(scroll*quantized GameTime) BEFORE the captured affine transform.

UV must already be the selected primary/secondary coordinate from the vertex
shader. xform is the evaluated four-lane Source matrix, not Blender Euler data.
ROUND_NE is nearest/ties-to-even. Float32 arithmetic is a CPU reference; GPU
dot-product contraction/precision and texture filtering are separate concerns.
"""
    _qualified(shader_sha256)
    uv=_vector(uv,2);scroll=_vector(scroll,2);xform=_vector(xform,4);offset=_vector(offset,2)
    time=_f32(game_time);quantize=_f32(quantize)
    if quantize!=0:
        time=_f32(_f32(round(_f32(time/quantize),0))*quantize)
    translated=[]
    for point,speed in zip(uv,scroll):
        product=_f32(time*speed)
        fraction=_f32(product-math.floor(product))
        translated.append(_f32(point+fraction))
    return tuple(_f32(_f32(_f32(xform[i]*translated[0])+_f32(xform[i+1]*translated[1]))+offset[i//2])for i in(0,2))

def slide_vertex_uv(uv,model_transform,primary_enabled,primary_u,primary_v,primary_origin,
                    secondary_enabled,secondary_u,secondary_v,secondary_origin,*,shader_sha256):
    """Actual VS0/dynamic4 UV scaling by Source-unit model basis lengths.

model_transform is the exact row-major3x4 draw transform, before Blender unit
conversion. Each derived U/V four-vector dots (axis lengths,1). Each negative
input UV component remains untouched; the two outputs both start from the SAME
mesh UV pair. The secondary output is not a second mesh UV layer. No transform,
vertex-source ownership or renderer support is established by this arithmetic.
"""
    if shader_sha256!=VERTEX_SHADER_SHA256:
        raise ParticleExpressionRefused('Unqualified slide vertex shader')
    if type(primary_enabled)is not bool or type(secondary_enabled)is not bool:
        raise ParticleExpressionRefused('Explicit slide model UV scaling flags required')
    uv=_vector(uv,2);matrix=_vector(model_transform,12)
    parameters=[(_vector(primary_u,4),_vector(primary_v,4),_vector(primary_origin,2),primary_enabled),
        (_vector(secondary_u,4),_vector(secondary_v,4),_vector(secondary_origin,2),secondary_enabled)]
    axes=[]
    for column in range(3):
        squared=[_f32(matrix[row*4+column]*matrix[row*4+column])for row in range(3)]
        axes.append(_f32(math.sqrt(_f32(_f32(squared[0]+squared[1])+squared[2]))))
    axes.append(1.)
    result=[]
    for u,v,origin,enabled in parameters:
        scales=[]
        for weights in(u,v):
            products=[_f32(a*b)for a,b in zip(axes,weights)]
            scales.append(_f32(_f32(_f32(products[0]+products[1])+products[2])+products[3]))
        result.append(tuple(_f32(_f32(_f32(point-center)*scale)+center)if enabled and point>=0 else point
            for point,center,scale in zip(uv,origin,scales)))
    return tuple(result)

def slide_opacity_factors(mask_alpha,alt_red,particle_alpha,opacity_scale,
                          view_normal_dot,angle_power,angle_scale_bias,*,blend_mode,shader_sha256):
    """Two sampled masks, facing factor, particle alpha and opacity scale.

The selected shader samples Color ALPHA and AltTranslucency RED. Modes0/1/2
are multiply/add-saturate/subtract-saturate. Facing scale/bias are the evaluated
native pair, with max(.001, scale*abs(dot)+bias)**abs(power); zero power bypasses
that factor. This stops before lighting/fog/refraction/compositing. Log/exp GPU
precision is not reproduced bit-for-bit. No camera/normal ownership is inferred.
"""
    _qualified(shader_sha256)
    if type(blend_mode)is not int or blend_mode not in(0,1,2):
        raise ParticleExpressionRefused('Unqualified slide translucency blend mode')
    a,b,alpha,scale,dot,power=map(_f32,(mask_alpha,alt_red,particle_alpha,opacity_scale,view_normal_dot,angle_power))
    angle_scale,angle_bias=_vector(angle_scale_bias,2)
    if not(0<=a<=1 and 0<=b<=1 and 0<=alpha<=1 and scale>=0):
        raise ParticleExpressionRefused('Slide mask/alpha/opacity range invalid')
    mask=_f32(a*b)if blend_mode==0 else min(1.,max(0.,_f32(a+b if blend_mode==1 else a-b)))
    if power!=0:
        facing=max(_f32(.001),_f32(_f32(angle_scale*abs(dot))+angle_bias))
        try:factor=_f32(math.pow(facing,abs(power)))
        except OverflowError as error:raise ParticleExpressionRefused('Slide facing factor overflow')from error
        mask=_f32(mask*factor)
    return _f32(_f32(mask*alpha)*scale)
