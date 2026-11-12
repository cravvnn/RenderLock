"""Bounded, read-only transport for one retained four-component slide cohort.

File digests identify supplied bytes; they do not authenticate a capture. The
original packages and scene identity remain unchanged. Fresh owner admission
and every existing native/frame/material validator still run before binding.
No game reader, file writer, cache, renderer or export-success policy lives here.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import re

from .captured_slide_binding import OWNER, bind_retained_owner, validate_retained_binding
from .replay_capture import canonical_sha256
from .scene_package import validate_scene

FORMAT = 'renderlock-captured-slide-inputs'
BOUND_FORMAT = 'renderlock-bound-slide-cohort'
BACKENDS = tuple('observed_slide_' + name + '_frames_v1'
                 for name in ('sprite', 'trail', 'light', 'model'))
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 16 * 1024**2
MAX_PACKAGE_BYTES = 8 * 1024**2
MAX_TOTAL_BYTES = 32 * 1024**2
MAX_NODES = 1_000_000
MAX_DEPTH = 64
COMMON_PROVENANCE = ('report_sha256', 'observer_sha256', 'field_reader_sha256',
                     'freeze_sha256', 'cleanup_sha256')


def require(value, reason):
    if not value:
        raise ValueError('Captured slide inputs: ' + reason)


def _keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'unexpected or missing fields')


def _object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate JSON key')
        value[key] = item
    return value


def _constant(_):
    raise ValueError('Captured slide inputs: non-finite JSON number')


def _decode(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('Captured slide inputs: invalid JSON') from exc
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= MAX_DEPTH and count <= MAX_NODES
                and not (type(item) is float and not math.isfinite(item)),
                'JSON depth, node or finite-number budget exceeded')
        if type(item) is dict:
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
    return value


def _avoid_outputs(source, reserved):
    for item in reserved:
        target = Path(item).resolve()
        require(not (source == target or source.is_relative_to(target)
                    or source.is_dir() and target.is_relative_to(source)
                    or source.exists() and target.exists() and source.samefile(target)),
                'input collides with replay or reserved output')


def _read(path, maximum, reserved, remaining):
    path = Path(path).resolve()
    _avoid_outputs(path, reserved)
    with path.open('rb') as stream:
        raw = stream.read(min(maximum, remaining) + 1)
    require(0 < len(raw) <= maximum and len(raw) <= remaining, 'file or aggregate byte budget exceeded')
    return _decode(raw), dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def _reference(value, base):
    _keys(value, ('path', 'sha256'))
    require(type(value['path']) is str and value['path'] and '\x00' not in value['path']
            and type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256']),
            'invalid file reference')
    path = Path(value['path'])
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _asset_roots(packages):
    # Original asset metadata is hashed by the component validators. Do not
    # rewrite it (and rehash a capture) merely to resolve a different directory.
    for package in packages:
        assets = package.get('assets', {})
        require(type(assets) is dict and all(type(assets.get(key, {})) is dict
                                            for key in ('models', 'textures')),
                'invalid asset mapping')
        for model in assets.get('models', {}).values():
            require(type(model) is dict and type(model.get('material_bundle')) is dict
                    and 'directory' in model and 'path' in model['material_bundle'],
                    'invalid original model asset paths')
            yield model['directory']
            yield model['material_bundle']['path']
        for texture in assets.get('textures', {}).values():
            require(type(texture) is dict and 'directory' in texture,
                    'invalid original texture asset path')
            yield texture['directory']
            if 'source_sheet' in texture:
                require(type(texture['source_sheet']) is dict and 'directory' in texture['source_sheet'],
                        'invalid original sheet asset path')
                yield texture['source_sheet']['directory']


def read_cohort(path, *, reserved=()):
    """Snapshot five referenced JSON files once, without executing any input.

    The descriptor may use relative paths. Original asset directories currently
    require absolute paths; portable asset relocation remains a separate gate.
    """
    path = Path(path).expanduser().resolve()
    document, receipt = _read(path, MAX_DESCRIPTOR_BYTES, reserved, MAX_TOTAL_BYTES)
    _keys(document, ('format', 'schema_version', 'source_scene', 'packages'))
    require(document['format'] == FORMAT and type(document['schema_version']) is int
            and document['schema_version'] == 1, 'unsupported descriptor schema')
    require(type(document['packages']) is list and len(document['packages']) == 4,
            'all four component references are required')
    source_path = _reference(document['source_scene'], path.parent)
    rows = document['packages']
    for row in rows:
        _keys(row, ('backend', 'file'))
    require([r['backend'] for r in rows] == list(BACKENDS), 'component set/order differs')
    paths = [source_path] + [_reference(row['file'], path.parent) for row in rows]
    require(len(set(paths + [path])) == 6, 'aliased input references')
    for i, left in enumerate(paths):
        require(not any(left.exists() and right.exists() and left.samefile(right)
                        for right in [path] + paths[:i]), 'aliased input files')
    files, values = [], []
    remaining = MAX_TOTAL_BYTES - receipt['bytes']
    references = [document['source_scene']] + [r['file'] for r in rows]
    for i, (file, reference) in enumerate(zip(paths, references)):
        value, file_receipt = _read(file, MAX_SOURCE_BYTES if i == 0 else MAX_PACKAGE_BYTES,
                                    reserved, remaining)
        require(file_receipt['sha256'] == reference['sha256'], 'input byte digest changed')
        remaining -= file_receipt['bytes']
        files.append(file_receipt)
        values.append(value)
    source, packages = values[0], values[1:]
    require(type(source) is dict and type(source.get('schema_version')) is int
            and source['schema_version'] == 1, 'invalid original scene schema')
    validate_scene(source)
    for entity in source['entities']:
        for key in ('model_file', 'skeleton_file', 'pose_file', 'secondary_motion_file'):
            if entity.get(key):
                require(type(entity[key]) is str and '\x00' not in entity[key],
                        'invalid original owner asset path')
                entity[key] = str((source_path.parent / entity[key]).resolve())
                _avoid_outputs(Path(entity[key]), reserved)
    require(all(type(p) is dict and p.get('backend') == name for p, name in zip(packages, BACKENDS)),
            'referenced component backend differs')
    for directory in _asset_roots(packages):
        require(type(directory) is str and Path(directory).is_absolute(),
                'original asset directories must be absolute; relocation is not qualified')
        _avoid_outputs(Path(directory).resolve(), reserved)
    result = dict(source_scene=source, packages=packages,
                  provenance=dict(descriptor=receipt, inputs=files))
    return dict(result, content_sha256=canonical_sha256(result))


def _validate_members(packages, source):
    from .simulated_effect_package import validate_package
    require(type(packages) is list and len(packages) == 4
            and all(type(p) is dict for p in packages)
            and [p.get('backend') for p in packages] == list(BACKENDS),
            'all four original components are required in order')
    for package in packages:
        validate_package(package, source)
    sprite, trail, light, model = [p['reference']['capture'] for p in packages]
    require(canonical_sha256(trail['sprite_reference']) == canonical_sha256(packages[0]['reference']),
            'trail refers to a different sprite capture')
    require(all(c['source']['report_sha256'] == sprite['source']['report_sha256']
                for c in (trail, light, model)), 'mixed capture reports')
    require(all(c['source'][key] == sprite['source'][key]
                for c in (light, model) for key in COMMON_PROVENANCE),
            'mixed capture reader, freeze or cleanup provenance')
    require([(f['tick'], f['root_count']) for f in sprite['samples']]
            == [(f['tick'], f['root_count']) for f in light['frames']]
            == [(f['tick'], f['root_count']) for f in model['frames']],
            'component frame/root coverage differs')


def bind_cohort(loaded, target):
    """Bind one complete snapshot; return no partly accepted subset."""
    _keys(loaded, ('source_scene', 'packages', 'provenance', 'content_sha256'))
    require(loaded['content_sha256'] == canonical_sha256({k: v for k, v in loaded.items()
                                                        if k != 'content_sha256'}),
            'loaded input snapshot changed')
    binding = bind_retained_owner(loaded['source_scene'], target)
    _validate_members(loaded['packages'], loaded['source_scene'])
    return dict(format=BOUND_FORMAT, schema_version=1, entity_ids=[OWNER],
                scene_identity=binding['target_scene_identity'],
                original=copy.deepcopy(loaded), binding=binding,
                coverage_scope='Supplied complete frame-local slide arrays; no automatic capture, '
                               'lifetime joins, Source clock/pixel or lighting parity established')


def validate_bound_cohort(bound, target):
    _keys(bound, ('format', 'schema_version', 'entity_ids', 'scene_identity',
                  'original', 'binding', 'coverage_scope'))
    require(canonical_sha256(bound) == canonical_sha256(bind_cohort(bound['original'], target)),
            'bound cohort or fresh target changed')
    validate_retained_binding(bound['binding'], bound['original']['source_scene'], target)
