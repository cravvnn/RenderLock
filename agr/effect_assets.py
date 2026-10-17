"""Derived particle texture and attachment assets from installed resources.

Texture crops and sequence indices come from Source 2 Viewer's MKS export.
No atlas is rendered as a single sprite when its sequence cannot be represented.
These cache operations have their own identity, leaving hero conversions reusable.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shlex
import struct
import subprocess
import zlib

from .asset_converter import ConversionError, compiled, sha
from .game_assets import safe_resource_path
from .vendor.kv3_text import parse_kv3


_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_MAX_FILE = 256 * 1024 * 1024
_MAX_IMAGES = 8192


def _json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def _export_path(directory, value):
    """Keep every referenced file within the converter's private output tree."""
    # safe_resource_path checks platform-independent absolute and device names.
    # Keep original case on disk: Source 2 Viewer can preserve it in MKS files.
    safe_resource_path(value)
    candidate = (Path(directory) / value.replace('\\', '/')).resolve()
    if not candidate.is_relative_to(Path(directory).resolve()) or not candidate.is_file():
        raise ConversionError(f'Missing or unsafe texture export: {value}')
    return candidate


def png_info(path):
    """Check PNG structure, dimensions, chunk CRCs and completeness, without Pillow."""
    path = Path(path)
    if path.stat().st_size > _MAX_FILE:
        raise ConversionError(f'Particle texture exceeds supported size: {path.name}')
    with path.open('rb') as stream:
        if stream.read(8) != _PNG_SIGNATURE:
            raise ConversionError(f'Invalid PNG signature: {path.name}')
        width = height = None
        saw_data = False
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ConversionError(f'Truncated PNG: {path.name}')
            size, kind = struct.unpack('>I4s', header)
            if size > _MAX_FILE:
                raise ConversionError(f'Invalid PNG chunk size: {path.name}')
            payload = stream.read(size)
            checksum = stream.read(4)
            if len(payload) != size or len(checksum) != 4:
                raise ConversionError(f'Truncated PNG chunk: {path.name}')
            if zlib.crc32(payload, zlib.crc32(kind)) & 0xffffffff != struct.unpack('>I', checksum)[0]:
                raise ConversionError(f'PNG checksum mismatch: {path.name}')
            if width is None:
                if kind != b'IHDR' or size != 13:
                    raise ConversionError(f'PNG has no valid header: {path.name}')
                width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
                valid_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
                                4: (8, 16), 6: (8, 16)}
                if (not 0 < width <= 16384 or not 0 < height <= 16384
                        or depth not in valid_depths.get(color, ())
                        or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                    raise ConversionError(f'Unsupported PNG header: {path.name}')
            elif kind == b'IHDR':
                raise ConversionError(f'Duplicate PNG header: {path.name}')
            if kind == b'IDAT':
                saw_data = True
            if kind == b'IEND':
                if size or not saw_data or stream.read(1):
                    raise ConversionError(f'Invalid PNG end: {path.name}')
                return {'width': width, 'height': height, 'sha256': sha(path)}


def parse_mks(text, directory):
    """Read the subset emitted by VRF20; preserve unsupported sequences explicitly."""
    if len(text) > 8 * 1024 * 1024:
        raise ConversionError('Particle sequence description is too large')
    sequences = {}
    current = None
    packing = None
    for number, line in enumerate(text.splitlines(), 1):
        line = line.split('//', 1)[0].strip()
        if not line:
            continue
        try:
            fields = shlex.split(line, posix=True)
        except ValueError as exc:
            raise ConversionError(f'Invalid MKS line {number}: {exc}') from exc
        key = fields[0].lower()
        if key == 'packmode' and len(fields) == 2:
            packing = fields[1].lower()
        elif key in ('sequence', 'sequence-rgb', 'sequence-a') and len(fields) == 2:
            if not fields[1].isdigit() or int(fields[1]) > 65535:
                raise ConversionError(f'Invalid MKS sequence index at line {number}')
            index = str(int(fields[1]))
            if index in sequences:
                raise ConversionError(f'Duplicate MKS sequence {index}')
            current = {'kind': key, 'loop': False, 'frames': [], 'reasons': []}
            sequences[index] = current
        elif key == 'name' and len(fields) >= 2 and current is not None:
            # VRF TextureExtract emits authored sequence names as metadata.
            # Runtime selection still uses the numeric sequence index; a name
            # must not renumber entries or make animated/split sheets supported.
            if 'name' in current or not ' '.join(fields[1:]).strip():
                raise ConversionError(f'Invalid or duplicate MKS sequence name at line {number}')
            current['name'] = ' '.join(fields[1:])
        elif key == 'loop' and len(fields) == 1 and current is not None:
            current['loop'] = True
        elif key == 'frame' and len(fields) == 3 and current is not None:
            try:
                duration = float(fields[2])
            except ValueError as exc:
                raise ConversionError(f'Invalid MKS frame duration at line {number}') from exc
            if not math.isfinite(duration) or duration < 0:
                raise ConversionError(f'Invalid MKS frame duration at line {number}')
            image = _export_path(directory, fields[1])
            info = png_info(image)
            current['frames'].append({'image': image.relative_to(Path(directory).resolve()).as_posix(),
                                      'duration': duration, **info})
            if sum(len(sequence['frames']) for sequence in sequences.values()) > _MAX_IMAGES:
                raise ConversionError('Too many frames in particle sequence')
        else:
            raise ConversionError(f'Unsupported MKS statement at line {number}: {key}')
    for sequence in sequences.values():
        if packing not in (None, 'flat') or sequence['kind'] != 'sequence':
            sequence['reasons'].append('separate RGB/alpha packing is not implemented')
        if len(sequence['frames']) != 1:
            sequence['reasons'].append('animated or empty texture sequence is not implemented')
        sequence['status'] = 'unsupported' if sequence['reasons'] else 'supported'
    return sequences


def _texture_descriptor(directory, resource):
    directory = Path(directory).resolve()
    files = [p for p in directory.rglob('*') if p.is_file()]
    if len(files) > _MAX_IMAGES + 16:
        raise ConversionError('Texture exporter produced too many files')
    for path in files:
        if not path.resolve().is_relative_to(directory):
            raise ConversionError('Texture exporter produced an unsafe linked file')
    mks = [p for p in files if p.suffix.lower() == '.mks']
    reasons = []
    if len(mks) == 1:
        # MKS subfiles normally share a directory with their script. Retain paths
        # relative to the whole cached conversion, even if VRF uses a subfolder.
        sequences = parse_mks(mks[0].read_text(encoding='utf-8-sig'), mks[0].parent)
        prefix = mks[0].parent.relative_to(directory)
        for seq in sequences.values():
            for frame in seq['frames']:
                frame['image'] = (prefix / frame['image']).as_posix()
        atlas = True
    elif mks:
        sequences = {}
        reasons.append('multiple MKS descriptions are not implemented')
        atlas = True
    else:
        pngs = [p for p in files if p.suffix.lower() == '.png']
        # Some source VTEXs store raw PNG bytes; the CLI writes that content to
        # the requested output filename rather than emitting an image subfile.
        if not pngs:
            for path in files:
                if path.suffix.lower() == '.vtex':
                    with path.open('rb') as stream:
                        if stream.read(8) == _PNG_SIGNATURE:
                            pngs.append(path)
        atlas = False
        if len(pngs) == 1:
            info = png_info(pngs[0])
            sequences = {'0': {'kind': 'sequence', 'loop': False, 'status': 'supported', 'reasons': [],
                               'frames': [{'image': pngs[0].relative_to(directory).as_posix(),
                                           'duration': 1.0, **info}]}}
        else:
            sequences = {}
            reasons.append('no unambiguous PNG sprite export; HDR, arrays and cubemaps are not implemented')
    good = sum(seq['status'] == 'supported' for seq in sequences.values())
    status = 'supported' if good and good == len(sequences) and not reasons else 'partial' if good else 'unsupported'
    return {'schema_version': 1, 'resource': resource, 'status': status, 'atlas': atlas, 'is_sprite_sheet': atlas,
            'sequences': sequences, 'reasons': reasons,
            'images': {frame['image']: {key: frame[key] for key in ('width', 'height', 'sha256')}
                       for seq in sequences.values() for frame in seq['frames']}}


def _kv3_blocks(text):
    """Separate concatenated -b MDAT KV3 documents, ignoring CLI status text."""
    for match in re.finditer(r'<!-- kv3[^\n]*?-->', text):
        start = match.start()
        pos = match.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text) or text[pos] != '{':
            raise ConversionError('Unsupported MDAT text block')
        depth, quoted, escape = 0, False, False
        for end in range(pos, len(text)):
            char = text[end]
            if quoted:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if not depth:
                    yield parse_kv3(text[start:end + 1])
                    break
        else:
            raise ConversionError('Truncated MDAT text block')


