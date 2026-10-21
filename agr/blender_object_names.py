"""Cosmetic actor object names, independent of source rig/material identities."""
from __future__ import annotations

import hashlib
import re


# Compatible with Blender versions that allow 63 UTF-8 bytes per ID name.
NAME_BYTES = 63
ORIGINAL_NAME_PROPERTY = 'agr_original_object_name'
_OBJECT_TYPES = {'MESH', 'ARMATURE', 'EMPTY'}


def _clean(value):
    return ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', str(value)).split())


def actor_label(entity):
    """Prefer the existing offline display catalog; retain unknown hero labels."""
    from .replay import hero_display_name
    from .model_tracks import actor_id

    hero_id = entity.get('hero_id')
    try:
        numeric_id = int(hero_id)
    except (TypeError, ValueError):
        numeric_id = None
    label = hero_display_name(numeric_id) if numeric_id is not None else None
    if label is None or label == f'Hero {numeric_id}':
        label = entity.get('hero_name') or label or hero_id or 'Entity'
    result = _clean(label) + ' [' + _clean(actor_id(entity)) + ']'
    if entity.get('model_track') is not None:
        result += ' | model ' + str(entity['model_track']['start_tick'])
    return result


def bounded_name(value, limit=NAME_BYTES):
    """Keep the hero prefix and part tail, hashing any removed middle text."""
    value = _clean(value)
    encoded = value.encode('utf-8')
    if len(encoded) <= limit:
        return value
    digest = '~' + hashlib.sha256(encoded).hexdigest()[:8] + '~'
    remaining = limit - len(digest)
    left = (remaining + 1) // 2
    return (encoded[:left].decode('utf-8', errors='ignore') + digest
            + encoded[-(remaining - left):].decode('utf-8', errors='ignore'))


def _unique_name(value, occupied):
    candidate = bounded_name(value)
    index = 0
    while candidate in occupied:
        index += 1
        suffix = f'.{index:03d}'
        candidate = bounded_name(value, NAME_BYTES - len(suffix)) + suffix
    occupied.add(candidate)
    return candidate


def object_part(original, object_type):
    """Discard converter directories/model resource while keeping part suffixes."""
    leaf = str(original).replace('\\', '/').rsplit('/', 1)[-1]
    match = re.search(r'\.vmdl(?:_c)?(?=$|[. :|])', leaf, flags=re.IGNORECASE)
    if match:
        leaf = leaf[match.end():].lstrip('. :|')
    return _clean(leaf) or object_type.lower()


def rename_imported_objects(objects, prefix, occupied_names):
    """Rename only supplied actor objects after all source-name consumers finish.

    No mesh/armature datablocks, bones, vertex groups or materials are renamed.
    Sorting original names removes dependence on the importer's unordered sets.
    A temporary pass prevents Blender auto-suffixes when old/new names overlap.
    """
    objects = [obj for obj in objects if obj.type in _OBJECT_TYPES]
    occupied = set(occupied_names)
    external = occupied - {obj.name for obj in objects}
    rows = sorted(((str(obj.get(ORIGINAL_NAME_PROPERTY, obj.name)), obj.name, obj)
                   for obj in objects), key=lambda row: (row[0], row[1]))
    planned = []
    for original, previous, obj in rows:
        target = _unique_name(prefix + ' | ' + object_part(original, obj.type), external)
        planned.append((obj, original, previous, target))
    occupied.update(external)
    for index, (obj, original, previous, target) in enumerate(planned):
        obj[ORIGINAL_NAME_PROPERTY] = original
        obj.name = _unique_name(f'AGR naming temporary {index}', occupied)
    for obj, original, previous, target in planned:
        obj.name = target
    return {previous: obj.name for obj, original, previous, target in planned}


def refresh_secondary_motion_names(report, renamed):
    """Update only cloth report object references; preserve all bone identities."""
    if not isinstance(report, dict):
        return
    if isinstance(report.get('objects'), list):
        report['objects'] = [renamed.get(name, name) for name in report['objects']]
    if isinstance(report.get('proxy'), str):
        report['proxy'] = renamed.get(report['proxy'], report['proxy'])
    for part in report.get('parts', []):
        refresh_secondary_motion_names(part, renamed)
