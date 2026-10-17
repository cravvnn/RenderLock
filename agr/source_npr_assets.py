"""Resolve NPR material inputs from an exported model and installed resources.

This produces a source plan, not decoded textures or a qualified shader package.
Every used material is accounted for; unsupported materials fail explicitly.
"""
from .asset_converter import ConversionError, compiled
from .game_assets import safe_resource_path
from .map_data import dump_map_data
from pathlib import Path
import struct
import subprocess
from .asset_converter import sha
from .source_lightmap_assets import QUALIFIED_DECODER_PAIRS

PARAMETERS=('g_tNprTransmissiveColor','g_tTintMaskRimLightMask')


def png_dimensions(path):
    with Path(path).open('rb') as stream:header=stream.read(33)
    if len(header)!=33 or header[:8]!=b'\x89PNG\r\n\x1a\n' or header[8:16]!=b'\x00\x00\x00\rIHDR':
        raise ConversionError('Expected decoded PNG with IHDR')
    width,height=struct.unpack('>II',header[16:24])
    if not 1<=width<=4096 or not 1<=height<=4096:
        raise ConversionError('NPR decoded texture exceeds supported dimensions')
    return [width,height]


def decode_npr_material_assets(plan,provider,decoder):
    """Content-verified conversion; decoded PNGs still require pixel validation."""
    if plan.get('status')!='sources_resolved_not_decoded' or not 1<=len(plan.get('materials',[]))<=64:
        raise ConversionError('Expected bounded NPR source plan')
    if Path(decoder.root).resolve().drive.upper()!='C:':
        raise ConversionError('NPR generated texture cache must be on C:')
    identity=(sha(decoder.executable),decoder.identity.get('companions',{}).get('libSkiaSharp.dll'))
    if identity not in QUALIFIED_DECODER_PAIRS:
        raise ConversionError('NPR texture decoder requires requalification')
    sources={}
    for material in plan['materials']:
        if provider.resolve(material['material']).sha256!=material['source_sha256']:
            raise ConversionError('NPR material source changed since planning')
        if set(material['textures'])!=set(PARAMETERS):raise ConversionError('Incomplete NPR texture plan')
        for row in material['textures'].values():
            resource=row['resource'];resolved=provider.resolve(resource)
            if resolved.sha256!=row['source_sha256']:raise ConversionError('NPR texture source changed since planning')
            if resolved.local_path.stat().st_size>16*1024*1024:raise ConversionError('NPR source texture exceeds decode bound')
            sources[resource]=resolved
    decoded={}
    for resource,resolved in sorted(sources.items()):
        def build(temp):
            # Source 2 Viewer 20.1 writes texture dumps next to a file-style
            # -o target using the resource name; an existing directory target
            # is combined with the absolute input path instead.
            out_dir=temp/'decoded';out_dir.mkdir(parents=True,exist_ok=True)
            target=out_dir/(Path(resolved.local_path).stem+'.png')
            proc=subprocess.run([str(decoder.executable),'-i',str(resolved.local_path),'-o',str(target),
                                 '-d','--texture_decode_flags','none'],capture_output=True,timeout=120)
            if proc.returncode:raise ConversionError('NPR texture decode failed: '+proc.stderr[-2000:].decode('utf-8',errors='replace'))
            files=list(out_dir.rglob('*.png'))
            if len(files)!=1:raise ConversionError('Expected exactly one decoded NPR PNG')
            png_dimensions(files[0])
        directory=decoder._entry('source_npr_texture_none_v1',{resource:resolved.sha256},
                                 {'adapter_sha256':sha(Path(__file__))},build)
        files=list((directory/'decoded').rglob('*.png'))
        if len(files)!=1:raise ConversionError('NPR cached PNG inventory changed')
        path=files[0]
        decoded[resource]=dict(resource=resource,source_sha256=resolved.sha256,path=str(path),sha256=sha(path),size=png_dimensions(path))
    return dict(schema_version=1,status='decoded_requires_pixel_validation',
                materials=[dict(material=r['material'],source_sha256=r['source_sha256'],
                    textures={k:dict(decoded[v['resource']]) for k,v in r['textures'].items()}) for r in plan['materials']],
                limits=['PNG identities/dimensions checked; pixel values, color space and shader channel semantics require validation.',
                        'No shader package or lighting coverage claim.'])


def plan_npr_material_assets(document,provider,decoder):
    materials=document.get('materials',[])
    used=set()
    for mesh in document.get('meshes',[]):
        for primitive in mesh.get('primitives',[]):
            index=primitive.get('material')
            if type(index) is not int or not 0<=index<len(materials):
                raise ConversionError('NPR primitive lacks valid material identity')
            used.add(index)
    if not used or len(used)>64:raise ConversionError('Expected 1..64 used NPR materials')
    rows=[];seen=set()
    for index in sorted(used):
        vmat=materials[index].get('extras',{}).get('vmat',{})
        name=vmat.get('Name')
        if not isinstance(name,str) or not name.endswith('.vmat'):
            raise ConversionError('Converted material lacks full VMAT resource identity')
        resource=safe_resource_path(compiled(name))
        if resource in seen:raise ConversionError('Duplicate Source material identity in model')
        seen.add(resource)
        resolved=provider.resolve(resource)
        source=dump_map_data(decoder,resource,provider)
        flags={r['m_name']:r['m_nValue'] for r in source.get('m_intParams',[])}
        if source.get('m_shaderName')!='pbr.vfx' or flags.get('F_USE_NPR_LIGHTING')!=1:
            raise ConversionError('Used material is not supported NPR pbr: '+resource)
        if vmat.get('ShaderName')!='pbr.vfx' or vmat.get('IntParams',{}).get('F_USE_NPR_LIGHTING')!=1:
            raise ConversionError('Converted NPR metadata differs from installed material')
        textures={r['m_name']:r['m_pValue'] for r in source.get('m_textureParams',[])}
        selected={}
        for parameter in PARAMETERS:
            name=textures.get(parameter)
            if not isinstance(name,str) or not name.endswith('.vtex'):
                raise ConversionError('Missing installed NPR texture: '+parameter)
            texture=safe_resource_path(compiled(name))
            converted=vmat.get('TextureParams',{}).get(parameter)
            if not isinstance(converted,str) or safe_resource_path(compiled(converted))!=texture:
                raise ConversionError('Converted texture identity differs: '+parameter)
            payload=provider.resolve(texture)
            selected[parameter]=dict(resource=texture,source_sha256=payload.sha256,path=str(payload.local_path))
        rows.append(dict(material=resource,material_index=index,source_sha256=resolved.sha256,
                         textures=selected,authored=source))
    return dict(schema_version=1,status='sources_resolved_not_decoded',materials=rows,
                limits=['Source identities only; texture decoding and sampling semantics require validation.',
                        'Model conversion provenance, runtime overrides and lighting coverage are separate contracts.'])
