"""Prepare only the animation resources referenced by a verified replay segment.

Names and lookup order come from this replay's string-table timeline. The game
installation supplies compiled graph, skeleton and clip bytes. No hero catalog,
known encounter, predecoded clip pack or four-hero whitelist is consulted.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import shutil

import numpy as np

from .animation import (AnimationError, _bind, _field, _hash, _identity,
                        _graph_change_error, _inside, _load_table, _read, _select_rows,
                        _skeleton, _TableTimeline, _trim_prehistory)
from .vendor.resource_hash import resource_id


def _resource(value: str, suffix: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnimationError(f'Missing {suffix} resource path')
    value = value.replace('\\', '/')
    if value.endswith('_c'):
        value = value[:-2]
    path = PurePosixPath(value)
    if path.is_absolute() or ':' in value or any(p in ('..', '.') for p in value.split('/')):
        raise AnimationError(f'Unsafe resource path: {value}')
    if not value.endswith(suffix):
        raise AnimationError(f'Expected {suffix} resource: {value}')
    return str(path)


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _copy(source: Path, destination: Path) -> None:
    source = Path(source).resolve()
    if source == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.part')
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def _clip_valid(metadata: dict, arrays: Path, skeleton: dict, primary: dict) -> None:
    if skeleton['m_boneIDs'] != primary['m_boneIDs'] or skeleton['m_parentIndices'] != primary['m_parentIndices']:
        raise AnimationError('Sampled clip requires retargeting to the primary skeleton')
    if metadata.get('m_secondaryAnimations'):
        raise AnimationError('Secondary animation is not implemented')
    if metadata.get('m_modelSpaceBoneSamplingIndices'):
        raise AnimationError('Model-space clip sampling is not implemented')
    if 'm_bIsAdditive' not in metadata:
        raise AnimationError('Decoded clip lacks its additive-pose flag')
    with np.load(arrays, allow_pickle=False) as data:
        q, t, s = data['q'], data['t'], data['s']
        bones = len(primary['m_boneIDs'])
        if q.ndim != 3 or q.shape[1:] != (bones, 4) or t.shape != q.shape[:-1] + (3,) or s.shape != q.shape[:-1]:
            raise AnimationError('Decoded clip array dimensions do not match the primary skeleton')
        if not len(q) or not all(np.isfinite(a).all() for a in (q, t, s)):
            raise AnimationError('Decoded clip contains empty or non-finite animation data')
        if np.any(np.linalg.norm(q, axis=-1) < 1e-8):
            raise AnimationError('Decoded clip contains zero-length quaternions')


def prepare_animation_assets(segment_path: Path, pack_root: Path, provider, converter,
                             demo: Path | None = None, strict: bool = True,
                             log=print) -> dict:
    """Build ``animation_catalog.json`` and the evaluator's ``clip_cache``.

    ``provider.resolve(name)`` returns an object with local_path and sha256.
    ``converter.dump(name, provider)`` returns decoded DATA;
    ``converter.decode_clip(name, provider)`` returns (DATA, decoded_npz_path).
    strict=False records each excluded entity; it never substitutes a pose.
    The following evaluate_segment stage still performs numerical evaluation.
    """
    segment_path = Path(segment_path).resolve()
    pack_root = Path(pack_root).resolve()
    demo = Path(demo).resolve() if demo else None
    segment = _read(segment_path)
    if segment.get('schema_version', 1) != 1:
        raise AnimationError('Unsupported segment schema version')
    if segment.get('metadata', {}).get('animation_source', 'replay') != 'replay':
        raise AnimationError('Installed animation preparation requires fresh replay state')
    digest = _identity(segment, demo)
    directory = segment_path.parent
    raw_path, ticks_path = directory/'raw_animation_state.json', directory/'network_ticks.json'
    provenance = _read(directory/'animation_provenance.json')
    if provenance.get('demo_sha256') != digest:
        raise AnimationError('Raw animation extraction belongs to a different replay')
    for key, path in [('raw_sha256', raw_path), ('network_ticks_sha256', ticks_path)]:
        if provenance.get(key) != _hash(path):
            raise AnimationError(f'Animation extraction checksum mismatch: {path.name}')
    raw = _read(raw_path)
    ticks = _read(ticks_path)
    server_ticks = {r['demo_tick']: r['server_tick'] for r in ticks}
    if len(server_ticks) != len(ticks):
        raise AnimationError('Duplicate network server tick records')
    table = _load_table(segment, directory, demo, digest)
    # Validate before preserving newly extracted tables for the evaluator.
    _TableTimeline(table)
    if not (directory/'animation_tables.json').is_file():
        _write(directory/'animation_tables.json', table)

    catalog, reports, diagnostics, sources = {}, [], [], {}
    skeletons, graphs, clips = {}, {}, {}
    cache = pack_root/'clip_cache'

    def asset_report(status):
        return {'schema_version': 1, 'source': 'installed-game', 'demo_sha256': digest,
                'status': status, 'catalog': catalog, 'entities': reports, 'diagnostics': diagnostics,
                'resources': sorted(sources.values(), key=lambda r: r['resource']),
                'sampled_clip_count': len(clips), 'pose_evaluation_complete': False,
                'limitations': ['Skeletal evaluation is a separate required stage.',
                                'Replay identity, graph ID and skeleton bone/mask ordering are checked.',
                                'Particle simulation, cloth and ragdoll are not part of this resource preparation.']}

    def source(name):
        if name not in sources:
            resolved = provider.resolve(name)
            sources[name] = {'resource': name, 'sha256': resolved.sha256}
        return sources[name]

    def get_skeleton(name):
        name = _resource(name, '.vnmskel')
        if name not in skeletons:
            source(name)
            data = converter.dump(name, provider)
            _write(_inside(cache, name, '.json'), data)
            data = _skeleton(pack_root, {'skeleton': name}, '')
            reference = np.asarray(data['m_parentSpaceReferencePose'], dtype=float)
            if reference.shape != (len(data['m_boneIDs']), 8) or not np.isfinite(reference).all():
                raise AnimationError('Invalid animation skeleton reference transforms')
            skeletons[name] = data
        return name, skeletons[name]

    for entity in segment['entities']:
        identity = str(entity['id'])
        label = entity.get('hero_name', identity)
        try:
            desired = sorted({r['tick'] for r in entity['samples']
                              if segment['start_tick'] <= r['tick'] <= segment['end_tick']})
            if not desired:
                raise AnimationError('Entity has no movement samples in the requested range')
            chosen = _trim_prehistory(_select_rows(raw, entity, segment['entities']),
                                     desired[0], desired[-1], server_ticks)
            if not chosen:
                raise AnimationError('No raw animation state for this entity')
            timeline = _TableTimeline(table)
            entry = None
            selected = []
            for row in chosen:
                recorded, registry = timeline.at(row['demo_tick'])
                primary = int(_field(row, 'm_primaryGraphId'))
                matches = [g for g in recorded if g.endswith('.vnmgraph') and resource_id(g) == primary]
                if len(matches) != 1:
                    raise AnimationError(f'Missing or ambiguous recorded primary graph ID {primary}')
                graph_name = _resource(matches[0], '.vnmgraph')
                if entry is not None and graph_name != entry['graph']:
                    raise _graph_change_error(entry['graph'], graph_name, row, chosen)
                if entry is None:
                    if graph_name not in graphs:
                        source(graph_name)
                        graphs[graph_name] = converter.dump(graph_name, provider)
                    skeleton_name, skeleton = get_skeleton(graphs[graph_name].get('m_skeleton'))
                    entry = {'hero_id': entity['hero_id'], 'graph': graph_name,
                             'graph_resource_id': str(primary), 'skeleton': skeleton_name,
                             'bones': skeleton['m_boneIDs'],
                             'masks': [m['m_ID'] for m in skeleton['m_maskDefinitions']]}
                if row['demo_tick'] not in server_ticks:
                    raise AnimationError(f'Missing network server tick at demo tick {row["demo_tick"]}')
                bound = _bind(row, server_ticks[row['demo_tick']], recorded, registry, entry, skeleton,
                              model_track=entity.get('model_track'), allow_unavailable=not strict)
                if any(t.get('extension_flag_b') for t in bound['tasks'] if t['type_id'] == 4):
                    raise AnimationError('Unimplemented IK extension')
                selected.append(bound)
                if bound.get('unavailable_pose'):
                    diagnostics.append({'id':entity['id'], 'hero_id':entity['hero_id'],
                        'code':'animation_reset_pose_unavailable', 'details':bound['unavailable_pose'],
                        'message':'No recorded reset pose; explicit partial export will hide this sample.'})
            by_tick = {row['demo_tick']: row for row in selected}
            if len(by_tick) != len(selected):
                raise AnimationError('Duplicate animation records for one entity and tick')
            for tick in desired:
                if tick not in by_tick:
                    raise AnimationError(f'No animation record for requested tick {tick}')
            live = False
            context = None
            desired_set = set(desired)
            for row in selected:
                if context is not None and context != row.get('context'):
                    live = False
                context = row.get('context')
                if row.get('unavailable_pose'):
                    live = False
                    continue
                live |= row['fresh_recipe']
                if row['demo_tick'] in desired_set and not live:
                    raise AnimationError(f'First recipe at tick {row["demo_tick"]} is stale; extract earlier state')

            needed = sorted({t['clip_resource'] for row in selected if row['fresh_recipe']
                             for t in row['tasks'] if 'clip_resource' in t})
            aliases = list({(a['index'], a['recorded'], a['asset']): a
                            for row in selected for a in row.get('bone_name_aliases', [])}.values())
            if aliases:
                log(f'{label}: matched {len(aliases)} bone names by ASCII letter case '
                    '(same indices; mask order unchanged)')
            mask_aliases = list({(a['index'], a['recorded'], a['asset']): a
                                for row in selected for a in row.get('mask_name_aliases', [])}.values())
            if mask_aliases:
                log(f'{label}: matched {len(mask_aliases)} mask names by ASCII letter case (same indices)')
            log(f'{label}: preparing {len(needed)} sampled animation clips')
            for name in needed:
                name = _resource(name, '.vnmclip')
                if name not in clips:
                    source(name)
                    metadata, npz = converter.decode_clip(name, provider)
                    clip_skeleton_name, clip_skeleton = get_skeleton(metadata.get('m_skeleton'))
                    metadata = dict(metadata, m_skeleton=clip_skeleton_name)
                    _clip_valid(metadata, npz, clip_skeleton, skeleton)
                    _write(_inside(cache, name, '.json'), metadata)
                    _copy(Path(npz), _inside(cache, name, '.npz'))
                    clips[name] = (metadata, clip_skeleton)
                else:
                    metadata, clip_skeleton = clips[name]
                    _clip_valid(metadata, _inside(cache, name, '.npz'), clip_skeleton, skeleton)
            # Graph is the key, so repeated hero names cannot overwrite a different rig.
            catalog[entry['graph']] = entry
            reports.append({'id': entity['id'], 'hero_id': entity['hero_id'],
                            'hero_name': label, 'graph': entry['graph'],
                            'skeleton': entry['skeleton'], 'clips': needed,
                            'bone_name_aliases': aliases,
                            'mask_name_aliases': mask_aliases,
                            'selected_rows': len(selected), 'frames': len(desired),
                            'status': 'prepared'})
        except (AnimationError, ValueError, OSError, KeyError, AssertionError, IndexError, RuntimeError) as error:
            message = f'{label} ({identity}): {error}'
            details = getattr(error, 'details', None)
            diagnostic = {'id': entity['id'], 'hero_id': entity['hero_id'],
                          'code': 'unsupported_animation_assets', 'message': message}
            if details:
                diagnostic['details'] = details
            diagnostics.append(diagnostic)
            if strict:
                try:
                    _write(pack_root/'animation_asset_report.json', asset_report('failed'))
                except OSError as report_error:
                    log(f'Could not save the animation asset diagnostics: {report_error}')
                raise AnimationError(message, details=details) from error
            log(message)
    report = asset_report('partial' if diagnostics else 'prepared')
    _write(pack_root/'animation_catalog.json', {'heroes': catalog})
    _write(pack_root/'animation_asset_report.json', report)
    return report
