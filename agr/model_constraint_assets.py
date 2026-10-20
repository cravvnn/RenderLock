"""Small source-bound authored-constraint metadata, independent of model conversion."""
import json
from pathlib import Path
import subprocess

from .asset_converter import ConversionError, compiled, sha
from .effect_assets import EffectAssetAdapter
from .game_assets import safe_resource_path
from .map_text import parse_map_kv3
from .model_constraints import ModelConstraints


def prepare_model_constraints(decoder, resource, provider):
    resource = safe_resource_path(compiled(resource))
    resolved = provider.resolve(resource)
    package = Path(__file__).parent
    dependencies = {name: sha(package / name) for name in (
        'model_constraint_assets.py', 'model_constraints.py', 'model_constraint_math.py',
        'map_text.py', 'effect_assets.py', 'installed_map.py', 'vendor/pose_math.py')}

    def validate(spec):
        if (spec.get('schema_version') != 1 or spec.get('resource') != resource
                or spec.get('resource_sha256') != resolved.sha256
                or spec.get('binding_policy') != 'native_disabled_missing_slave_v1'
                or spec.get('preparation_dependencies') != dependencies):
            raise ConversionError('Authored constraint source/preparation identity mismatch')
        if 'attachments' in spec and spec.get('attachment_source') != {
                'resource': resource, 'sha256': resolved.sha256}:
            raise ConversionError('Authored constraint attachment source identity mismatch')
        ModelConstraints(spec['names'], spec['parents'], spec['definitions'],
                         attachments=spec.get('attachments'),binding_policy=spec['binding_policy'])

    def build(temp):
        proc = subprocess.run([str(decoder.executable), '-i', str(resolved.local_path), '-b', 'DATA'],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        if proc.returncode:
            raise ConversionError('Model constraint DATA decode failed: ' + proc.stderr[-2000:])
        data = parse_map_kv3(proc.stdout)
        skeleton = data['m_modelSkeleton']
        keys = parse_map_kv3(data['m_modelInfo']['m_keyValueText'])
        spec = dict(schema_version=1, names=skeleton['m_boneName'], parents=skeleton['m_nParent'],
                    definitions=keys.get('BoneConstraintList', []), resource=resource,
                    resource_sha256=resolved.sha256, preparation_dependencies=dependencies,
                    binding_policy='native_disabled_missing_slave_v1')
        if any(record.get('m_bIsAttachment') for definition in spec['definitions']
               for record in (*definition['m_slaves'], *definition['m_targets'])):
            spec['attachments'] = EffectAssetAdapter(decoder).attachments(resource, provider)
            spec['attachment_source'] = {'resource': resource, 'sha256': resolved.sha256}
        validate(spec)
        (temp / 'constraints.json').write_text(json.dumps(spec, indent=2) + '\n', encoding='utf-8')

    dest = decoder._entry('model_constraints_v2', {resource: resolved.sha256}, dependencies, build)
    result = json.loads((dest / 'constraints.json').read_text(encoding='utf-8'))
    validate(result)
    return result
