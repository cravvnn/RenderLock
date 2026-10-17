"""Explicit partial NPR material texture package; separate from probe coverage."""
import copy
from pathlib import Path
from .asset_converter import ConversionError,sha
from .source_hero_lighting_package import canonical_sha
from .source_npr_assets import PARAMETERS,png_dimensions

SHADER_RESOURCE='shaders/vfx/pbr_vulkan_50_ps.vcs'
SHADER_SHA256='13cc4576f69be8411f1b1400ea5c0bcedcb947c9406e24102cc6ccd7a33e41ef'
BINDINGS={'g_tNprTransmissiveColor':dict(colorspace='sRGB',alpha_mode='CHANNEL_PACKED',channel='RGB'),
          'g_tTintMaskRimLightMask':dict(colorspace='Non-Color',alpha_mode='CHANNEL_PACKED',channel='G',constant_only=True)}


def _index(rows):
    result={}
    for row in rows:
        name=row['material']
        if name in result:raise ConversionError('Duplicate NPR package material')
        result[name]=row
    if not 1<=len(result)<=64:raise ConversionError('Invalid NPR material inventory')
    return result


def build_npr_material_package(plan,decoded,provider,*,allow_partial=False):
    if allow_partial is not True:raise ConversionError('Explicit partial NPR selection required')
    if plan.get('status')!='sources_resolved_not_decoded' or decoded.get('status')!='decoded_requires_pixel_validation':
        raise ConversionError('Expected resolved and decoded NPR inputs')
    if provider.resolve(SHADER_RESOURCE).sha256!=SHADER_SHA256:
        raise ConversionError('NPR shader requires requalification')
    sources=_index(plan['materials']);images=_index(decoded['materials'])
    if sources.keys()!=images.keys():raise ConversionError('NPR package material coverage mismatch')
    rows=[]
    for name,source in sources.items():
        row=images[name]
        if source['source_sha256']!=row['source_sha256'] or provider.resolve(name).sha256!=source['source_sha256']:
            raise ConversionError('NPR material identity changed')
        if set(row['textures'])!=set(PARAMETERS):raise ConversionError('Incomplete NPR decoded texture inventory')
        for parameter in PARAMETERS:
            texture=row['textures'][parameter];original=source['textures'][parameter]
            if any(texture[k]!=original[k] for k in ('resource','source_sha256')):
                raise ConversionError('NPR decoded texture source mismatch')
            if provider.resolve(texture['resource']).sha256!=texture['source_sha256']:
                raise ConversionError('Installed NPR texture changed')
        rows.append(dict(material=name,source_sha256=row['source_sha256'],
                         textures=copy.deepcopy(row['textures']),authored=copy.deepcopy(source['authored'])))
    package=dict(schema_version=1,mode='partial_npr_material_inputs',allow_partial=True,
                 shader_sha256=SHADER_SHA256,bindings=copy.deepcopy(BINDINGS),materials=rows,
                 limits=['Pixel validation required before use; only constant rim masks supported.',
                         'No runtime attribute/permutation, direct lighting, depth, probe coverage or game parity qualification.'])
    package['content_sha256']=canonical_sha(package)
    validate_npr_material_package(package)
    return package


def validate_npr_material_package(package):
    if not isinstance(package,dict) or type(package.get('schema_version')) is not int or package.get('schema_version')!=1 or package.get('mode')!='partial_npr_material_inputs' or package.get('allow_partial') is not True:
        raise ConversionError('Unsupported or implicit NPR material package')
    if package.get('shader_sha256')!=SHADER_SHA256 or package.get('bindings')!=BINDINGS:
        raise ConversionError('Unqualified NPR shader/channel semantics')
    content={k:v for k,v in package.items() if k!='content_sha256'}
    if canonical_sha(content)!=package.get('content_sha256'):raise ConversionError('NPR package metadata changed')
    rows=_index(package['materials'])
    for row in rows.values():
        if set(row['textures'])!=set(PARAMETERS):raise ConversionError('Incomplete NPR package textures')
        for name,texture in row['textures'].items():
            path=Path(texture['path'])
            if sha(path)!=texture['sha256'] or png_dimensions(path)!=texture['size']:
                raise ConversionError('NPR decoded texture payload changed')
            if name=='g_tTintMaskRimLightMask' and texture['size']!=[1,1]:
                raise ConversionError('Variable rim mask needs a qualified texture sampling path')
    return rows
