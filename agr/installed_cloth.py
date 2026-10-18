"""Installed FE inputs for version-matched decompiled cloth proxies.

The experimental native resimulation (Seven waist surface cloth and the Haze
hose prototype) was retired in 0.3.0b1. Preparation reconstructs supported
decompiled proxies automatically and caches definitions whose compiled model,
decoded physics, resource identity and integrity match the installed game build.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from .asset_converter import ConversionError, compiled, sha
from .vendor.kv3_text import parse_kv3


class UnsupportedCloth(ValueError):
    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def decode_physics(resource, provider, decoder):
    """Keep FE conversion in a separate cache operation, preserving model keys."""
    resolved = provider.resolve(compiled(resource))
    def build(temp):
        proc = subprocess.run([str(decoder.executable), '-i', str(resolved.local_path), '-b', 'PHYS'],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        if proc.returncode:
            raise ConversionError(f'PHYS decode failed for {resource}: {proc.stderr[-1500:]}')
        try:
            value = parse_kv3(proc.stdout)
        except (ValueError, IndexError) as exc:
            raise ConversionError(f'Unsupported PHYS for {resource}: {exc}') from exc
        (temp / 'physics.json').write_text(json.dumps(value), encoding='utf-8')
    path = decoder._entry('cloth_PHYS', {compiled(resource): resolved.sha256},
                          {'adapter_sha256': sha(Path(__file__))}, build)
    return json.loads((path / 'physics.json').read_text(encoding='utf-8')), resolved.sha256


def prepare_cloth_assets(segment, bindings, provider, decoder, pack, log=print, *, extractor_command=None):
    """Mutate matched entities with secondary_motion_file; return full coverage."""
    pack = Path(pack)
    dest = pack / 'secondary-motion'
    dest.mkdir(parents=True, exist_ok=True)
    entities = {str(e['id']): e for e in segment['entities']}
    for entity in entities.values():
        entity.pop('secondary_motion_file', None)
    report = {'status': 'prepared', 'supported_entities': [], 'unsupported_entities': [], 'not_applicable_entities': [],
              'scope': 'Version-matched prepared decompiled cloth proxies; all other layouts explicitly reported'}
    decoded = {}
    for binding in bindings:
        resource = binding['resource']
        entity = entities[str(binding['id'])]
        row = {'id': entity['id'], 'hero_name': entity.get('hero_name'), 'resource': resource}
        try:
            if resource not in decoded:
                physics, digest = decode_physics(resource, provider, decoder)
                from .prepared_cloth import load
                cache = getattr(provider, 'cache_dir', None)
                prepared = load(cache, compiled(resource), digest, physics) if cache is not None else None
                if prepared is None:
                    from .reconstructed_cloth import reconstruct
                    generated = reconstruct(resource, provider, decoder, physics, command=extractor_command)
                    if generated['status'] == 'not_applicable':
                        row.update(status='not_applicable', reason=generated['reason'])
                        report['not_applicable_entities'].append(row)
                        continue
                    prepared = generated['definition']
                    if cache is not None:
                        from .prepared_cloth import register
                        register(cache, compiled(resource), digest, prepared, physics)
                target = dest / (hashlib.sha256(resource.encode()).hexdigest()[:20] + '.json')
                target.write_text(json.dumps(prepared, indent=2) + '\n', encoding='utf-8')
                decoded[resource] = (target, prepared)
            target, definition = decoded[resource]
            entity['secondary_motion_file'] = str(target.resolve())
            row.update(status='ready_to_bake', schema=definition['schema'],
                       scope=definition['scope'], limitations=definition.get('limitations', []))
            report['supported_entities'].append(row)
            log(f"Cloth: loaded version-matched reconstructed proxy for {entity['id']}")
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            row.update(status='unsupported', reason=str(exc))
            report['unsupported_entities'].append(row)
    if not report['supported_entities']:
        report['status'] = 'no_supported_entities' if report['unsupported_entities'] else 'not_applicable'
    elif report['unsupported_entities']:
        report['status'] = 'prepared_partial'
    (dest / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
