"""Read once, then bind qualified observations to fresh export inputs.

This is an input transport, not a game reader or authenticity proof. The
Crossbow validator still owns module, replay, birth, model and graph admission.
No observations means unavailable, never a claim that an effect does not exist.
"""
import hashlib
import json
import math
from pathlib import Path
import re

FORMAT = 'renderlock-particle-observations'
MAX_BYTES = 1024 * 1024
MAX_CAPTURES = 64
CAPTURE_FIELDS = frozenset('schema_version policy client_sha256 particles_sha256 resource entity_id tick '
    'demo_sha256 raw_capture_sha256 model model_sha256 coherent identity_flags model_method '
    'runtime_owner_handle collection_owner_handle skeleton_vtable_rva body_component_type_index '
    'body_component_offset owner_srt runtime_axes_rvas runtime_axes_raw runtime_axes_coherent '
    'graph_coherence_policy graph_coherent child_model_inputs_complete eligible_render_children '
    'graph_nodes graph_sha256 cp0_record collection_record'.split())


def require_options(path, *, simulation, constraints):
    if path and not (simulation and constraints):
        raise ValueError('Particle observations require installed particle simulation and model bone rules (--model-constraints). Clear the observation file to export without it.')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Particle observations contain duplicate JSON keys')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Particle observations contain a non-finite JSON number')


def read_observations(path, *, reserved=()):
    """Return one bounded in-memory snapshot, with no later file reads."""
    source = Path(path).expanduser().resolve()
    for item in reserved:
        target = Path(item).resolve()
        if (source == target or source.is_relative_to(target)
                or source.exists() and target.exists() and source.samefile(target)):
            raise ValueError('Particle observation input collides with the replay or an export output')
    with source.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Particle observation file exceeds 1 MiB')
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid particle observation JSON') from exc
    pending = [(document, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > 10000 or isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Particle observations exceed bounded finite JSON values')
        if isinstance(value, dict):
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
    if (not isinstance(document, dict)
            or set(document) != {'format', 'schema_version', 'captures'}
            or document['format'] != FORMAT
            or type(document['schema_version']) is not int or document['schema_version'] != 1
            or not isinstance(document['captures'], list)
            or not 1 <= len(document['captures']) <= MAX_CAPTURES):
        raise ValueError('Particle observations require schema 1 and 1..64 captures')
    captures = {}
    for row in document['captures']:
        if (not isinstance(row, dict) or set(row) != {'instance_id', 'capture'}
                or not isinstance(row['instance_id'], str)
                or re.fullmatch(r'(0|[1-9][0-9]{0,9}):[1-9][0-9]{0,9}', row['instance_id']) is None
                or not isinstance(row['capture'], dict) or set(row['capture']) != CAPTURE_FIELDS
                or row['instance_id'] in captures):
            raise ValueError('Particle observations contain invalid or duplicate instance records')
        captures[row['instance_id']] = row['capture']
    return dict(captures=captures, source=str(source), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def bind_observations(document, scene):
    """All supplied rows must bind; never return a partly accepted batch."""
    from .particle_crossbow_owner import bind_capture
    instances = {}
    for row in scene.get('particle_instances', []):
        key = row['id']
        if key in instances:
            raise ValueError('Particle observation target identity is ambiguous')
        instances[key] = row
    bound = {}
    for key, capture in document['captures'].items():
        if key not in instances:
            raise ValueError(f'Particle observation {key} is absent from the selected replay history')
        bound[key] = bind_capture(capture, scene, instances[key])
    receipt = dict(schema_version=1, format=FORMAT, sha256=document['sha256'],
                   bytes=document['bytes'], instance_ids=sorted(bound),
                   status='imported_qualified_observations', acquisition='supplied_file',
                   scope='Imported observations do not establish complete particle coverage')
    return bound, receipt
