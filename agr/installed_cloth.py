"""Installed FE inputs for version-matched decompiled cloth proxies.

The experimental native resimulation (Seven waist surface cloth and the Haze
hose prototype) was retired in 0.3.0b1. Preparation reconstructs supported
decompiled proxies automatically and caches definitions whose compiled model,
decoded physics, resource identity and integrity match the installed game build.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess

from .asset_converter import ConversionError, compiled, sha


class UnsupportedCloth(ValueError):
    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def decode_physics(resource, provider, decoder, command=None):
    """Transport binary FE values without display-text numeric rounding.

    Referenced PHYS resources are resolved and cached by their own content
    hashes on every request, so changing one cannot reuse an older model's FE.
    """
    if command is None:
        from .reconstructed_cloth import discover_extractor
        command,environment=discover_extractor()
    else:
        command,environment=list(command),None
    if not command or not Path(command[-1]).is_file():
        raise ConversionError('Binary PHYS extractor is unavailable')
    tool=Path(command[-1])
    settings={'adapter_sha256':sha(Path(__file__)),'probe_sha256':sha(tool),
              'numeric_encoding':'binary_kv3_roundtrip_v1'}
    for name in ('ValveResourceFormat.dll','ValveKeyValue.dll'):
        library=tool.with_name(name)
        settings[name]=sha(library) if library.is_file() else None

    def reject_constant(token):
        raise ValueError('Nonfinite PHYS JSON value: '+token)

    def finite_float(token):
        value=float(token)
        if not math.isfinite(value):
            raise ValueError('Nonfinite PHYS JSON value: '+token)
        return value

    def read(name):
        name=compiled(name);resolved=provider.resolve(name)
        def build(temp):
            proc=subprocess.run(command+['--physics-json-v1',str(resolved.local_path.resolve()),
                resolved.sha256,str((temp/'physics.json').resolve())],env=environment,
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
            if proc.returncode:
                raise ConversionError(f'Binary PHYS decode failed for {name}: {proc.stderr[-1500:]}')
        path=decoder._entry('cloth_PHYS_binary_v1',{name:resolved.sha256},settings,build)
        output=path/'physics.json'
        if not output.is_file() or not 0<output.stat().st_size<=32*1024*1024:
            raise ConversionError('Binary PHYS JSON is missing or exceeds its size bound')
        try:
            value=json.loads(output.read_text(encoding='utf-8'),parse_constant=reject_constant,parse_float=finite_float)
        except (ValueError,UnicodeError) as exc:
            raise ConversionError('Binary PHYS JSON is malformed: '+str(exc)) from exc
        if (not isinstance(value,dict) or type(value.get('schema_version')) is not int
                or value['schema_version']!=1 or value.get('resource_sha256')!=resolved.sha256
                or value.get('numeric_encoding')!='binary_kv3_roundtrip_v1'
                or value.get('status') not in ('embedded','direct','not_applicable','referenced')):
            raise ConversionError('Binary PHYS identity or numeric transport contract changed')
        refs=value.get('referenced_physics')
        if (not isinstance(refs,list) or len(refs)>1 or any(not isinstance(ref,str)
                or not ref.endswith('.vphys') or '..' in ref or ':' in ref or '\\' in ref
                or ref.startswith('/') for ref in refs)):
            raise ConversionError('Binary PHYS referenced resource inventory changed')
        physics=value.get('physics')
        if value['status']=='referenced':
            if len(refs)!=1 or physics is not None:
                raise ConversionError('Binary PHYS referenced outcome differs')
        elif (refs or not isinstance(physics,dict) or 'm_pFeModel' not in physics
                or physics['m_pFeModel'] is not None and not isinstance(physics['m_pFeModel'],dict)
                or value['status']=='not_applicable' and physics['m_pFeModel'] is not None):
            raise ConversionError('Binary PHYS data outcome differs')
        return value,resolved.sha256

    value,digest=read(resource)
    if value['status']=='referenced':
        value,_=read(value['referenced_physics'][0])
        if value['status']!='direct':
            raise ConversionError('Referenced PHYS did not resolve to a direct physics aggregate')
    return value['physics'],digest


def prepare_cloth_assets(segment, bindings, provider, decoder, pack, log=print, *, extractor_command=None):
    """Mutate matched entities with secondary_motion_file; return full coverage."""
    pack = Path(pack)
    dest = pack / 'secondary-motion'
    dest.mkdir(parents=True, exist_ok=True)
    entities = {str(e['id']): e for e in segment['entities']}
    for entity in entities.values():
        entity.pop('secondary_motion_file', None)
    report = {'status': 'prepared', 'supported_entities': [], 'unsupported_entities': [], 'failed_entities': [], 'not_applicable_entities': [],
              'scope': 'Version-matched prepared decompiled cloth proxies; all other layouts explicitly reported'}
    decoded = {}
    for binding in bindings:
        resource = binding['resource']
        entity = entities[str(binding['id'])]
        row = {'id': entity['id'], 'hero_name': entity.get('hero_name'), 'resource': resource}
        try:
            if resource not in decoded:
                physics, digest = decode_physics(resource, provider, decoder, extractor_command)
                from .prepared_cloth import load
                cache = getattr(provider, 'cache_dir', None)
                try:
                    prepared = load(cache, compiled(resource), digest, physics) if cache is not None else None
                except ValueError as exc:
                    raise ConversionError('Prepared cloth cache validation failed: '+str(exc)) from exc
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
        except (ConversionError, OSError, KeyError, TypeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            row.update(status='failed', stage='preparation', error=str(exc), error_type=type(exc).__name__)
            report['failed_entities'].append(row)
            log(f"Cloth preparation failed for {entity['id']}: {exc}")
        except ValueError as exc:
            row.update(status='unsupported', reason=str(exc))
            report['unsupported_entities'].append(row)
    if report['failed_entities']:
        report.update(status='failed',stage='preparation',
                      reason='Cloth preparation failed for one or more characters; see failed_entities.')
    elif not report['supported_entities']:
        report['status'] = 'no_supported_entities' if report['unsupported_entities'] else 'not_applicable'
    elif report['unsupported_entities']:
        report['status'] = 'prepared_partial'
    (dest / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
