"""Version-bound reconstructed cloth inputs, cached independently of hero models."""
from pathlib import Path
import hashlib
import json
import re


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _path(cache, digest):
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid compiled model SHA256')
    return Path(cache) / 'prepared-cloth-v1' / (digest + '.json')


def _validate_bundle(definition, physics):
    if definition.get('schema') != 'agr_decompiled_fe_proxy_bundle_v1':
        return
    parts = definition.get('parts')
    if (not isinstance(parts, list) or not 1 <= len(parts) <= 16
            or any(not isinstance(p, dict) or p.get('schema') != 'agr_decompiled_fe_proxy_v1'
                   or p.get('physics') != physics or not isinstance(p.get('mesh_suffix'), str)
                   or not p['mesh_suffix'].startswith('.') or len(p['mesh_suffix']) < 2 for p in parts)
            or len({p['mesh_suffix'] for p in parts}) != len(parts)):
        raise ValueError('Invalid reconstructed garment bundle')


def register(cache, resource, model_sha256, definition, physics):
    if definition.get('schema') not in ('agr_decompiled_fe_proxy_v1', 'agr_decompiled_fe_proxy_bundle_v1'):
        raise ValueError('Unsupported prepared cloth schema')
    if definition.get('physics') != physics:
        raise ValueError('Prepared cloth physics differs from installed model')
    if not definition.get('source_sha256') or not definition.get('scope'):
        raise ValueError('Prepared cloth must include provenance and coverage scope')
    _validate_bundle(definition, physics)
    target = _path(cache, model_sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {'schema_version': 1, 'resource': resource, 'model_sha256': model_sha256,
              'definition_sha256': _digest(definition), 'definition': definition}
    temp = target.with_suffix('.part')
    temp.write_text(json.dumps(record, allow_nan=False), encoding='utf-8')
    temp.replace(target)
    return target


def load(cache, resource, model_sha256, physics):
    target = _path(cache, model_sha256)
    if not target.is_file():
        return None
    record = json.loads(target.read_text(encoding='utf-8'))
    definition = record['definition']
    if (record.get('schema_version') != 1 or record.get('resource') != resource
            or record.get('model_sha256') != model_sha256
            or record.get('definition_sha256') != _digest(definition)
            or definition.get('schema') not in ('agr_decompiled_fe_proxy_v1', 'agr_decompiled_fe_proxy_bundle_v1')
            or definition.get('physics') != physics):
        raise ValueError('Prepared cloth cache identity/integrity mismatch; regenerate its definition')
    _validate_bundle(definition, physics)
    return definition