def attachment_values(data):
    """Return source order, never sort attachment names: network IDs are 1-based."""
    raw = data.get('m_attachments')
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConversionError('Unsupported model attachment table')
    result = []
    for row in raw:
        if not isinstance(row, dict):
            raise ConversionError('Invalid model attachment entry')
        value = row.get('value', row)
        if not isinstance(value, dict) or not isinstance(value.get('m_name'), str):
            raise ConversionError('Invalid model attachment value')
        # Do not prune unused/unsupported attachments: that would shift IDs.
        result.append(value)
    return result


class EffectAssetAdapter:
    def __init__(self, decoder):
        self.decoder = decoder
        self.identity = {'effect_adapter_sha256': sha(Path(__file__))}

    def texture(self, resource, provider):
        resource = safe_resource_path(compiled(resource))
        if not resource.endswith('.vtex_c'):
            raise ConversionError(f'Expected a particle VTEX resource: {resource}')
        resolved = provider.resolve(resource)
        def build(temp):
            command = [str(self.decoder.executable), '-i', str(resolved.local_path),
                       '-o', str(temp / 'texture.vtex'), '-d']
            proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', timeout=180)
            output = proc.stdout + '\n' + proc.stderr
            (temp / 'texture_converter.log').write_text(output, encoding='utf-8')
            if proc.returncode:
                raise ConversionError(f'Particle texture conversion failed for {resource}: {output[-2000:]}')
            _json(temp / 'texture.json', _texture_descriptor(temp, resource))
        dest = self.decoder._entry('particle_texture', {resource: resolved.sha256}, self.identity, build)
        return dest, json.loads((dest / 'texture.json').read_text(encoding='utf-8'))

    def attachments(self, resource, provider):
        """Ordered attachment values; source attachment ID N maps to result[N-1]."""
        resource = safe_resource_path(compiled(resource))
        resolved = provider.resolve(resource)
        def build(temp):
            # Embedded mesh blocks are the actual attachment source on modern
            # heroes. DATA contains multiline authoring text beyond the general
            # clip parser's subset, and is unnecessary for these models.
            proc = subprocess.run([str(self.decoder.executable), '-i', str(resolved.local_path), '-b', 'MDAT'],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            if proc.returncode:
                raise ConversionError(f'MDAT attachment decode failed for {resource}: {proc.stderr[-2000:]}')
            blocks = list(_kv3_blocks(proc.stdout))
            tables = [value for block in blocks if (value := attachment_values(block))]
            # LOD meshes repeat the same ordered table. Differing tables need
            # explicit model mesh selection; never guess a new network order.
            unique = {json.dumps(value, sort_keys=True): value for value in tables}
            if len(unique) > 1:
                raise ConversionError(f'Ambiguous attachment tables in embedded meshes: {resource}')
            table = next(iter(unique.values()), [])
            source = 'MDAT'
            if not blocks:
                table = attachment_values(self.decoder.dump(resource, provider))
                source = 'DATA'
            _json(temp / 'attachments.json', {'resource': resource, 'source_block': source,
                                             'index_base': 1, 'attachments': table})
        dest = self.decoder._entry('particle_attachments', {resource: resolved.sha256}, self.identity, build)
        return json.loads((dest / 'attachments.json').read_text(encoding='utf-8'))['attachments']
