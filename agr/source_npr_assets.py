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
from .path_io import native_path
from .source_material_routes import source_material_contract,NPR_ROUTE

PARAMETERS=('g_tNprTransmissiveColor','g_tTintMaskRimLightMask')


def validate_npr_decode_source(path):
    """Bound 2D output before decoding, including complete4K BC7 mip chains.

    The former16MiB whole-file cap excluded valid4096-square BC7 textures,
    whose appended mip chain alone is about21.3MiB. Only that typed extension
    is accepted; other formats retain the former source-file budget.
    """
    size=Path(path).stat().st_size
    if not 16<=size<=32*1024*1024:raise ConversionError('NPR source texture exceeds decode bound')
    with open(native_path(path),'rb') as stream:
        header=stream.read(16)
        metadata_size,version,_,directory,count=struct.unpack('<IHHII',header)
        if version!=12 or not 28<=metadata_size<=min(size,4*1024*1024) or not 1<=count<=64:
            raise ConversionError('Unsupported bounded NPR texture resource header')
        metadata=header+stream.read(metadata_size-16)
    if len(metadata)!=metadata_size:raise ConversionError('Truncated NPR texture metadata')
    directory+=8
    if directory<16 or directory+12*count>metadata_size:
        raise ConversionError('NPR texture block directory exceeds metadata')
    blocks=[]
    for index in range(count):
        at=directory+12*index;kind,relative,length=struct.unpack_from('<4sII',metadata,at)
        start=at+4+relative
        if start<directory+12*count or start+length>metadata_size:
            raise ConversionError('NPR texture block exceeds metadata')
        if kind==b'DATA':blocks.append((start,length))
    if len(blocks)!=1 or blocks[0][1]<40:
        raise ConversionError('Expected one complete NPR texture DATA header')
    start,data_length=blocks[0];data_version,flags=struct.unpack_from('<HH',metadata,start)
    width,height,depth,fmt,mips=struct.unpack_from('<HHHBB',metadata,start+20)
    # Cube/volume/array textures cannot become this adapter's single2D PNG.
    if (data_version!=1 or not 1<=width<=4096 or not 1<=height<=4096
            or depth!=1 or flags & (16|32|64)
            or not 1<=mips<=max(width,height).bit_length()):
        raise ConversionError('NPR texture requires bounded2D DATA1 dimensions/mips')
    storage_width,storage_height=width,height
    extra_offset,extra_count=struct.unpack_from('<II',metadata,start+32)
    if extra_count:
        extra=start+32+extra_offset
        if extra_count>64 or extra<start+40 or extra+12*extra_count>start+data_length:
            raise ConversionError('NPR texture extra directory exceeds DATA')
        crop=None
        for index in range(extra_count):
            at=extra+12*index;kind,relative,length=struct.unpack_from('<III',metadata,at)
            payload=at+4+relative
            if payload<extra+12*extra_count or payload+length>start+data_length:
                raise ConversionError('NPR texture extra payload exceeds DATA')
            if kind==3:  # VRF METADATA: authored display rectangle.
                if crop is not None or length<8:raise ConversionError('Invalid NPR original texture extent')
                original=struct.unpack_from('<HH',metadata,payload+2)
                # Source image extents can exceed a downsampled compiled image.
                # VRF ignores that rectangle. It also ignores spurious1 extents
                # except for a minimum4-texel compression block.
                crop=(width,height)
                if 1<=original[0]<=width and 1<=original[1]<=height:
                    crop=tuple(value if value!=1 or stored==4 else stored
                               for value,stored in zip(original,(width,height)))
        if crop is not None:width,height=crop
    decoded_bytes=width*height*4*4
    if decoded_bytes>256*1024*1024:raise ConversionError('NPR decoded float pixels exceed bound')
    if size>16*1024*1024:
        expected=sum(max(1,((storage_width>>level)+3)//4)*max(1,((storage_height>>level)+3)//4)*16
                     for level in range(mips))
        if fmt!=20 or size!=metadata_size+expected:
            raise ConversionError('Extended NPR source requires exact bounded BC7 mip payload')
    return dict(width=width,height=height,depth=depth,format=fmt,mips=mips,
                stored_size=[storage_width,storage_height],source_bytes=size,decoded_float_bytes=decoded_bytes)


def png_dimensions(path):
    with open(native_path(path),'rb') as stream:header=stream.read(33)
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
    identity=(sha(decoder.executable),decoder.identity.get('companions',{}).get('libSkiaSharp.dll'))
    if identity not in QUALIFIED_DECODER_PAIRS:
        raise ConversionError('NPR texture decoder requires requalification')
    sources={};layouts={}
    for material in plan['materials']:
        if provider.resolve(material['material']).sha256!=material['source_sha256']:
            raise ConversionError('NPR material source changed since planning')
        expected=set(PARAMETERS) if material.get('route',NPR_ROUTE)==NPR_ROUTE else set()
        if set(material['textures'])!=expected:raise ConversionError('Incomplete typed material texture plan')
        for row in material['textures'].values():
            resource=row['resource'];resolved=provider.resolve(resource)
            if resolved.sha256!=row['source_sha256']:raise ConversionError('NPR texture source changed since planning')
            layouts[resource]=validate_npr_decode_source(resolved.local_path)
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
            if png_dimensions(files[0])!=[layouts[resource]['width'],layouts[resource]['height']]:
                raise ConversionError('NPR decoded/source texture dimensions differ: '+resource)
        directory=decoder._entry('source_npr_texture_none_v1',{resource:resolved.sha256},
                                 {'adapter_sha256':sha(Path(__file__))},build)
        files=list((directory/'decoded').rglob('*.png'))
        if len(files)!=1:raise ConversionError('NPR cached PNG inventory changed')
        path=files[0]
        if png_dimensions(path)!=[layouts[resource]['width'],layouts[resource]['height']]:
            raise ConversionError('NPR cached/source texture dimensions differ: '+resource)
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
        contract=source_material_contract(source,resolve_constants=True)
        expression=contract.get('expression_evaluation')
        if expression and expression['vector_overrides']:
            from .material_expressions import verify_expression_module
            verify_expression_module(provider)
        if (vmat.get('ShaderName')!='pbr.vfx' or any(vmat.get('IntParams',{}).get(key,0)!=flags.get(key,0)
                for key in set(flags)|set(vmat.get('IntParams',{})) if key.startswith('F_'))):
            raise ConversionError('Converted material feature metadata differs from installed material')
        textures={r['m_name']:r['m_pValue'] for r in source.get('m_textureParams',[])}
        selected={}
        for parameter in PARAMETERS if contract['route']==NPR_ROUTE else ():
            name=textures.get(parameter)
            if not isinstance(name,str) or not name.endswith('.vtex'):
                raise ConversionError('Missing installed NPR texture: '+parameter)
            texture=safe_resource_path(compiled(name))
            converted=vmat.get('TextureParams',{}).get(parameter)
            if not isinstance(converted,str) or safe_resource_path(compiled(converted))!=texture:
                raise ConversionError('Converted texture identity differs: '+parameter)
            payload=provider.resolve(texture)
            selected[parameter]=dict(resource=texture,source_sha256=payload.sha256,path=str(payload.local_path))
        row=dict(material=resource,material_index=index,source_sha256=resolved.sha256,
                 textures=selected,authored=source,**contract)
        vectors={item['m_name']:item['m_value'] for item in source.get('m_vectorParams',[])}
        if expression:vectors.update(expression['vector_overrides'])
        csb=vectors.get('g_vAlbedoContrastSaturationBrightness1',[1,1,1,0])
        if contract['route']==NPR_ROUTE and isinstance(csb,(list,tuple)) and len(csb)==4 and csb[0]!=1:
            from .source_texture_average import read_texture_average
            color=textures.get('g_tColor')
            if not isinstance(color,str) or not color.endswith('.vtex'):
                raise ConversionError('Authored contrast requires original color texture')
            color=safe_resource_path(compiled(color))
            if safe_resource_path(compiled(vmat.get('TextureParams',{}).get('g_tColor','')))!=color:
                raise ConversionError('Authored contrast color identity differs')
            original=provider.resolve(color)
            row['albedo_average']=dict(schema_version=1,mode='vtex_v1_compiler_average',
                resource=color,source_sha256=original.sha256,path=str(original.local_path),
                rgb=read_texture_average(original.local_path))
        rows.append(row)
    return dict(schema_version=3,status='sources_resolved_not_decoded',materials=rows,
                limits=['Source identities only; texture decoding and sampling semantics require validation.',
                        'Model conversion provenance, runtime overrides and lighting coverage are separate contracts.'])
