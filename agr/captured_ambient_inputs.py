"""Bounded snapshots for independently captured ambient sprite/rope components.

Reuse the existing JSON/path/byte-budget primitives, never the slide cohort's
same-run admission. Every original component retains its own capture provenance.
"""
import copy
from pathlib import Path

from . import captured_slide_inputs as transport
from .captured_slide_binding import OWNER, bind_retained_owner
from .replay_capture import canonical_sha256
from .scene_package import validate_scene

FORMAT = 'renderlock-captured-ambient-inputs'
BOUND_FORMAT = 'renderlock-bound-ambient-inputs'
BACKENDS = ('observed_sprite_frames_v1', 'observed_rope_frames_v1')
MAX_DESCRIPTOR_BYTES = transport.MAX_DESCRIPTOR_BYTES
MAX_SOURCE_BYTES = transport.MAX_SOURCE_BYTES
MAX_PACKAGE_BYTES = transport.MAX_PACKAGE_BYTES
MAX_TOTAL_BYTES = transport.MAX_TOTAL_BYTES


def require(ok, reason):
    if not ok:
        raise ValueError('Captured ambient inputs: ' + reason)


def require_options(path, *, simulation, constraints, partial, start_tick, end_tick, history_ticks):
    if path:
        require(simulation and constraints,
                'supplied ambient inputs require installed simulation and --model-constraints')
        require(partial, 'captured renderer/clock scope is incomplete; explicitly select --partial')
        require((start_tick, end_tick, history_ticks) == (17920, 18240, 62),
                'qualified ambient inputs require ticks 17920..18240 and --particle-history-ticks 62')


def read_inputs(path, *, reserved=()):
    """Read descriptor, source and two packages once; never execute input code."""
    try:
        return _read_inputs(path, reserved)
    except ValueError as exc:
        raise ValueError('Captured ambient inputs: ' + str(exc).removeprefix(
            'Captured slide inputs: ')) from exc


def _read_inputs(path, reserved):
    path = Path(path).expanduser().resolve()
    document, receipt = transport._read(path, MAX_DESCRIPTOR_BYTES, reserved, MAX_TOTAL_BYTES)
    transport._keys(document, ('format', 'schema_version', 'source_scene', 'packages'))
    require(document['format'] == FORMAT and type(document['schema_version']) is int
            and document['schema_version'] == 1, 'unsupported descriptor schema')
    rows = document['packages']
    require(type(rows) is list and len(rows) == len(BACKENDS), 'both ambient components are required')
    for row in rows:
        transport._keys(row, ('backend', 'file'))
    require([r['backend'] for r in rows] == list(BACKENDS), 'component set/order differs')
    references = [document['source_scene']] + [row['file'] for row in rows]
    paths = [transport._reference(reference, path.parent) for reference in references]
    require(len(set(paths + [path])) == 4, 'aliased input references')
    for i, left in enumerate(paths):
        require(not any(left.exists() and right.exists() and left.samefile(right)
                        for right in [path] + paths[:i]), 'aliased input files')
    values, files = [], []
    remaining = MAX_TOTAL_BYTES - receipt['bytes']
    for i, (file, reference) in enumerate(zip(paths, references)):
        value, file_receipt = transport._read(file, MAX_SOURCE_BYTES if i == 0 else MAX_PACKAGE_BYTES,
                                              reserved, remaining)
        require(file_receipt['sha256'] == reference['sha256'], 'input byte digest changed')
        remaining -= file_receipt['bytes']
        values.append(value)
        files.append(file_receipt)
    source, packages = values[0], values[1:]
    require(type(source) is dict and type(source.get('schema_version')) is int
            and source['schema_version'] == 1, 'invalid original scene schema')
    validate_scene(source)
    for entity in source['entities']:
        for key in ('model_file', 'skeleton_file', 'pose_file', 'secondary_motion_file'):
            if entity.get(key):
                require(type(entity[key]) is str and '\x00' not in entity[key], 'invalid owner asset path')
                entity[key] = str((paths[0].parent/entity[key]).resolve())
                transport._avoid_outputs(Path(entity[key]), reserved)
    require(all(type(p) is dict and p.get('backend') == name for p,name in zip(packages,BACKENDS)),
            'referenced component backend differs')
    for directory in transport._asset_roots(packages):
        require(type(directory) is str and Path(directory).is_absolute(),
                'original asset directories must be absolute; relocation is not qualified')
        transport._avoid_outputs(Path(directory).resolve(), reserved)
    result = dict(source_scene=source, packages=packages,
                  provenance=dict(descriptor=receipt, inputs=files))
    return dict(result, content_sha256=canonical_sha256(result))


def bind_inputs(loaded, target):
    """Preserve original packages; independently qualify both against the source."""
    transport._keys(loaded, ('source_scene','packages','provenance','content_sha256'))
    require(loaded['content_sha256'] == canonical_sha256({k:v for k,v in loaded.items()
                                                        if k != 'content_sha256'}),
            'loaded input snapshot changed')
    from .simulated_effect_package import validate_package
    packages, source = loaded['packages'], loaded['source_scene']
    require(type(packages) is list and len(packages) == len(BACKENDS)
            and all(type(p) is dict for p in packages)
            and [p.get('backend') for p in packages] == list(BACKENDS),
            'both original ambient components are required in order')
    for package in packages:
        validate_package(package, source)
    # A matching owner does not establish matching capture clocks. No common
    # report/freeze/cleanup assertion is made; each is bound by its reference.
    binding = bind_retained_owner(source, target)
    return dict(format=BOUND_FORMAT, schema_version=1, entity_ids=[OWNER],
        scene_identity=binding['target_scene_identity'], original=copy.deepcopy(loaded), binding=binding,
        coverage_scope='Supplied independent ambient sprite and rope captures; complete frame-local arrays '
            'held between ticks. No shared capture/hero clock, lifetime joins, automatic acquisition, '
            'Source pixel or lighting parity established')


def validate_bound_inputs(bound, target):
    transport._keys(bound, ('format','schema_version','entity_ids','scene_identity',
                            'original','binding','coverage_scope'))
    require(canonical_sha256(bound) == canonical_sha256(bind_inputs(bound['original'], target)),
            'bound ambient inputs or fresh target changed')
