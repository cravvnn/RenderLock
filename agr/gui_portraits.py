"""Read missing UI portraits from the user's installed game, never the package."""
from __future__ import annotations

from pathlib import Path
import struct
import subprocess
import tempfile
import zlib

NURSE_HERO_ID = 87
NURSE_RESOURCE = 'panorama/images/heroes/nurse_sm_psd.vtex_c'
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_SIDE = 512
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def decode_rgba_png(payload: bytes) -> tuple[int, bytes]:
    """Decode a bounded, noninterlaced 8-bit RGBA portrait PNG, preserving alpha."""
    if not isinstance(payload, bytes) or len(payload) > MAX_PNG_BYTES or not payload.startswith(PNG_SIGNATURE):
        raise ValueError('Invalid or oversized portrait PNG')
    pos, width, height, ended, chunks = 8, None, None, False, []
    while pos < len(payload):
        if pos + 12 > len(payload):
            raise ValueError('Truncated portrait PNG chunk')
        length, kind = struct.unpack_from('>I4s', payload, pos)
        end = pos + 12 + length
        if end > len(payload):
            raise ValueError('Truncated portrait PNG data')
        data = payload[pos+8:pos+8+length]
        expected = struct.unpack_from('>I', payload, pos+8+length)[0]
        if zlib.crc32(data, zlib.crc32(kind)) & 0xffffffff != expected:
            raise ValueError('Portrait PNG checksum mismatch')
        if width is None:
            if kind != b'IHDR' or length != 13:
                raise ValueError('Portrait PNG has no valid header')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', data)
            if (not 0 < width == height <= MAX_SIDE or
                    (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0)):
                raise ValueError('Portrait PNG needs a square 8-bit RGBA image')
        elif kind == b'IHDR':
            raise ValueError('Duplicate portrait PNG header')
        elif kind == b'IDAT':
            chunks.append(data)
        elif kind == b'IEND':
            if length or not chunks or end != len(payload):
                raise ValueError('Invalid portrait PNG end')
            ended = True
            break
        pos = end
    if not ended:
        raise ValueError('Portrait PNG is incomplete')
    stride = width * 4
    expected_size = (stride + 1) * height
    stream = zlib.decompressobj()
    decoded = stream.decompress(b''.join(chunks), expected_size + 1)
    if len(decoded) != expected_size or not stream.eof or stream.unconsumed_tail or stream.unused_data:
        raise ValueError('Portrait PNG decompressed size is invalid')
    out = bytearray(stride * height)
    prior = bytearray(stride)
    for row in range(height):
        offset = row * (stride + 1)
        filter_type = decoded[offset]
        if filter_type > 4:
            raise ValueError('Unsupported portrait PNG filter')
        values = bytearray(decoded[offset+1:offset+1+stride])
        for i, value in enumerate(values):
            left = values[i-4] if i >= 4 else 0
            above = prior[i]
            upper_left = prior[i-4] if i >= 4 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                p = left + above - upper_left
                distances = (abs(p-left), abs(p-above), abs(p-upper_left))
                predictor = (left if distances[0] <= distances[1] and distances[0] <= distances[2]
                             else above if distances[1] <= distances[2] else upper_left)
            values[i] = (value + predictor) & 255
        out[row*stride:(row+1)*stride] = values
        prior = values
    return width, bytes(out)


def load_nurse_portrait(game_install, converter, cache_dir):
    """Convert the user's exact installed portrait off the UI thread."""
    from .game_assets import GameAssetProvider
    executable = Path(converter).expanduser().resolve()
    if not executable.is_file() or executable.name.lower() in {'source2viewer.exe', 'source2viewer'}:
        raise ValueError('Source 2 Viewer CLI is unavailable')
    provider = GameAssetProvider(game_install, cache_dir)
    resolved = provider.resolve(NURSE_RESOURCE)
    temporary_root = provider.cache_dir/'portrait-work'
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nurse-', dir=temporary_root) as folder:
        output = Path(folder)/'portrait.vtex'
        result = subprocess.run([str(executable), '-i', str(resolved.local_path), '-o', str(output), '-d'],
            capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('Installed Nurse portrait conversion failed')
        images = [path for path in Path(folder).iterdir()
                  if path.is_file() and (path.suffix.lower() == '.png' or path == output)]
        images = [path for path in images if path.stat().st_size <= MAX_PNG_BYTES
                  and path.read_bytes().startswith(PNG_SIGNATURE)]
        if len(images) != 1:
            raise ValueError('Installed Nurse portrait did not produce one PNG')
        size, rgba = decode_rgba_png(images[0].read_bytes())
    for archive in provider.archives:
        archive.assert_unchanged()
    return size, rgba
