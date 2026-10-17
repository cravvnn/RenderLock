"""Bridge installed resource IDs to AGR's existing scene/evaluator contracts."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .asset_converter import AssetConverter, ConversionError
from .game_assets import GameAssetProvider, GameAssetError
from .installed_animation import prepare_animation_assets
from .vendor.resource_hash import resource_id


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.part')
    temp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def bind_models(segment, resource_paths):
    """Compare exact recorded 64-bit resource IDs, including duplicate hero tracks."""
    index = {}
    for path in resource_paths:
        if path.endswith('.vmdl_c'):
            index.setdefault(resource_id(path[:-2]), []).append(path)
    bindings, failures = [], []
    for entity in segment['entities']:
        try:
            handle = int(entity.get('model_handle', ''))
        except (TypeError, ValueError):
            failures.append({'id': entity['id'], 'error': 'Replay lacks a numeric model resource ID'})
            continue
        matches = index.get(handle, [])
        if len(matches) != 1:
            failures.append({'id': entity['id'], 'hero_name': entity.get('hero_name'),
                             'model_handle': str(handle), 'matches': matches,
                             'error': 'No unique installed model matches the recorded resource ID'})
            continue
        bindings.append({'id': entity['id'], 'hero_id': entity['hero_id'],
                         'model_handle': str(handle), 'resource': matches[0]})
    return bindings, failures


def prepare_installed_assets(segment_path: Path, work_dir: Path, install_path: Path,
                             cache_dir: Path, converter: Path, demo: Path | None = None,
                             log=print, strict=True, map_name=None, need_animation=True,
                             include_effects=False, include_map=False, map_margin=26.0,
                             secondary_motion=False, material_repairs=False, simulate_effects=False,
                             model_constraints=False, source_lighting=False,
                             lighting_probe=None, lighting_dotnet=None, source_hero_lighting=False,
                             atlas_probe=None, cube_probe=None, source_npr_lighting=False) -> Path:
    if source_npr_lighting and (strict or not source_hero_lighting or not source_lighting or not include_map):
        raise ValueError('NPR preparation requires explicit partial hero lighting')
    segment_path = Path(segment_path).resolve()
    work_dir = Path(work_dir).resolve()
    pack = work_dir / 'installed-assets'
    pack.mkdir(parents=True, exist_ok=True)
    segment = json.loads(segment_path.read_text(encoding='utf-8'))
    for entity in segment['entities']:
        # Installed mode must not silently fall back to a stale model from a
        # prepared manifest when exact ID lookup or conversion fails.
        for key in ('model_file', 'asset_objects', 'asset_collection', 'rig_object',
                    'model_local_transform', 'model_yaw_degrees', 'model_scale',
                    'pose_basis_yaw_degrees', 'require_weighted_bone_mapping', 'static_generated_cloth', 'model_constraints'):
            entity.pop(key, None)
    # Optional visual map loading must not block otherwise valid hero assets.
    # Preserve the older explicit mount-only CLI behavior when no import is requested.
    provider = GameAssetProvider(install_path, cache_dir,
                                 map_name=map_name if not include_map else None)
    decoder = AssetConverter(converter, cache_dir, log=log)
    bindings, failures = bind_models(segment, provider.resources)
    report = {'status': 'preparing', 'source': 'installed_game',
              'demo_build': segment.get('metadata', {}).get('build'),
              'demo_sha256': segment.get('metadata', {}).get('demo_sha256'),
              'bindings': bindings, 'diagnostics': failures, 'models': [],
              'converter': decoder.identity,
              'scope': 'Hero geometry, materials and recorded skeletal animation, plus requested scene extensions with separate coverage.'}
    def checkpoint(status):
        report['status'] = status
        report['conversion_cache'] = dict(decoder.stats)
        snapshot = getattr(provider, 'snapshot', None)
        report['install_snapshot'] = snapshot() if callable(snapshot) else snapshot
        _write(pack / 'installed_asset_report.json', report)
        _write(work_dir / 'installed_asset_report.json', report)
    checkpoint('preparing')
    if failures and strict:
        checkpoint('missing_models')
        raise ConversionError('Installed model mapping failed: ' + '; '.join(
            f"{x.get('hero_name', x['id'])}: {x['error']}" for x in failures))
    try:
        converted = {}
        constraints = {}
        # No hero-name aliases: model identity is retained per entity.
        by_id = {str(e['id']): e for e in segment['entities']}
        for binding in bindings:
            path = binding['resource']
            try:
                if path not in converted:
                    if model_constraints:
                        constraints[path] = decoder.model_constraints(path, provider)
                    model, details = decoder.model(path, provider)
                    dest = pack / 'models' / model.parent.name
                    shutil.copytree(model.parent, dest, dirs_exist_ok=True)
                    converted[path] = dest / model.name
                    if material_repairs:
                        from .gltf_materials import repair_glb
                        repaired = dest / 'model-agr-materials.glb'
                        # Work output may be retried; the copied converter input
                        # remains intact and the derived variant is regenerated.
                        if repaired.exists():
                            repaired.unlink()
                        details = dict(details)
                        details['material_repairs'] = repair_glb(converted[path], repaired, approximate_glass=True)
                        _write(dest / 'material_repairs.json', details['material_repairs'])
                        converted[path] = repaired
                    report['models'].append(details)
                entity = by_id[str(binding['id'])]
                for key in ('asset_objects', 'asset_collection', 'rig_object', 'model_local_transform'):
                    entity.pop(key, None)
                entity.update(model_file=str(converted[path]), model_yaw_degrees=180,
                              pose_basis_yaw_degrees=-90, model_scale=1,
                              require_weighted_bone_mapping=True, static_generated_cloth=True)
                if model_constraints:
                    entity['model_constraints'] = constraints[path]
            except (GameAssetError, ConversionError, ValueError, OSError) as exc:
                report['diagnostics'].append({'id': binding['id'], 'resource': path, 'error': str(exc)})
                if strict:
                    raise
        _write(pack / 'assets.json', {'schema_version': 1, 'heroes': {},
                                     'binding_source': 'Exact recorded resource ID; model paths on each segment entity'})
        # Save model identities before evaluation so the existing pipeline imports
        # the exact model for each entity, even when hero identities repeat.
        _write(segment_path, segment)
        if need_animation:
            report['animation'] = prepare_animation_assets(segment_path, pack, provider, decoder,
                                                           demo=demo, strict=strict, log=log)
        else:
            report['animation'] = {'status': 'not_required', 'sampled_clip_count': 0,
                                   'reason': 'Explicit movement-only export or saved evaluated bone tracks'}
        segment = json.loads(segment_path.read_text(encoding='utf-8'))
        # These optional stages report their own coverage. A failed new feature
        # must not discard an otherwise valid hero/animation conversion.
        if include_effects or include_map or secondary_motion:
            from .scene_extensions import prepare_scene_extensions
            report['scene_extras'] = prepare_scene_extensions(
                segment_path, pack, provider, decoder, bindings, demo=demo,
                include_effects=include_effects, include_map=include_map,
                map_name=map_name, map_margin=map_margin,
                source_lighting=source_lighting, lighting_probe=lighting_probe, lighting_dotnet=lighting_dotnet,
                source_hero_lighting=source_hero_lighting,atlas_probe=atlas_probe,cube_probe=cube_probe,
                prepare_npr_materials=source_npr_lighting,apply_npr_indirect=source_npr_lighting,
                secondary_motion=secondary_motion, log=log,simulate_effects=simulate_effects)
            segment = json.loads(segment_path.read_text(encoding='utf-8'))
        segment.setdefault('coverage', {})['asset_source'] = 'installed_game'
        segment['coverage']['installed_model_bindings'] = len(converted)
        segment['coverage']['installed_asset_diagnostics'] = report['diagnostics']
        segment.setdefault('metadata', {})['installed_asset_snapshot'] = report.get('install_snapshot')
        if isinstance(segment.get('map'), dict):
            segment['map']['material_repairs'] = bool(material_repairs)
        _write(segment_path, segment)
        # A matching path hash is not proof that a current game asset matches an
        # old replay build. The evaluator still enforces recorded bones/masks/LUTs.
        _write(pack / 'asset_pack.json', {'source': 'installed_game',
               'compatibility': 'recorded_graph_and_skeleton_checks_required',
               'validated_demo_builds': []})
        checkpoint('prepared' if not report['diagnostics'] else 'prepared_partial')
        log(f'Installed assets ready: {len(converted)} distinct models; '
            f"{report['animation'].get('sampled_clip_count', 0)} sampled animation clips.")
        return pack
    except BaseException:
        checkpoint('failed')
        raise
