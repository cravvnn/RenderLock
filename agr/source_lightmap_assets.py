"""Prepare identity-checked SH textures for the qualified partial lighting path.

This is asset decoding, not validation of engine colourimetry or full lighting.
Each texture uses the normal content-verified converter cache independently.
"""
from pathlib import Path
import json
import math
import re
import struct
import subprocess
import time
import sys

from .asset_converter import ConversionError, compiled, sha
from .installed_map import selected_world
from .map_data import dump_map_data
from .source_lightmap import SHADER_RESOURCE, SHADER_SHA256


QUALIFIED_CONVERTER_SHA256 = 'f3381c8e7866397defbad040a5bfb8640d4d89786469a1f19ef64dd3cfbc13ab'
QUALIFIED_CONVERTER_6779_SHA256 = 'fa7fd27bc611d43b9a266c287f44906d7f15719c41d4703a2e0beae3c0dd3bd6'
QUALIFIED_CONVERTER_20_1_SHA256 = 'ef9f6fb3943c30b73f0991bce6654128727b0807968f7997a6bb014efbec8830'
QUALIFIED_SKIA_SHA256 = '09fb4b1afee9810f127672baff0e050ed076b6d889b90adec7271914c3894125'
QUALIFIED_SKIA_20_1_SHA256 = '0d98e29c028b3315d0e0344d02cd7aa4080abdf17fa67086847da13435320f2a'
# A decoder is only accepted together with its qualified image companion. The
# 20.1 pair is required for map data from the October 2026 Deadlock update.
QUALIFIED_DECODER_PAIRS = (
    (QUALIFIED_CONVERTER_SHA256, QUALIFIED_SKIA_SHA256),
    (QUALIFIED_CONVERTER_6779_SHA256, QUALIFIED_SKIA_SHA256),
    (QUALIFIED_CONVERTER_20_1_SHA256, QUALIFIED_SKIA_20_1_SHA256),
)
CHANNELS = ('DC', 'R', 'G', 'B')


def image_decoder_identity(decoder, *, executable_sha256=None):
    """Bind qualification to the native image library the host will actually load.

    Recording a Unix companion does not grant qualification to an unknown pair.
    In particular, a missing Windows DLL is never evidence about Linux decoding.
    """
    name = ('libSkiaSharp.dll' if sys.platform == 'win32' else
            'libSkiaSharp.dylib' if sys.platform == 'darwin' else 'libSkiaSharp.so')
    return (executable_sha256 or decoder.identity.get('executable_sha256'),
            decoder.identity.get('companions', {}).get(name))


def qualify_world_lighting(world):
    info = world.get('m_worldLightingInfo', {})
    for key, value in {'m_nLightmapVersionNumber': 8, 'm_nLightmapGameVersionNumber': 4,
                       'm_nLightmapEncoding': 1}.items():
        if type(info.get(key)) is not int or info[key] != value:
            raise ConversionError(f'Unqualified Source lightmap setting: {key}')
    if info.get('m_bHasLightmaps') is not True or info.get('m_bCompressionEnabled') is not True:
        raise ConversionError('Qualified compressed Source lightmaps are required')
    scale = info.get('m_vLightmapUvScale')
    if (not isinstance(scale, list) or len(scale) != 2 or any(
            type(x) not in (float, int) or not math.isfinite(x) or x <= 0 for x in scale)):
        raise ConversionError('Invalid Source lightmap UV scale')
    resources = info.get('m_lightMaps')
    if not isinstance(resources, list) or any(not isinstance(x, str) for x in resources):
        raise ConversionError('Invalid Source lightmap resource inventory')
    result = {}
    for channel in CHANNELS:
        name = f'directional_irradiance_sh2_{channel.lower()}.vtex'
        matches = [x for x in resources if Path(x).name == name]
        if len(matches) != 1:
            raise ConversionError(f'Source lightmap requires exactly one {channel} texture')
        result[channel] = matches[0]
    return result


def qualify_texture_header(header, channel):
    """Require the exact single-layer texture layout qualified in C01/C03."""
    if channel not in CHANNELS:
        raise ConversionError('Unknown Source lightmap channel')
    expected = {'VTEX Version': 1, 'Width': 8192, 'Height': 8192, 'Depth': 1,
                'NumMipLevels': 1, 'Format': 19 if channel == 'DC' else 1, 'Flags': 0x40}
    for key, value in expected.items():
        matches = re.findall(r'^' + re.escape(key) + r'\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)\b', header, re.M)
        if len(matches) != 1 or int(matches[0], 16 if matches[0].startswith('0x') else 10) != value:
            raise ConversionError(f'Unqualified {channel} lightmap texture: {key}')
    return expected


