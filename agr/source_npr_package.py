"""Explicit partial NPR material texture package; separate from probe coverage."""
import copy
from pathlib import Path
from .asset_converter import ConversionError,sha
from .source_hero_lighting_package import canonical_sha
from .source_npr_assets import PARAMETERS,png_dimensions
from .source_material_routes import source_material_contract,NPR_ROUTE,material_route

SHADER_RESOURCE='shaders/vfx/pbr_vulkan_50_ps.vcs'
SHADER_SHA256='13cc4576f69be8411f1b1400ea5c0bcedcb947c9406e24102cc6ccd7a33e41ef'
LEGACY_BINDINGS={'g_tNprTransmissiveColor':dict(colorspace='sRGB',alpha_mode='CHANNEL_PACKED',channel='RGB'),
          'g_tTintMaskRimLightMask':dict(colorspace='Non-Color',alpha_mode='CHANNEL_PACKED',channel='G',constant_only=True)}
BINDINGS=copy.deepcopy(LEGACY_BINDINGS)
BINDINGS['g_tTintMaskRimLightMask'].update(constant_only=False,tint_channel='R',uv='albedo')


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
        contract=source_material_contract(source['authored'],resolve_constants='expression_evaluation' in source) if plan.get('schema_version')==3 else None
        if contract is not None and any(source.get(key)!=value for key,value in contract.items()):
            raise ConversionError('Source material route contract differs from authored metadata')
        expected=set(PARAMETERS) if contract is None or contract['route']==NPR_ROUTE else set()
        if set(row['textures'])!=expected or set(source['textures'])!=expected:
            raise ConversionError('Incomplete typed decoded texture inventory')
        for parameter in expected:
            texture=row['textures'][parameter];original=source['textures'][parameter]
            if any(texture[k]!=original[k] for k in ('resource','source_sha256')):
                raise ConversionError('NPR decoded texture source mismatch')
            if provider.resolve(texture['resource']).sha256!=texture['source_sha256']:
                raise ConversionError('Installed NPR texture changed')
        material=dict(material=name,source_sha256=row['source_sha256'],
                      textures=copy.deepcopy(row['textures']),authored=copy.deepcopy(source['authored']))
        if contract is not None:material.update(copy.deepcopy(contract))
        if contract and contract.get('expression_evaluation',{}).get('vector_overrides'):
            from .material_expressions import verify_expression_module
            verify_expression_module(provider)
        if 'albedo_average' in source:
            from .source_texture_average import validate_texture_average
            average=source['albedo_average'];validate_texture_average(average)
            if provider.resolve(average['resource']).sha256!=average['source_sha256']:
                raise ConversionError('Authored contrast texture source changed')
            material['albedo_average']=copy.deepcopy(average)
        rows.append(material)
    package=dict(schema_version=3 if plan.get('schema_version')==3 else 2,
                 mode='partial_source_material_inputs' if plan.get('schema_version')==3 else 'partial_npr_material_inputs',allow_partial=True,
                 shader_sha256=SHADER_SHA256,bindings=copy.deepcopy(BINDINGS),materials=rows,
                 limits=['Pixel validation required before use; red tint and green rim use the authenticated albedo UV.',
                         'Linear repeat sampling; GPU anisotropic filtering/minification equivalence is not certified.',
                         'No runtime attribute/permutation, direct lighting, depth, probe coverage or game parity qualification.'])
    package['content_sha256']=canonical_sha(package)
    validate_npr_material_package(package)
    return package


def validate_npr_material_package(package):
    if not isinstance(package,dict) or type(package.get('schema_version')) is not int or package.get('schema_version') not in (1,2,3) or package.get('mode')!=('partial_source_material_inputs' if package.get('schema_version')==3 else 'partial_npr_material_inputs') or package.get('allow_partial') is not True:
        raise ConversionError('Unsupported or implicit NPR material package')
    expected=LEGACY_BINDINGS if package['schema_version']==1 else BINDINGS
    if package.get('shader_sha256')!=SHADER_SHA256 or package.get('bindings')!=expected:
        raise ConversionError('Unqualified NPR shader/channel semantics')
    content={k:v for k,v in package.items() if k!='content_sha256'}
    if canonical_sha(content)!=package.get('content_sha256'):raise ConversionError('NPR package metadata changed')
    rows=_index(package['materials'])
    for row in rows.values():
        if package['schema_version']==3:
            contract=source_material_contract(row['authored'],resolve_constants='expression_evaluation' in row)
            if any(row.get(key)!=value for key,value in contract.items()):
                raise ConversionError('Material route/runtime contract changed')
        if 'albedo_average' in row:
            from .source_texture_average import validate_texture_average
            validate_texture_average(row['albedo_average'])
        if package['schema_version']<3 and material_route(row)!=NPR_ROUTE:
            raise ConversionError('Legacy NPR package cannot declare generic material routes')
        expected=set(PARAMETERS) if material_route(row)==NPR_ROUTE else set()
        if set(row['textures'])!=expected:raise ConversionError('Incomplete typed material package textures')
        for name,texture in row['textures'].items():
            path=Path(texture['path'])
            if sha(path)!=texture['sha256'] or png_dimensions(path)!=texture['size']:
                raise ConversionError('NPR decoded texture payload changed')
            if package['schema_version']==1 and name=='g_tTintMaskRimLightMask' and texture['size']!=[1,1]:
                raise ConversionError('Variable rim mask needs a qualified texture sampling path')
    return rows
