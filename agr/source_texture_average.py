"""Bounded VTEX v1 compiler average metadata, with full source identity.

Resource header12 and the DATA v1 float4 Reflectivity layout follow VRF's
Resource/Texture readers. Mapping this metadata into a shader expression is a
separate caller qualification; this parser never substitutes a guessed pivot.
"""
import math
import struct
from .asset_converter import ConversionError,sha
from .path_io import native_path


def read_texture_average(path):
    with open(native_path(path),'rb') as stream:
        raw=stream.read(16)
        if len(raw)!=16:raise ConversionError('Truncated texture resource header')
        size,header,version,directory,count=struct.unpack_from('<IHHII',raw)
        # Resource metadata is bounded independently of appended GPU mip data.
        # No pixel decode/allocation is needed to read the compiler average.
        if not 16<=size<=4*1024*1024:raise ConversionError('Texture metadata exceeds bound')
        raw+=stream.read(size-16)
    if len(raw)!=size:raise ConversionError('Truncated texture resource metadata')
    directory+=8
    if header!=12 or not 16<=size<=len(raw) or not 1<=count<=64:
        raise ConversionError('Unsupported texture resource header')
    if directory<16 or directory+12*count>size:
        raise ConversionError('Texture block directory exceeds resource')
    blocks=[]
    for index in range(count):
        at=directory+12*index;kind,relative,length=struct.unpack_from('<4sII',raw,at)
        start=at+4+relative
        if start<directory+12*count or start+length>size:
            raise ConversionError('Texture block exceeds resource')
        if kind==b'DATA':blocks.append((start,length))
    if len(blocks)!=1 or blocks[0][1]<40:
        raise ConversionError('Expected one complete texture DATA header')
    at,length=blocks[0]
    data_version,flags=struct.unpack_from('<HH',raw,at)
    if data_version!=1:raise ConversionError('Unsupported VTEX DATA version')
    average=struct.unpack_from('<4f',raw,at+4)
    if any(not math.isfinite(value) for value in average) or any(value<0 or value>1 for value in average[:3]):
        raise ConversionError('Invalid normalized albedo texture average')
    return list(average[:3])


def validate_texture_average(descriptor):
    if (not isinstance(descriptor,dict) or descriptor.get('schema_version')!=1
            or descriptor.get('mode')!='vtex_v1_compiler_average'
            or not isinstance(descriptor.get('resource'),str)
            or not descriptor['resource'].endswith('.vtex_c')):
        raise ConversionError('Authenticated albedo texture average required')
    if sha(descriptor['path'])!=descriptor['source_sha256']:
        raise ConversionError('Albedo texture average source changed')
    values=read_texture_average(descriptor['path'])
    if values!=descriptor['rgb']:raise ConversionError('Albedo texture average metadata changed')
    return values