def decoded_image_size(path, channel):
    """Read bounded image headers; full payload identity is checked by the cache."""
    with Path(path).open('rb') as stream:
        if channel != 'DC':
            header = stream.read(33)
            if len(header) != 33 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[8:16] != b'\0\0\0\rIHDR':
                raise ConversionError('Directional lightmap decoder did not produce PNG')
            if header[24] != 8 or header[25] not in (2, 6):
                raise ConversionError('Directional lightmap requires 8-bit RGB/RGBA data')
            return list(struct.unpack('>II', header[16:24]))
        if stream.read(8) != struct.pack('<II', 20000630, 2):
            raise ConversionError('DC lightmap requires a single scanline EXR')
        def string():
            text = bytearray()
            for _ in range(256):
                char = stream.read(1)
                if char == b'\0': return bytes(text)
                if not char: break
                text.extend(char)
            raise ConversionError('Invalid EXR attribute name/type')
        size = None
        for _ in range(128):
            name = string()
            if not name:
                if size is None: raise ConversionError('EXR is missing dataWindow')
                return size
            kind = string()
            raw = stream.read(4)
            if len(raw) != 4: raise ConversionError('Truncated EXR header')
            length, = struct.unpack('<I', raw)
            if length > 65536: raise ConversionError('Unqualified EXR attribute size')
            value = stream.read(length)
            if len(value) != length: raise ConversionError('Truncated EXR attribute')
            if name == b'dataWindow':
                if kind != b'box2i' or length != 16 or size is not None:
                    raise ConversionError('Invalid EXR dataWindow')
                x0, y0, x1, y1 = struct.unpack('<iiii', value)
                if x0 != 0 or y0 != 0: raise ConversionError('EXR dataWindow must start at zero')
                size = [x1 + 1, y1 + 1]
        raise ConversionError('EXR attribute bound exceeded')


def prepare_lightmap_assets(provider, decoder, *, allow_partial=False, log=print):
    if allow_partial is not True:
        raise ConversionError('Source static diffuse requires explicit partial-lighting selection')
    identity = image_decoder_identity(decoder)
    if identity[0] not in {pair[0] for pair in QUALIFIED_DECODER_PAIRS}:
        raise ConversionError('Source lightmap texture decoder is not qualified')
    if identity not in QUALIFIED_DECODER_PAIRS:
        raise ConversionError('Source lightmap native image decoder is not qualified')
    shader = provider.resolve(SHADER_RESOURCE)
    if shader.sha256 != SHADER_SHA256:
        raise ConversionError('Installed Source lighting shader changed; requalification required')
    world_name = selected_world(provider)
    world = provider.resolve(world_name)
    resources = qualify_world_lighting(dump_map_data(decoder, world_name, provider))
    result = {}
    for channel, resource in resources.items():
        resolved = provider.resolve(compiled(resource))
        filename = 'lightmap.exr' if channel == 'DC' else 'lightmap.png'
        def build(temp):
            header_run = subprocess.run([str(decoder.executable), '-i', str(resolved.local_path), '-b', 'DATA'],
                                        capture_output=True, timeout=120,cwd=temp)
            (temp/'header.txt').write_bytes(header_run.stdout)
            if header_run.returncode:
                raise ConversionError(f'{channel} texture header decode failed: {header_run.stderr[-2000:]!r}')
            qualify_texture_header(header_run.stdout.decode('utf-8-sig'), channel)
            log(f'Decoding Source {channel} lightmap into the verified asset cache')
            with (temp/'decode.log').open('wb') as output:
                run = subprocess.run([str(decoder.executable), '-i', str(resolved.local_path),
                    '-o', str(temp/filename), '-d', '--texture_decode_flags', 'none'],
                    stdout=output, stderr=subprocess.STDOUT, timeout=600,cwd=temp)
            target = temp/filename
            if run.returncode == 0 and not target.is_file():
                # Source 2 Viewer 20.1 names texture dumps after the input
                # resource instead of honoring the requested filename.
                candidates = [p for p in temp.iterdir()
                              if p.is_file() and p.name.startswith(Path(resolved.local_path).stem + '.')]
                if len(candidates) == 1:
                    candidates[0].replace(target)
            if run.returncode or not target.is_file():
                raise ConversionError(f'{channel} lightmap decode failed')
            if decoded_image_size(temp/filename, channel) != [8192, 8192]:
                raise ConversionError(f'{channel} decoded lightmap dimensions changed')
        def build_with_diagnostics(temp):
            try:
                build(temp)
            except Exception as error:
                diagnostic = decoder.root.parent/'map-diagnostics'/f'lightmap-{channel}-{time.time_ns()}'
                try:
                    diagnostic.mkdir(parents=True, exist_ok=False)
                    for name in ('header.txt', 'decode.log', 'exceptions.txt'):
                        if (temp/name).is_file():
                            (diagnostic/name).write_bytes((temp/name).read_bytes())
                    (diagnostic/'error.json').write_text(json.dumps({'channel': channel,
                        'resource': compiled(resource), 'source_sha256': resolved.sha256,
                        'error': str(error), 'error_type': type(error).__name__}, indent=2))
                except OSError:
                    raise error
                raise ConversionError(f'{error}\nSource lightmap diagnostics retained at: {diagnostic}') from error
        directory = decoder._entry('source_SH_lightmap_v1', {compiled(resource): resolved.sha256},
            {'channel': channel, 'decode_flags': 'none', 'adapter_sha256': sha(Path(__file__))}, build_with_diagnostics)
        image = directory/filename
        result[channel] = {'path': str(image), 'sha256': sha(image), 'size': [8192, 8192], 'layer': 0,
                           'source_resource': compiled(resource), 'source_sha256': resolved.sha256}
    return {'world_resource': world_name, 'world_sha256': world.sha256,
            'shader_sha256': shader.sha256, 'lightmaps': result,
            'scope': 'Decoded static SH diffuse assets; no direct, specular, probes, fog or engine colourimetry validation'}
