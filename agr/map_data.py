"""Separate, content-verified DATA cache for map dependency inspection."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from .asset_converter import ConversionError, compiled, sha
from .map_text import MapTextError, parse_map_kv3


def map_data_identity():
    return {'map_data_adapter_sha256': sha(Path(__file__)),
            'map_text_parser_sha256': sha(Path(__file__).with_name('map_text.py'))}


def _retain(decoder, resource, resolved, stdout, stderr, exc, working_dir):
    destination = decoder.root.parent / 'map-diagnostics' / f'data-{time.time_ns()}'
    try:
        destination.mkdir(parents=True, exist_ok=False)
        # Original bytes are retained so no lossy decoding disguises the cause.
        (destination / 'DATA.stdout.txt').write_bytes(stdout)
        (destination / 'DATA.stderr.txt').write_bytes(stderr)
        # Source2Viewer writes this file relative to its working directory.
        # Preserve it before the failed cache transaction removes its scratch.
        exception_log = working_dir / 'exceptions.txt'
        if exception_log.is_file():
            (destination / 'exceptions.txt').write_bytes(exception_log.read_bytes())
        report = {'stage': 'map_DATA', 'resource': resource,
                  'input_sha256': resolved.sha256, 'error': str(exc),
                  'error_type': type(exc).__name__, 'adapter': map_data_identity()}
        if isinstance(exc, MapTextError):
            report['parse_location'] = {'offset': exc.offset, 'line': exc.line,
                                       'column': exc.column, 'excerpt': exc.excerpt}
        (destination / 'error.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return destination
    except OSError:
        return None  # The original failure remains useful if the disk is full.


def dump_map_data(decoder, resource_path, provider):
    """Decode with map grammar, without changing any existing hero cache key."""
    resource = compiled(resource_path)
    resolved = provider.resolve(resource)

    def build(temp):
        stdout, stderr = b'', b''
        try:
            proc = subprocess.run([str(decoder.executable), '-i', str(resolved.local_path), '-b', 'DATA'],
                                  capture_output=True, timeout=120, cwd=temp)
            stdout, stderr = proc.stdout, proc.stderr
            if proc.returncode:
                raise ConversionError(f'Map DATA decoder exited with code {proc.returncode}: '
                                      + stderr[-2000:].decode('utf-8', errors='replace'))
            data = parse_map_kv3(stdout.decode('utf-8-sig'))
            (temp / 'data.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        except (ValueError, subprocess.SubprocessError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                stdout, stderr = exc.stdout or b'', exc.stderr or b''
            destination = _retain(decoder, resource, resolved, stdout, stderr, exc, temp)
            diagnostic = f'\nMap DATA diagnostics retained at: {destination}' if destination else ''
            raise ConversionError(f'Unsupported map DATA for {resource}: {exc}{diagnostic}') from exc

    destination = decoder._entry('map_DATA', {resource: resolved.sha256}, map_data_identity(), build)
    return json.loads((destination / 'data.json').read_text(encoding='utf-8'))
