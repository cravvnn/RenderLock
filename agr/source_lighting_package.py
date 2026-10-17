"""Strict identity contract for explicitly selected partial static lighting."""
import hashlib
import json
from pathlib import Path

from .gltf_region import local_resource
from .source_lightmap import SHADER_SHA256


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build_lighting_package(descriptor, assets, manifest_dir, *, allow_partial=False):
    """Bind freshly qualified assets to a region with the same world identity.

    Do not infer partial selection or source world identity from a file name.
    """
    if allow_partial is not True:
        raise ValueError('Static diffuse lighting requires explicit allow_partial selection')
    if assets.get('world_resource') != descriptor.get('world_resource'):
        raise ValueError('Map and lighting world resource mismatch')
    if assets.get('world_sha256') != descriptor.get('source_world_sha256'):
        raise ValueError('Map and lighting world source identity mismatch')
    model = Path(descriptor['model_file'])
    if not model.is_absolute():
        model = Path(manifest_dir)/model
    document = json.loads(model.read_text(encoding='utf-8'))
    resources = {row['uri'] for kind in ('buffers','images')
                 for row in document.get(kind,[]) if 'uri' in row}
    package = dict(schema_version=1, mode='static_diffuse', allow_partial=True,
                   shader_sha256=assets['shader_sha256'], world_sha256=assets['world_sha256'],
                   gltf_sha256=file_sha(model),
                   map_files={uri:file_sha(local_resource(model.parent,uri)) for uri in sorted(resources)},
                   lightmaps=assets['lightmaps'])
    if 'brdf' in assets:
        package['brdf'] = assets['brdf']
    validate_lighting_package(package,descriptor,model,manifest_dir)
    return package


def validate_lighting_package(package, descriptor, model_path, manifest_dir):
    if (not isinstance(package, dict) or type(package.get('schema_version')) is not int
            or package.get('schema_version') != 1):
        raise ValueError('Unsupported Source lighting package schema')
    if package.get('mode') != 'static_diffuse' or package.get('allow_partial') is not True:
        raise ValueError('Static diffuse lighting requires explicit allow_partial selection')
    if package.get('shader_sha256') != SHADER_SHA256:
        raise ValueError('Source lighting shader is not qualified')
    world_hash = package.get('world_sha256')
    if (not isinstance(world_hash,str) or len(world_hash)!=64 or any(c not in '0123456789abcdef' for c in world_hash)
            or world_hash != descriptor.get('source_world_sha256')):
        raise ValueError('Map and lighting world source identity mismatch')
    model_path = Path(model_path)
    if file_sha(model_path) != package.get('gltf_sha256'):
        raise ValueError('Source lighting map geometry identity mismatch')
    document = json.loads(model_path.read_text(encoding='utf-8'))
    resources = {row['uri'] for kind in ('buffers','images') for row in document.get(kind,[]) if 'uri' in row}
    files = package.get('map_files')
    if not isinstance(files,dict) or set(files) != resources:
        raise ValueError('Source lighting map dependency inventory mismatch')
    for uri, digest in files.items():
        if file_sha(local_resource(model_path.parent,uri)) != digest:
            raise ValueError(f'Source lighting map dependency changed: {uri}')
    lightmaps = package.get('lightmaps')
    if not isinstance(lightmaps,dict) or set(lightmaps) != {'DC','R','G','B'}:
        raise ValueError('Source lighting requires all four qualified SH textures')
    resolved = {}
    for name,row in lightmaps.items():
        path = Path(row['path'])
        if not path.is_absolute():
            path = Path(manifest_dir)/path
        if file_sha(path) != row.get('sha256'):
            raise ValueError(f'Source lightmap changed: {name}')
        if row.get('size') != [8192,8192] or type(row.get('layer')) is not int or row.get('layer') != 0:
            raise ValueError('Unqualified Source lightmap size/layer')
        resolved[name] = path.resolve()
    if 'brdf' in package:
        from .source_brdf_assets import validate_brdf_asset
        resolved['BRDF'] = validate_brdf_asset(package['brdf'],manifest_dir)
    return resolved
