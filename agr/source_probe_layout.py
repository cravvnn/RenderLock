"""Installed light-probe layout from the atlas/cubemap VTEX headers.

The old layout table was keyed by the world resource hash. Map content changes
on every game update even when the probe texture layout is unchanged, so the
authoritative dimensions are read from the installed atlas and cubemap array
instead. Probe records still have to fit those bounds, and the recorded shader
identity stays in the plan for provenance.
"""
import struct

from .asset_converter import ConversionError
from .path_io import native_path

ATLAS_FORMAT = 19
CUBEMAP_FORMAT = 19
ATLAS_MIPS = 1
CUBEMAP_MIPS = 7
CUBEMAP_SIZE = 256
MAX_DIMENSION = 8192
MAX_CUBE_DEPTH = 4096


def read_texture_layout(path):
    """Read only the VTEX v1 metadata header; no pixel decode or allocation."""
    with open(native_path(path), 'rb') as stream:
        raw = stream.read(16)
        if len(raw) != 16:
            raise ConversionError('Truncated texture resource header')
        size, header, version, directory, count = struct.unpack_from('<IHHII', raw)
        if not 16 <= size <= 4 * 1024 * 1024:
            raise ConversionError('Texture metadata exceeds bound')
        raw += stream.read(size - 16)
    if len(raw) != size:
        raise ConversionError('Truncated texture resource metadata')
    if header != 12 or version != 1:
        raise ConversionError('Unsupported texture resource header')
    directory += 8
    if not 1 <= count <= 64 or directory < 16 or directory + 12 * count > size:
        raise ConversionError('Texture block directory exceeds resource')
    block = None
    for index in range(count):
        at = directory + 12 * index
        kind, relative, length = struct.unpack_from('<4sII', raw, at)
        start = at + 4 + relative
        if start < directory + 12 * count or start + length > size:
            raise ConversionError('Texture block exceeds resource')
        if kind == b'DATA':
            if block is not None:
                raise ConversionError('Expected one texture DATA block')
            block = (start, length)
    if block is None or block[1] < 28:
        raise ConversionError('Expected a complete texture DATA header')
    at, length = block
    data_version, _flags = struct.unpack_from('<HH', raw, at)
    if data_version != 1:
        raise ConversionError('Unsupported VTEX DATA version')
    width, height, depth = struct.unpack_from('<HHH', raw, at + 20)
    image_format, mips = struct.unpack_from('<BB', raw, at + 26)
    if (not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION
            or not 0 < depth <= MAX_DIMENSION or not 0 < mips <= 16):
        raise ConversionError('Unsupported texture dimensions')
    return dict(width=width, height=height, depth=depth, format=image_format, mips=mips)


def derive_probe_layout(atlas, cubemap, *, atlas_resource, atlas_sha256,
                        cubemap_resource, cubemap_sha256):
    """Qualify installed probe textures and return their explicit layout."""
    if atlas['format'] != ATLAS_FORMAT or atlas['mips'] != ATLAS_MIPS:
        raise ConversionError('Probe atlas format/mip layout requires requalification')
    if atlas['depth'] % 6:
        raise ConversionError('Probe atlas depth must contain six face slabs')
    layers = atlas['depth'] // 6
    if (cubemap['format'] != CUBEMAP_FORMAT or cubemap['mips'] != CUBEMAP_MIPS
            or cubemap['width'] != CUBEMAP_SIZE or cubemap['height'] != CUBEMAP_SIZE):
        raise ConversionError('Probe cubemap format/layout requires requalification')
    if not 1 <= cubemap['depth'] <= MAX_CUBE_DEPTH:
        raise ConversionError('Unsupported probe cubemap array depth')
    layout = dict(schema_version=1, dimensions=[atlas['width'], atlas['height'], layers],
                  cube_depth=cubemap['depth'],
                  atlas=dict(resource=atlas_resource, sha256=atlas_sha256, **atlas),
                  cubemap=dict(resource=cubemap_resource, sha256=cubemap_sha256, **cubemap),
                  basis='VTEX v1 DATA header dimensions; six stacked atlas faces; cubemap array depth')
    validate_probe_layout(layout)
    return layout


def validate_probe_layout(layout):
    if not isinstance(layout, dict) or type(layout.get('schema_version')) is not int or layout['schema_version'] != 1:
        raise ConversionError('Unsupported probe layout')
    dimensions = layout.get('dimensions')
    if (not isinstance(dimensions, list) or len(dimensions) != 3
            or any(type(value) is not int or not 0 < value <= MAX_DIMENSION for value in dimensions)):
        raise ConversionError('Invalid probe atlas dimensions')
    cube_depth = layout.get('cube_depth')
    if type(cube_depth) is not int or not 1 <= cube_depth <= MAX_CUBE_DEPTH:
        raise ConversionError('Invalid probe cubemap depth')
    for key, expected in (('atlas', ATLAS_FORMAT), ('cubemap', CUBEMAP_FORMAT)):
        row = layout.get(key)
        if (not isinstance(row, dict) or not isinstance(row.get('resource'), str)
                or not isinstance(row.get('sha256'), str) or len(row['sha256']) != 64
                or row.get('format') != expected):
            raise ConversionError('Invalid probe texture identity')
    atlas = layout['atlas']
    if atlas['depth'] != dimensions[2] * 6 or atlas['mips'] != ATLAS_MIPS:
        raise ConversionError('Probe atlas dimensions differ from the face layout')
    cubemap = layout['cubemap']
    if (cubemap['depth'] != cube_depth or cubemap['width'] != CUBEMAP_SIZE
            or cubemap['height'] != CUBEMAP_SIZE or cubemap['mips'] != CUBEMAP_MIPS):
        raise ConversionError('Probe cubemap dimensions differ from the array layout')
    return layout


def dimensions_for_plan(plan):
    """Return (dimensions, cube_depth) for a current plan or a legacy package."""
    layout = plan.get('probe_layout')
    if layout is not None:
        validate_probe_layout(layout)
        return list(layout['dimensions']), layout['cube_depth']
    from .source_lighting_profiles import probe_layout
    dimensions, cube_depth = probe_layout(plan['world_sha256'])
    return list(dimensions), cube_depth
