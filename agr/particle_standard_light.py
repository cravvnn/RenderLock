"""Default Source point/FX light arithmetic, with an explicit Blender adaptation.

Native point inputs and evaluated outputs were compared on one coherent replay
cohort. FX's linear override is separately bound to its typed native branch.
Analytic sRGB decoding approximates the native byte lookup (max absolute lookup
error 4e-7); no game-private table is distributed. Source photometry, submitted
pixels, BRDF/fog and fractional render-clock equivalence remain unqualified.
"""
import math
import struct
from .installed_effects import UnsupportedEffect
from .particle_render_controls import render_scalar

POLICY='agr_default_particle_light_v1'
SCOPE=('Native default point/FX light color, intensity and finite falloff rules; '
       'analytic color lookup and Blender photometry/shading remain approximate.')
LOOKUP_ABSOLUTE_ERROR=4e-7
FALLOFF_EPSILON=2**-23
PROFILE_KEY='native_standard_light'
PROFILE_POLICY='agr_native_default_point_fx_profile_v1'
PARTICLES_SHA='46ae70beaef7935e27794d2148406407605994b59fa5ff19f9a37c8ba68c129f'


def validate_native_inputs(proof):
    if proof!={'policy':PROFILE_POLICY,'particles_sha256':PARTICLES_SHA}:
        raise UnsupportedEffect('Particle light: unqualified native default point/FX profile')
    return proof


def collect_native_inputs(provider):
    from pathlib import Path
    from .asset_converter import sha
    path=Path(provider.install_path)/'game/bin/win64/particles.dll'
    if not path.is_file():
        raise UnsupportedEffect('Particle light: qualified installed Windows particle module is unavailable')
    return validate_native_inputs(dict(policy=PROFILE_POLICY,particles_sha256=sha(path)))


def _require(value,message):
    if not value:raise UnsupportedEffect('Particle light: '+message)


def _f32(value):
    try:return struct.unpack('<f',struct.pack('<f',value))[0]
    except OverflowError as error:raise UnsupportedEffect('Particle light: value outside finite float32 profile')from error


def profile(renderer):
    """Refuse unimplemented light shapes/settings before allocating geometry."""
    allowed={'_class','m_nLightType','m_flIntensity','m_flRadiusMultiplier','m_bCastShadows',
             'm_nAttenuationStyle','m_flFalloffLinearity','m_vecColorScale',
             'm_bRenderDiffuse','m_bRenderSpecular','m_bDynamicBounce'}
    _require(type(renderer)is dict and renderer.get('_class')=='C_OP_RenderStandardLight','renderer class changed')
    _require(not set(renderer)-allowed,'unimplemented settings: '+', '.join(sorted(set(renderer)-allowed)))
    for key in ('m_flIntensity','m_flRadiusMultiplier'):
        _require(key not in renderer or renderer[key] is not None,'invalid null '+key)
    kind=renderer.get('m_nLightType',0)
    _require(type(kind)in(int,str),'invalid light type')
    _require(kind in(0,2,'PARTICLE_LIGHT_TYPE_POINT','PARTICLE_LIGHT_TYPE_FX'),'unimplemented light shape '+str(kind))
    style=renderer.get('m_nAttenuationStyle',0)
    _require(type(style)is int and style==0,'unimplemented attenuation style')
    linearity=renderer.get('m_flFalloffLinearity')
    _require('m_flFalloffLinearity'not in renderer or type(linearity)in(int,float)and linearity==0,'unimplemented authored falloff linearity')
    _require('m_vecColorScale'not in renderer or renderer['m_vecColorScale']=={},'unimplemented color-scale provider')
    for key,default in (('m_bRenderDiffuse',True),('m_bRenderSpecular',True),('m_bDynamicBounce',False),('m_bCastShadows',False)):
        _require(type(renderer.get(key,default))is bool,'invalid '+key)
    _require(renderer.get('m_bRenderDiffuse',True)and renderer.get('m_bRenderSpecular',True)
             and not renderer.get('m_bDynamicBounce',False),'unimplemented diffuse/specular/bounce override')
    fx=kind in(2,'PARTICLE_LIGHT_TYPE_FX')
    return dict(attenuation='linear'if fx else'quadratic',casts_shadows=False if fx else renderer.get('m_bCastShadows',False))


def decode_color(color):
    _require(type(color)in(list,tuple)and len(color)==3,'color vector required')
    result=[]
    for value in color:
        _require(type(value)in(int,float)and math.isfinite(value)and 0<=value<=1,'color outside normalized native byte profile')
        byte=min(255,max(0,int(_f32(_f32(value)*255.))))
        v=byte/255.
        result.append(_f32(v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4))
    return result


def parameters(renderer,particle,unit_scale,*,context=None):
    """Evaluate one sampled state; no allocations, caching or clock ownership."""
    config=profile(renderer)
    _require(type(unit_scale)in(int,float)and math.isfinite(unit_scale)and 1e-9<=unit_scale<=1,'explicit scene unit scale required')
    intensity=render_scalar(renderer.get('m_flIntensity'),default=1,context=context)
    multiplier=render_scalar(renderer.get('m_flRadiusMultiplier'),default=8,context=context)
    alpha=particle.get('alpha');radius=particle.get('radius')
    _require(all(type(v)in(int,float)and math.isfinite(v)and 0<=v<=1e6 for v in (intensity,multiplier,alpha,radius)),
             'intensity, radius, multiplier or field7 alpha outside bounds')
    intensity,alpha=_f32(intensity),_f32(alpha)
    linear=decode_color(particle.get('color'))
    normalization=100. if config['attenuation']=='linear'else 10000.
    rgb=[_f32(_f32(_f32(v*intensity)*alpha)*normalization)for v in linear]
    _require(max(rgb)<=1e12,'evaluated RGB outside descriptor bound')
    authored_range=_f32(_f32(radius)*_f32(multiplier))
    _require(authored_range<=1e12,'authored range outside bound')
    luminance=_f32(_f32(_f32(rgb[1]*_f32(.7152))+_f32(rgb[0]*_f32(.2126)))+_f32(rgb[2]*_f32(.0722)))
    effective=0.
    if luminance>0:
        threshold=_f32(_f32(.001)/luminance)
        if config['attenuation']=='linear':
            solved=_f32(1./threshold)
        else:
            solved=_f32(_f32(math.sqrt(_f32(4.*threshold)))/_f32(2.*threshold))
        # Native final processing treats nonpositive authored range as an
        # automatic luminance-limited range, rather than a zero-sized light.
        effective=min(authored_range,solved)if authored_range>0 else solved
    power_distance=_f32(effective*effective)if config['attenuation']=='quadratic'else effective
    offset=_f32(1./_f32(power_distance+FALLOFF_EPSILON))if power_distance>0 else 0.
    peak=max(rgb);chroma=rgb if peak else linear;chroma_peak=max(chroma)
    return dict(color=[v/chroma_peak for v in chroma]if chroma_peak else[0.,0.,0.],
                energy=4.*math.pi*peak*unit_scale**2,range=effective*unit_scale,
                source_rgb=rgb,source_range=effective,falloff_offset=offset,unit_scale=unit_scale,
                attenuation=config['attenuation'],casts_shadows=config['casts_shadows'],policy=POLICY)
