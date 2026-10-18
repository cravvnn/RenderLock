"""Verified AG2 recipes to reusable per-entity skeletal animation arrays.

The primary mode reads newly extracted state next to the segment. An explicit
``metadata.animation_source = 'verified-cache'`` mode can reuse named recipes
only for the exact replay and range covered by their provenance report.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import numpy as np

from .vendor.decode_pose_recipes import parse
from .vendor.evaluate_recipes import Evaluator
from .vendor.inspect_recipe_headers import recipe_header
from .vendor.resource_hash import resource_id


class AnimationError(ValueError):
    """An unsupported or unverifiable animation input; never invent a pose."""

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = dict(details) if details else {}

TASKS = {
    'CNmCachedPoseWriteTask': 0, 'CNmSampleTask': 1, 'CNmCachedPoseReadTask': 5,
    'CNmTwoBoneIKTask': 4, 'CNmBlendTask': 6,
    'CNmAdditiveBlendTask': 7, 'CNmOverlayBlendTask': 8,
    'CNmModelSpaceBlendTask': 9, 'CNmFollowBoneTask': 11,
    'CNmZeroPoseTask': 12,
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _field(row: dict, suffix: str, default: Any = ...) -> Any:
    values = [v for k, v in row.get('fields', {}).items()
              if k == suffix or k.endswith('.' + suffix)]
    if not values and default is not ...:
        return default
    if len(values) != 1:
        raise AnimationError(f'Missing or ambiguous animation field {suffix}')
    return values[0]


def _inside(root: Path, resource: str, suffix: str = '') -> Path:
    path = (root / (resource + suffix)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AnimationError(f'Invalid resource path: {resource}')
    return path


def _catalog(assets: Path) -> dict:
    for path in [assets/'animation_catalog.json', assets/'mapping/hero_lookup_tables.json']:
        if path.is_file():
            data = _read(path)
            return data.get('heroes', data)
    raise AnimationError('No animation_catalog.json or mapping/hero_lookup_tables.json '
                         'in the asset pack; a recorded graph-to-skeleton mapping is required.')


def _skeleton(assets: Path, entry: dict, name: str) -> dict:
    candidates = [_inside(assets/'clip_cache', entry['skeleton'], '.json')]
    # Legacy exported packs store the same skeleton by hero display name.
    if re.fullmatch(r'[A-Za-z0-9_ -]+', name):
        candidates.append(assets/'data/skeletons'/f'{name}_skeleton.json')
    for path in candidates:
        if path.is_file():
            skeleton = _read(path)
            names = skeleton['m_boneIDs']; parents = skeleton['m_parentIndices']
            if not names or len(set(names)) != len(names) or len(parents) != len(names):
                raise AnimationError('Invalid or duplicate skeleton bones')
            if any(p < -1 or p >= i for i, p in enumerate(parents)):
                raise AnimationError('Skeleton must be ordered with each parent before its children')
            if parents[0] != -1 or any(p < 0 for p in parents[1:]):
                raise AnimationError('Only single-root animation skeletons are supported')
            return skeleton
    raise AnimationError(f'Missing decoded skeleton {entry["skeleton"]}')


class _TableTimeline:
    """Apply recorded changes before each recipe, never use the final LUT early."""
    def __init__(self, table: dict):
        from .vendor.extract_animation_tables import parse_asset
        self.parse_asset = parse_asset
        if not table.get('initial_tables') or not table.get('snapshot_checks'):
            raise AnimationError('Animation tables lack initial state or snapshot verification')
        if not all(r['matches_sequential_stream'] for r in table['snapshot_checks']):
            raise AnimationError('Animation table snapshots do not match the replay stream')
        if any(t >= 0 for t in table.get('table_clears', [])):
            raise AnimationError('Mid-replay animation string-table reset is not supported')
        self.state = {k: {r['index']: dict(r) for r in v}
                      for k, v in table['initial_tables'].items()}
        self.changes = sorted(table.get('animation_table_updates', []), key=lambda r: r['tick'])
        self.cursor = 0
        self.tick = -float('inf')
        self.cached = None

    def at(self, tick: int) -> tuple[dict, list[str]]:
        if tick < self.tick:
            raise AnimationError('Recipe input is not ordered by replay tick')
        self.tick = tick
        while self.cursor < len(self.changes) and self.changes[self.cursor]['tick'] <= tick:
            change = self.changes[self.cursor]
            self.state.setdefault(change['table'], {})[change['index']] = {
                k: change[k] for k in ['index', 'name', 'data_hex']}
            self.cursor += 1
            self.cached = None
        if self.cached is not None:
            return self.cached
        tasks = self.state.get('AnimTaskTypes', {})
        if sorted(tasks) != list(range(len(tasks))) or not tasks:
            raise AnimationError('Animation task registry is empty or sparse')
        assets = {r['name']: self.parse_asset(r)
                  for r in self.state.get('AnimAssetData', {}).values()}
        self.cached = assets, [tasks[i]['name'] for i in range(len(tasks))]
        return self.cached


def _identity(segment: dict, demo: Path | None) -> str:
    metadata = segment.get('metadata', {})
    expected = metadata.get('demo_sha256') or segment.get('demo_sha256')
    actual = _hash(demo) if demo else expected
    if expected and actual != expected:
        raise AnimationError('Replay SHA256 does not match the segment provenance')
    if not actual:
        raise AnimationError('Segment is missing metadata.demo_sha256; replay identity is required')
    return actual


def _load_table(segment: dict, directory: Path, demo: Path | None, digest: str) -> dict:
    path = directory/'animation_tables.json'
    if path.is_file():
        table = _read(path)
        if table.get('demo_sha256') != digest:
            raise AnimationError('Cached animation tables belong to a different replay')
        if table.get('end_tick', -1) < segment['end_tick']:
            raise AnimationError('Cached animation tables do not cover the requested end tick')
        return table
    if demo is None:
        raise AnimationError('Fresh recipes need the original --demo or matching '
                             'animation_tables.json beside the segment')
    try:
        from .vendor.extract_animation_tables import extract
    except ImportError as error:
        raise AnimationError('Reading replay animation tables requires python-snappy') from error
    table = extract(demo, segment['end_tick'])
    table['demo_sha256'] = digest
    return table


def _row_entity(row: dict) -> str | None:
    for key in ['entity_id', 'id', 'entity_index']:
        if row.get(key) is not None:
            return str(row[key])
    return None


def _select_rows(raw: list[dict], entity: dict, entities: list[dict]) -> list[dict]:
    hero = entity['hero_id']; identity = str(entity['id'])
    candidates = [r for r in raw if r.get('hero_id') == hero]
    exact = [r for r in candidates if _row_entity(r) == identity]
    if exact:
        return exact
    # Legacy recordings retain entity_index, while caller IDs may be descriptive.
    # Permit the fallback only when both sides identify exactly one entity.
    ids = {_row_entity(r) for r in candidates}
    if len([e for e in entities if e['hero_id'] == hero]) == 1 and len(ids) <= 1:
        return candidates
    raise AnimationError(f'Cannot uniquely map recipe rows to entity {identity} (hero {hero})')


def _trim_prehistory(rows: list[dict], first_tick: int, last_tick: int,
                     server_ticks: dict | None = None) -> list[dict]:
    """Retain the fresh recipe that seeds the range, including a stale start.

    V3 can read poses cached by earlier recipes, so retain all supplied V3
    history. Extraction does not yet recover arbitrary cache writes before the
    requested range; the evaluator rejects reads without an observed write.
    Table state replays its complete update history independently.
    """
    rows = sorted((r for r in rows if r['demo_tick'] <= last_tick), key=lambda r: r['demo_tick'])
    if any(r.get('version') == 3 or any(t.get('type_id') == 5 for t in r.get('tasks', [])) or any(
            k.endswith('m_nSerializePoseRecipeVersionAG2') and v == 3
            for k, v in r.get('fields', {}).items()) for r in rows):
        return rows
    latest = 0
    for index, row in enumerate(rows):
        if row['demo_tick'] > first_tick:
            break
        if server_ticks is None:
            fresh = row['fresh_recipe']
        else:
            payload = row.get('serialized_pose_recipe_array', [])
            fresh = len(payload) >= 4 and server_ticks.get(row['demo_tick']) == int.from_bytes(
                bytes(payload[:4]), 'little')
        if fresh:
            latest = index
    return rows[latest:]


def _require_cached_clips(rows: list[dict], cache: Path) -> None:
    clips = sorted({t['clip_resource'] for r in rows if r['fresh_recipe']
                    for t in r['tasks'] if 'clip_resource' in t})
    missing = []
    for clip in clips:
        absent = [suffix for suffix in ['.json', '.npz']
                  if not _inside(cache, clip, suffix).is_file()]
        if absent:
            missing.append(clip + ' [' + ', '.join(absent) + ']')
    if missing:
        raise AnimationError(f'Missing decoded animation clips ({len(missing)}): '
                             + '; '.join(missing) + '. Build/import these clip cache resources first.')


def _bone_name_aliases(recorded_bones: list[str], asset_bones: list[str], *,
                       component: str = 'bones', context: dict | None = None) -> list[dict]:
    """Accept only unambiguous ASCII-case spelling changes at the same index.

    Recipe bone indices keep their original meaning. This does not reorder or
    rename the installed skeleton, retarget poses, or establish build equality.
    The same indexed ASCII-case check is used separately for mask names.
    """
    ascii_lower = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')

    def diagnostic_name(name):
        return name.translate(ascii_lower) if isinstance(name, str) else name

    def invalid(reason, index, *, side=None, conflicting_index=None):
        details = dict(context or {})
        details.update(code='animation_skeleton_mismatch', component=component, reason=reason,
                       recorded_count=len(recorded_bones), asset_count=len(asset_bones),
                       first_index=index,
                       recorded_name=recorded_bones[index] if index < len(recorded_bones) else None,
                       asset_name=asset_bones[index] if index < len(asset_bones) else None)
        if side is not None:
            details['side'] = side
        if conflicting_index is not None:
            details['conflicting_index'] = conflicting_index
        explanation = {
            'count': 'counts differ', 'order': 'names or order differ',
            'invalid': 'an empty or non-text name is invalid',
            'ambiguous': 'names collide when ignoring ASCII letter case',
        }[reason]
        location = f"; skeleton {details['skeleton']}" if 'skeleton' in details else ''
        if 'graph' in details:
            location += f"; graph {details['graph']}"
        if 'demo_tick' in details:
            location += f"; replay tick {details['demo_tick']}"
        return AnimationError(
            'Asset skeleton bone or mask order differs from the replay: '
            f'{component} {explanation} (replay {len(recorded_bones)}, installed {len(asset_bones)}; '
            f'index {index}: replay {details["recorded_name"]!r}, installed {details["asset_name"]!r}'
            f'{location}). The recorded animation cannot be safely matched to these installed assets. '
            'For compatibility checking, share failure.json and animation_asset_report.json '
            'from the retained diagnostic folder, plus the replay ID and tick range.',
            details=details)

    if len(recorded_bones) != len(asset_bones):
        index = next((i for i, (recorded, asset) in enumerate(zip(recorded_bones, asset_bones))
                      if diagnostic_name(recorded) != diagnostic_name(asset)),
                     min(len(recorded_bones), len(asset_bones)))
        raise invalid('count', index)
    normalized = []
    for side, names in [('recorded', recorded_bones), ('asset', asset_bones)]:
        for index, name in enumerate(names):
            if not isinstance(name, str) or not name:
                raise invalid('invalid', index, side=side)
        folded = [name.translate(ascii_lower) for name in names]
        seen = {}
        for index, name in enumerate(folded):
            if name in seen:
                raise invalid('ambiguous', seen[name], side=side, conflicting_index=index)
            seen[name] = index
        normalized.append(folded)
    if normalized[0] != normalized[1]:
        index = next(i for i, (recorded, asset) in enumerate(zip(*normalized)) if recorded != asset)
        raise invalid('order', index)
    return [{'index': index, 'recorded': recorded, 'asset': asset}
            for index, (recorded, asset) in enumerate(zip(recorded_bones, asset_bones))
            if recorded != asset]


def _bind(raw: dict, tick: int, recorded: dict, registry: list[str],
          entry: dict, skeleton: dict) -> dict:
    graph = entry['graph']
    if int(_field(raw, 'm_primaryGraphId')) != resource_id(graph):
        raise AnimationError(f'Recorded primary graph does not match {graph}')
    for suffix in ['m_vecExternalClipIds', 'm_vecExternalGraphIds',
                   'm_vecSecondarySkeletons']:
        value = _field(raw, suffix, 0)
        if value not in [0, [], None]:
            raise AnimationError(f'Unsupported animation context: {suffix} is nonempty')
    if graph not in recorded or entry['skeleton'] not in recorded:
        raise AnimationError(f'Replay tables do not contain graph/skeleton {graph}')
    graph_asset = recorded[graph]; sk_asset = recorded[entry['skeleton']]
    bones = skeleton['m_boneIDs']; masks = [m['m_ID'] for m in skeleton['m_maskDefinitions']]
    context = {'skeleton': entry['skeleton'], 'graph': graph,
               'demo_tick': raw['demo_tick'], 'server_tick': tick}
    aliases = _bone_name_aliases(sk_asset['bones'], bones, context=context)
    mask_aliases = _bone_name_aliases(sk_asset['masks'], masks, component='masks', context=context)
    lut = graph_asset['resources']
    if not lut or len(set(lut)) != len(lut):
        raise AnimationError('Recorded graph resource list is empty or ambiguous')
    dependency_counts = {0: 1, 1: 0, 4: 1, 5: 0, 6: 2, 7: 2, 8: 2, 9: 2, 11: 1, 12: 0}
    header = recipe_header(raw, tick, task_type_bits=len(registry).bit_length(),
                           topology_dependency_counts={i: dependency_counts[TASKS[name]]
                               for i, name in enumerate(registry)
                               if name in TASKS and TASKS[name] in dependency_counts})
    if header['recipe_age_ticks'] < 0:
        raise AnimationError('Recipe update tick is in the future relative to the server tick')
    numeric = header['candidate_task_type_ids_4bit']
    classes = []
    for type_id in numeric:
        if type_id >= len(registry):
            raise AnimationError(f'Animation type ID {type_id} exceeds the recorded registry')
        name = registry[type_id]
        if name not in TASKS:
            raise AnimationError(f'Unsupported recorded animation operation: {name} (ID {type_id})')
        classes.append(name)
    # The bounded parser uses canonical operation IDs internally; names from this
    # replay control the dispatch even if the registry order changes in a build.
    header['candidate_task_type_ids_4bit'] = [TASKS[name] for name in classes]
    row = parse(header, resource_bits=len(lut).bit_length(),
                mask_bits=len(masks).bit_length(), bone_bits=len(bones).bit_length())
    if not row['complete_layout'] or row['error'] or row['unsupported']:
        raise AnimationError(f'AG2 payload at tick {raw["demo_tick"]}: '
                             f'{row["error"] or row["unsupported"] or "incomplete layout"}')
    row.update(version=header['version'], fresh_recipe=header['recipe_age_ticks'] == 0,
               recipe_update_tick=header['recipe_update_tick'], graph_resource=graph,
               context=[_field(raw, 'm_nServerGraphInstanceIteration'),
                        _field(raw, 'm_nServerSerializationContextIteration')])
    if aliases:
        row['bone_name_aliases'] = aliases
    if mask_aliases:
        row['mask_name_aliases'] = mask_aliases
    for index, task in enumerate(row['tasks']):
        task['task_class'] = classes[index]
        task['recorded_type_id'] = numeric[index]
        if task['type_id'] == 1:
            slot = task['resource_index']
            if slot >= len(lut) or not lut[slot].endswith('.vnmclip'):
                raise AnimationError(f'Invalid animation clip lookup slot {slot}')
            task['clip_resource'] = lut[slot]
        for key in ['effector_bone_index', 'target_bone_index', 'bone_index', 'follow_bone_index']:
            if key in task and not 0 <= task[key] < len(bones):
                raise AnimationError(f'Bone index outside skeleton: {key}')
        for mask in task.get('masks', []):
            if 'mask_index' in mask and mask['mask_index'] >= len(masks):
                raise AnimationError('Mask index outside recorded skeleton')
        if task['type_id'] == 4 and task.get('extension_flag_a'):
            value = task['extension_value_9bit']
            task.update(extension_mode_bit=value & 1, extension_weight_01=(value >> 1)/255)
    return row


def _cached_rows(segment: dict, assets: Path, digest: str) -> list[dict]:
    validation_path = assets/'mapping/mapping_validation.json'
    recipe_path = assets/'mapping/named_pose_recipes.json'
    validation = _read(validation_path)
    if validation.get('mapping_validation') != 'PASS' or validation.get('demo_sha256') != digest:
        raise AnimationError('Verified recipe cache is not validated for this replay SHA256')
    if segment['start_tick'] < validation['start_tick'] or segment['end_tick'] > validation['end_tick']:
        raise AnimationError('Requested range is outside the verified recipe cache coverage')
    expected = validation.get('recipes_sha256')
    package_hashes = assets/'package_file_hashes.json'
    if package_hashes.is_file():
        hashes = _read(package_hashes)
        for rel in ['mapping/mapping_validation.json', 'mapping/hero_lookup_tables.json',
                    'mapping/named_pose_recipes.json']:
            expected_file = hashes.get(rel, {}).get('sha256')
            if not expected_file or _hash(assets/rel) != expected_file:
                raise AnimationError(f'Verified cache checksum mismatch: {rel}')
        expected = hashes['mapping/named_pose_recipes.json']['sha256']
    if not expected or _hash(recipe_path) != expected:
        raise AnimationError('Verified recipe cache requires a matching recipes_sha256 '
                             'or original package_file_hashes.json')
    rows = _read(recipe_path)
    for row in rows:
        for task in row['tasks']:
            if TASKS.get(task.get('task_class')) != task['type_id']:
                raise AnimationError('Verified cache task class/ID mismatch')
    return rows


def evaluate_segment(segment_path: Path, output: Path, assets: Path,
                     demo: Path | None = None, strict: bool = True) -> Path:
    """Create a segment manifest enriched with pose_file and skeleton_file.

    In strict mode unsupported entities raise AnimationError. With strict=False,
    supported entities are baked and each excluded entity receives a diagnostic.
    Positions are not altered. Poses remain in Source parent-local coordinates;
    the Blender stage performs the agreed coordinate and model-facing transform.
    """
    segment_path = Path(segment_path).resolve(); output = Path(output).resolve()
    assets = Path(assets).resolve(); demo = Path(demo).resolve() if demo else None
    segment = _read(segment_path)
    if segment.get('schema_version', 1) != 1:
        raise AnimationError('Unsupported segment schema version')
    digest = _identity(segment, demo)
    source = segment.get('metadata', {}).get('animation_source', 'replay')
    if source not in ['replay', 'verified-cache']:
        raise AnimationError(f'Unknown animation source: {source}')
    catalog = _catalog(assets)
    graph_catalog = {v['graph']: (k, v) for k, v in catalog.items()}
    raw = []; table = None; server_ticks = {}
    if source == 'verified-cache':
        rows = _cached_rows(segment, assets, digest)
    else:
        raw_path = segment_path.parent/'raw_animation_state.json'
        tick_path = segment_path.parent/'network_ticks.json'
        if not raw_path.is_file() or not tick_path.is_file():
            raise AnimationError('Fresh animation needs raw_animation_state.json and '
                                 'network_ticks.json beside the segment; run replay extraction first')
        raw = _read(raw_path)
        server_ticks = {r['demo_tick']: r['server_tick'] for r in _read(tick_path)}
        # A segment digest alone cannot identify adjacent raw files: bind every
        # sidecar to the replay and to its exact extracted bytes.
        provenance_path = segment_path.parent/'animation_provenance.json'
        if not provenance_path.is_file():
            raise AnimationError('Fresh animation sidecars need animation_provenance.json '
                                 'from replay extraction to verify replay and file identity')
        provenance = _read(provenance_path)
        if provenance.get('demo_sha256') != digest:
            raise AnimationError('Raw animation extraction belongs to a different replay')
        for key, path in [('raw_sha256', raw_path), ('network_ticks_sha256', tick_path)]:
            if provenance.get(key) != _hash(path):
                raise AnimationError(f'Animation extraction checksum mismatch: {path.name}')
        table = _load_table(segment, segment_path.parent, demo, digest)
    output.mkdir(parents=True, exist_ok=True)
    result = copy.deepcopy(segment)
    diagnostics = []; reports = []
    for entity in result['entities']:
        identity = str(entity['id']); label = entity.get('hero_name', identity)
        try:
            desired = sorted({s['tick'] for s in entity['samples']
                              if segment['start_tick'] <= s['tick'] <= segment['end_tick']})
            if not desired:
                raise AnimationError('Entity has no movement samples in the requested range')
            if source == 'verified-cache':
                selected = _trim_prehistory(_select_rows(rows, entity, result['entities']),
                                            desired[0], desired[-1])
                graphs = {r['graph_resource'] for r in selected}
                if len(graphs) != 1:
                    raise AnimationError('Changing cached primary graph requires a new rig')
                graph = next(iter(graphs))
                if graph not in graph_catalog:
                    raise AnimationError(f'Asset pack does not contain graph {graph}')
                name, entry = graph_catalog[graph]
                skeleton = _skeleton(assets, entry, name)
                if entry.get('bones') != skeleton['m_boneIDs'] or entry.get('masks') != [
                        m['m_ID'] for m in skeleton['m_maskDefinitions']]:
                    raise AnimationError('Cached mapping does not match the asset skeleton')
            else:
                selected_raw = _trim_prehistory(_select_rows(raw, entity, result['entities']),
                                                desired[0], desired[-1], server_ticks)
                if not selected_raw:
                    raise AnimationError('No raw animation state for this entity')
                timeline = _TableTimeline(table)
                selected = []; skeleton = None; graph = None
                for row in selected_raw:
                    if row['demo_tick'] > desired[-1]:
                        break
                    recorded, registry = timeline.at(row['demo_tick'])
                    primary = int(_field(row, 'm_primaryGraphId'))
                    matches = [g for g in recorded if g.endswith('.vnmgraph') and resource_id(g) == primary]
                    if len(matches) != 1 or matches[0] not in graph_catalog:
                        raise AnimationError(f'Asset pack has no graph/skeleton mapping for primary graph ID {primary}')
                    current = matches[0]
                    if graph is not None and current != graph:
                        raise AnimationError('Primary graph changes within one entity; rig switching is unsupported')
                    graph = current; name, entry = graph_catalog[graph]
                    if skeleton is None:
                        skeleton = _skeleton(assets, entry, name)
                    if row['demo_tick'] not in server_ticks:
                        raise AnimationError(f'Missing network server tick at demo tick {row["demo_tick"]}')
                    selected.append(_bind(row, server_ticks[row['demo_tick']], recorded, registry, entry, skeleton))
            by_tick = {r['demo_tick']: r for r in selected}
            if len(by_tick) != len(selected):
                raise AnimationError('Duplicate animation records for one entity and tick')
            if any(t not in by_tick for t in desired):
                missing = next(t for t in desired if t not in by_tick)
                raise AnimationError(f'No animation record for requested tick {missing}')
            _require_cached_clips(selected, assets/'clip_cache')
            evaluator = Evaluator(label, skeleton, assets/'clip_cache')
            q = []; t = []; s = []; fresh = []; last = None; context = None
            desired_set = set(desired)
            for row in sorted(selected, key=lambda r: r['demo_tick']):
                if row['demo_tick'] > desired[-1]:
                    break
                current_context = row.get('context')
                if context is not None and current_context != context:
                    last = None
                context = current_context
                if row['fresh_recipe']:
                    last = evaluator.evaluate(row)
                elif last is None:
                    if row['demo_tick'] not in desired_set:
                        continue
                    raise AnimationError(f'First recipe at tick {row["demo_tick"]} is stale; '
                                         'extract earlier state through the last fresh pose')
                if row['demo_tick'] in desired_set:
                    q.append(last.q.copy()); t.append(last.t.copy()); s.append(last.s.copy())
                    fresh.append(bool(row['fresh_recipe']))
            slug = re.sub(r'[^A-Za-z0-9_-]', '_', identity)[:60]
            slug += '_' + hashlib.sha256(identity.encode()).hexdigest()[:8]
            pose_path = output/f'{slug}_poses.npz'
            skeleton_path = output/f'{slug}_skeleton.json'
            np.savez_compressed(pose_path, q=np.array(q), t=np.array(t), s=np.array(s),
                                ticks=np.asarray(desired, dtype=np.int64), fresh=np.asarray(fresh, dtype=bool))
            skeleton_path.write_text(json.dumps(skeleton, separators=(',', ':'))+'\n', encoding='utf-8')
            entity['pose_file'] = str(pose_path)
            entity['skeleton_file'] = str(skeleton_path)
            entity['animation_status'] = 'evaluated'
            reports.append({'id': entity['id'], 'frames': len(desired), 'bones': len(evaluator.names),
                            'fresh': sum(fresh), 'held': len(fresh)-sum(fresh),
                            'operations': dict(evaluator.histogram), 'clips': len(evaluator.clips),
                            'mask_diagnostics': evaluator.mask_diagnostics,
                            'mask_name_aliases': list({
                                (a['index'], a['recorded'], a['asset']): a
                                for row in selected for a in row.get('mask_name_aliases', [])}.values()),
                            'bone_name_aliases': list({
                                (a['index'], a['recorded'], a['asset']): a
                                for row in selected for a in row.get('bone_name_aliases', [])}.values())})
        except (AnimationError, ValueError, FileNotFoundError, KeyError, AssertionError, IndexError) as error:
            message = f'{label} ({identity}): {error}'
            details = getattr(error, 'details', None)
            if strict:
                raise AnimationError(message, details=details) from error
            entity['animation_status'] = 'unsupported'
            diagnostic = {'id': entity['id'], 'code': 'unsupported_animation', 'message': message}
            if details:
                diagnostic['details'] = details
            diagnostics.append(diagnostic)
    result['animation'] = {
        'source': source, 'demo_sha256': digest,
        'coordinates': {'space': 'source', 'quaternion_order': 'xyzw',
                        'translation_units': 'source_units', 'transforms': 'parent-local'},
        'entities': reports, 'diagnostics': diagnostics,
        'particles': False, 'ragdoll': False,
        'limitations': ['Numerical evaluator was validated against one supplied client build.',
                        'Stale recorded recipes hold the last evaluated pose.',
                        'Secondary animation, cloth, and physics simulation are unsupported.'],
    }
    result.setdefault('coverage', {})['skeletal_animation'] = {
        'evaluated_entities': len(reports), 'unsupported_entities': len(diagnostics),
        'all_requested_entities': not diagnostics,
    }
    manifest = output/'animated_segment.json'
    manifest.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return manifest
