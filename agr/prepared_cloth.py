"""Version-bound reconstructed cloth inputs, cached independently of hero models."""
from pathlib import Path
import hashlib
import json
import re
import tempfile
import time

from .asset_converter import ConversionError


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _path(cache, resource, digest, physics):
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid compiled model SHA256')
    # Binary numeric transport has a separate namespace. Preserve rounded v1
    # entries and rebuild; referenced physics can change without the model.
    if not isinstance(resource,str) or not resource:
        raise ValueError('Prepared cloth resource identity required')
    key=_digest({'resource':resource,'physics':physics})
    return Path(cache) / 'prepared-cloth-v2' / digest / (key + '.json')


def _validate_bundle(definition, physics):
    if definition.get('schema') != 'agr_decompiled_fe_proxy_bundle_v1':
        return
    parts = definition.get('parts')
    pending = definition.get('unsupported_parts', [])
    if (not isinstance(pending, list) or len(pending) > 16
            or any(not isinstance(part, str) or not part.strip() for part in pending)):
        raise ValueError('Invalid reconstructed garment coverage')
    if (not isinstance(parts, list) or not 1 <= len(parts) <= 16
            or any(not isinstance(p, dict) or p.get('schema') not in (
                'agr_decompiled_fe_proxy_v1','agr_decompiled_fe_bone_proxy_v1')
                or p.get('physics') != physics for p in parts)):
        raise ValueError('Invalid reconstructed garment bundle')
    identities = []
    for part in parts:
        if part['schema']=='agr_decompiled_fe_proxy_v1':
            suffix = part.get('mesh_suffix')
            if not isinstance(suffix,str) or not suffix.startswith('.') or len(suffix)<2:
                raise ValueError('Invalid reconstructed garment bundle')
            identities.append(('surface',suffix))
        else:
            from .fe_proxy_bones import proxy_bone_outputs, uniform_proxy_colliders
            filename = part.get('source_proxy_file')
            if not isinstance(filename,str) or not filename or part.get('bone_basis_yaw_degrees')!=-90.:
                raise ValueError('Invalid back-solved proxy identity/coordinate adapter')
            proxy_bone_outputs(part['proxy'],physics)
            uniform_proxy_colliders(part['proxy'],physics)
            identities.append(('bone',filename))
    if len(set(identities))!=len(identities):
        raise ValueError('Invalid reconstructed garment bundle')


def register(cache, resource, model_sha256, definition, physics):
    if definition.get('schema') not in ('agr_decompiled_fe_proxy_v1', 'agr_decompiled_fe_proxy_bundle_v1', 'agr_imported_fe_nodes_v1'):
        raise ValueError('Unsupported prepared cloth schema')
    if definition.get('physics') != physics:
        raise ValueError('Prepared cloth physics differs from installed model')
    if not definition.get('source_sha256') or not definition.get('scope'):
        raise ValueError('Prepared cloth must include provenance and coverage scope')
    _validate_bundle(definition, physics)
    if definition.get('schema') == 'agr_imported_fe_nodes_v1':
        from .fe_bone_motion import validate_motion_physics
        validate_motion_physics(physics)
    target = _path(cache, resource, model_sha256, physics)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {'schema_version': 2, 'resource': resource, 'model_sha256': model_sha256,
              'physics_sha256': _digest(physics),
              'definition_sha256': _digest(definition), 'definition': definition}
    # Independent writers must not share a .part name. Both complete records
    # are valid for this exact identity; atomic replacement publishes one.
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix=target.stem+'-',
                                     suffix='.part',dir=target.parent,delete=False) as stream:
        temp=Path(stream.name)
        try:stream.write(json.dumps(record,allow_nan=False))
        except BaseException:
            stream.close();temp.unlink(missing_ok=True);raise
    try:
        for attempt in range(7):
            try:
                temp.replace(target)
                break
            except PermissionError as exc:
                # Concurrent Windows renames/brief scanner handles can deny
                # deletion of the freshly published destination temporarily.
                if getattr(exc,'winerror',None) not in (5,32) or attempt==6:raise
                time.sleep(.01*2**attempt)
    finally:temp.unlink(missing_ok=True)
    return target


def load(cache, resource, model_sha256, physics):
    target = _path(cache, resource, model_sha256, physics)
    if not target.is_file():
        return None
    record = json.loads(target.read_text(encoding='utf-8'))
    definition = record['definition']
    if (record.get('schema_version') != 2 or record.get('resource') != resource
            or record.get('model_sha256') != model_sha256
            or record.get('physics_sha256') != _digest(physics)
            or record.get('definition_sha256') != _digest(definition)
            or definition.get('schema') not in ('agr_decompiled_fe_proxy_v1', 'agr_decompiled_fe_proxy_bundle_v1', 'agr_imported_fe_nodes_v1')
            or definition.get('physics') != physics):
        raise ConversionError('Prepared cloth cache identity/integrity mismatch; regenerate its definition')
    extractor=definition.get('extractor')
    if extractor is not None:
        from .asset_converter import sha
        from . import reconstructed_cloth
        module=Path(reconstructed_cloth.__file__)
        if (not isinstance(extractor,dict) or extractor.get('adapter')!=sha(module)
                or extractor.get('parser')!=sha(module.parent/'vendor/datamodel.py')
                or extractor.get('cloth_modules')!=reconstructed_cloth.cloth_module_hashes()):
            return None
    _validate_bundle(definition, physics)
    if definition.get('schema') == 'agr_imported_fe_nodes_v1':
        from .fe_bone_motion import validate_motion_physics
        validate_motion_physics(physics)
    return definition
