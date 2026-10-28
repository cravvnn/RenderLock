"""Command line and conversion orchestration for RenderLock."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .scene_package import read_scene, save_scene, slice_scene
from .coverage_diagnostics import (merge_stage_coverage, merge_secondary_motion_coverage,
                                   scene_coverage_warnings, scene_completion_message)


def log(message):
    print(message, flush=True)


def hero_list(value):
    try:
        heroes = list(dict.fromkeys(int(x.strip()) for x in value.split(",") if x.strip()))
        if not heroes or any(x <= 0 for x in heroes):
            raise ValueError
        return heroes
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated numeric hero IDs, e.g. 13,2,15,31") from exc


def record_model_coverage(data, assets, manifest_dir, heroes=None):
    """Keep all selected pawn lifetimes; asset availability is not a roster filter."""
    from .blender_scene import asset_spec
    entries = []
    for entity in data["entities"]:
        spec = asset_spec(entity, assets, manifest_dir)
        available = bool(spec.get("model_file") and Path(spec["model_file"]).is_file())
        entries.append({"id": entity["id"], "hero_id": entity.get("hero_id"),
                        "hero_name": entity.get("hero_name", str(entity.get("hero_id", entity["id"]))),
                        "model_file_available": available})
    coverage = {"selection": "all_heroes" if heroes is None else "explicit_hero_ids",
                "requested_hero_ids": heroes, "entity_tracks": len(entries),
                "unique_hero_ids": sorted({e["hero_id"] for e in entries if e["hero_id"] is not None}),
                "model_files_available": sum(e["model_file_available"] for e in entries),
                "missing_model_tracks": sum(not e["model_file_available"] for e in entries),
                "entities": entries,
                "note": "File availability only; rig and animation compatibility are checked during conversion."}
    data.setdefault("coverage", {})["hero_models"] = coverage
    return coverage


def executable(value):
    if not value:
        value = shutil.which("blender")
    if not value:
        raise ValueError("Select your Blender executable with --blender or in the window.")
    chosen = Path(value).expanduser()
    found = str(chosen.absolute()) if chosen.is_file() else shutil.which(str(value))
    if not found:
        raise ValueError(f"Executable not found: {value}")
    # Launch the selected path itself. Resolving a venv's Python symlink would
    # run the base interpreter and discard that environment's installed bpy.
    return str(Path(found).absolute())


def default_asset_cache():
    """Per-user generated assets; never put extraction output in the game folder."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "RenderLock" / "assets"


def discover_source2viewer():
    """Find the command-line exporter without launching it."""
    names = (("Source2Viewer-CLI.exe", "Source2ViewerCLI.exe") if sys.platform == 'win32'
             else ("Source2Viewer-CLI", "Source2ViewerCLI"))
    program = Path(__file__).resolve().parents[1]
    for base in (program / "tools", program / "tools" / "source2viewer", program, program / "bin"):
        for name in names:
            if (base / name).is_file():
                return (base / name).resolve()
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def resolve_asset_source(args):
    """Resolve basic input choices before spending time parsing the demo."""
    assets_arg = getattr(args, "assets", None)
    install_arg = getattr(args, "game_install", None)
    if assets_arg is not None:
        if install_arg is not None:
            raise ValueError("Choose either --assets or --game-install, not both.")
        if any(getattr(args, name, None) is not None for name in ("asset_cache", "source2viewer", "map_name")):
            raise ValueError("--asset-cache, --source2viewer and --map-name apply only to the installed-game source.")
        assets = Path(assets_arg).expanduser().resolve()
        if not assets.is_dir():
            raise ValueError("Prepared asset directory does not exist. Select the extracted asset folder with --assets.")
        return {"assets": assets}
    from .game_assets import discover_installations, normalize_install_path
    if install_arg is None:
        found = discover_installations()
        if not found:
            raise ValueError("Deadlock was not found automatically. Choose its installation folder with --game-install, or use --assets for a prepared pack.")
        if len(found) > 1:
            paths = "\n".join(f"  {path}" for path in found)
            raise ValueError("Several Deadlock installations were found. Choose one with --game-install:\n" + paths)
        install_arg = found[0]
    install = normalize_install_path(Path(install_arg).expanduser())
    requested = getattr(args, "source2viewer", None)
    converter = Path(requested).expanduser() if requested else discover_source2viewer()
    if converter is not None and not converter.is_file():
        found = shutil.which(str(converter))
        converter = Path(found) if found else None
    if converter is None:
        raise ValueError("Select the Source 2 Viewer command-line exporter using --source2viewer (Source2Viewer-CLI.exe on Windows).")
    if converter.name.lower() in {"source2viewer.exe", "source2viewer"}:
        raise ValueError("Source2Viewer.exe is the desktop viewer. Select Source2Viewer-CLI.exe with --source2viewer.")
    cache = Path(getattr(args, "asset_cache", None) or default_asset_cache()).expanduser().resolve()
    if cache.exists() and not cache.is_dir():
        raise ValueError("The asset cache must be a folder.")
    # Generated cache files must not enter the read-only game installation.
    if cache == install or install in cache.parents:
        raise ValueError("Choose an asset cache outside the Deadlock installation folder.")
    return {"install_path": install, "cache_dir": cache, "converter": converter.resolve(),
            "map_name": getattr(args, "map_name", None)}


def animation_mode(data, motion_only=False, saved_package=False, partial=False):
    """Choose current-job behavior without persisting an old job's opt-out."""
    coverage = data.setdefault("coverage", {})
    if motion_only:
        coverage["animation"] = "not_requested"
        return "motion"
    if coverage.get("animation") == "not_requested":
        del coverage["animation"]
    start, end = data.get('start_tick', 0), data.get('end_tick', float('inf'))
    for entity in data['entities']:
        selected = [s for s in entity.get('samples', []) if start <= s['tick'] <= end]
        if selected and all(s.get('pose_available') is False for s in selected):
            raise ValueError('A selected model track contains only an unavailable reset pose; choose a range before the reset or containing a later recorded pose.')
    if not partial and any(start <= t <= end for e in data['entities'] for t in e.get('pose_unavailable_ticks', [])):
        raise ValueError('Saved model tracks contain unavailable reset pose samples; enable explicit partial export or select a range after the reset.')
    complete = [bool(e.get("pose_file") and e.get("skeleton_file")) for e in data["entities"]]
    if all(complete):
        return "cached"
    if saved_package or any(complete):
        missing = [str(e.get("hero_name", e["id"])) for e, ready in zip(data["entities"], complete) if not ready]
        if not partial:
            raise ValueError("Saved package has no evaluated bone tracks for: " + ", ".join(missing) + ". Select supported entities, enable partial export, or convert the original replay.")
        for entity, ready in zip(data["entities"], complete):
            if not ready:
                entity["animation_status"] = "unsupported"
        coverage["skeletal_animation"] = {"evaluated_entities": sum(complete), "unsupported_entities": len(complete)-sum(complete), "scope": "selected saved tracks"}
        return "cached"
    return "evaluate"


def read_blender_completion(path, *, segment, output, data, fps, partial=False,
                            requested_stages=(), shot_hero=None, partial_cloth=False, external_map=None,
                            map_viewport_proxy=False):
    """A scene file alone cannot establish which stages Blender completed."""
    try:
        report = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError('Blender completion report is missing or unreadable; the scene was not published.') from exc
    if not isinstance(report, dict) or type(report.get('schema_version')) is not int or report['schema_version'] != 1:
        raise RuntimeError('Blender completion report has an unsupported schema; the scene was not published.')
    for key, expected in (('segment', segment), ('output', output)):
        value = report.get(key)
        if not isinstance(value, str) or Path(value).resolve() != Path(expected).resolve():
            raise RuntimeError(f'Blender completion report {key} differs from this job; the scene was not published.')
    for key, expected in (('start_tick', data['start_tick']), ('end_tick', data['end_tick']),
                          ('tick_rate', data['tick_rate']), ('fps', fps)):
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != expected:
            raise RuntimeError(f'Blender completion report {key} differs from this job; the scene was not published.')
    rows = report.get('entities')
    if (not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows)
            or len({row['id'] for row in rows}) != len(rows)
            or {row['id'] for row in rows} != {entity['id'] for entity in data['entities']}):
        raise RuntimeError('Blender completion report character tracks differ from this job; the scene was not published.')
    warnings = report.get('warnings')
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise RuntimeError('Blender completion report has invalid warnings; the scene was not published.')
    if not isinstance(report.get('coverage', {}), dict):
        raise RuntimeError('Blender completion report has invalid coverage; the scene was not published.')
    poses_requested = data.get('coverage', {}).get('animation') != 'not_requested'
    baked = 0
    for row in rows:
        entity=next(entity for entity in data['entities'] if entity['id']==row['id'])
        pose = row.get('pose')
        if not isinstance(pose, dict) or not isinstance(pose.get('status'), str):
            raise RuntimeError('Blender completion report lacks character animation outcomes; the scene was not published.')
        if row.get('model_status') not in ('imported', 'missing asset placeholder'):
            raise RuntimeError('Blender completion report lacks character model outcomes; the scene was not published.')
        if row['model_status'] != 'imported':
            if not partial:
                raise RuntimeError(f"{row['id']}: Blender used a model placeholder without partial export.")
            warnings.append(f"{row['id']}: model unavailable; visible placeholder used")
        if poses_requested:
            missing = [t for t in entity.get('pose_unavailable_ticks', []) if data['start_tick'] <= t <= data['end_tick']]
            if missing:
                from .blender_scene import visibility_changes
                expected_keys = [{'frame':f, 'hidden':h} for f,h in visibility_changes(entity,entity['samples'],data,fps)]
                if (not partial or pose['status'] != 'baked_partial' or pose.get('unavailable_pose_ticks') != missing
                        or row.get('visibility',{}).get('keys') != expected_keys
                        or type(pose.get('sample_count')) is not int or pose['sample_count'] <= 0):
                    raise RuntimeError(f"{row['id']}: Blender completion lacks exact partial reset-pose visibility coverage.")
            elif pose['status'] == 'baked_partial':
                raise RuntimeError(f"{row['id']}: Unexpected partial reset-pose claim.")
            if pose['status'] == 'baked':
                if type(pose.get('sample_count')) is not int or pose['sample_count'] <= 0:
                    raise RuntimeError(f"{row['id']}: Blender completion report claims a bake without recorded pose samples.")
                baked += 1
            else:
                detail = str(pose.get('reason') or pose['status'])
                if not partial:
                    raise RuntimeError(f"{row['id']}: Blender did not complete skeletal animation: {detail}")
                warnings.append(f"{row['id']}: " + ('partial skeletal animation. ' if pose['status']=='baked_partial'
                                                   else 'skeletal animation unavailable; imported rest pose. ') + detail)
        if entity.get('model_track') is not None and row.get('model_track') != entity['model_track']:
            raise RuntimeError(f"{row['id']}: Blender model-track identity differs from this job.")
        if entity.get('model_mesh_groups') is not None:
            from .blender_mesh_groups import expected_mesh_group_summary,validate_mesh_group_consent
            expected=expected_mesh_group_summary(entity['model_mesh_groups'],entity['samples'])
            try:validate_mesh_group_consent(expected,poses_requested=poses_requested,partial=partial)
            except ValueError as error:raise RuntimeError(f"{row['id']}: {error}") from error
            actual=row.get('mesh_groups')
            if not isinstance(actual,dict) or any(actual.get(key)!=expected[key] for key in
                    ('status','basis','mesh_count','default_mask','selections')):
                raise RuntimeError(f"{row['id']}: Blender completion lacks exact mesh-group visibility coverage; the scene was not published.")
        if entity.get('model_material_groups') is not None:
            from .model_material_groups import (material_group_summary,validate_material_group_contract,
                validate_material_group_consent)
            contract=entity['model_material_groups']
            validate_material_group_contract(contract,entity['samples'],model=entity.get('model_file'))
            validate_material_group_consent(contract,poses_requested=poses_requested,partial=partial)
            if row.get('material_groups')!=material_group_summary(contract):
                raise RuntimeError(f"{row['id']}: Blender completion lacks exact material-group slot coverage; the scene was not published.")
        if entity.get('secondary_motion_file'):
            motion=row.get('secondary_motion')
            if not isinstance(motion,dict) or motion.get('status') not in ('baked_partial','baked','failed'):
                raise RuntimeError(f"{row['id']}: Blender completion report lacks requested cloth outcome; the scene was not published.")
            if motion['status']=='failed':
                if not (partial or partial_cloth):raise RuntimeError(f"{row['id']}: requested cloth failed: {motion.get('error','no completed bake')}")
                warnings.append(f"{row['id']}: requested cloth failed: {motion.get('error','no completed bake')}")
    stages=set(requested_stages)
    if external_map is not None:
        from .external_map import validate_map_completion
        validate_map_completion(report.get('external_map'), external_map,
                                data.get('metadata', {}).get('coordinate_system'))
        stages.add('external_map')
    if map_viewport_proxy:
        from .external_map import validate_map_proxy_completion
        validate_map_proxy_completion(report.get('map_proxy'), report.get('external_map'))
    for key,stage in [('hero_lighting','hero_lighting'),('npr_runtime','npr_lighting'),('effects','effects')]:
        if data.get(key) is not None:stages.add(stage)
    allowed={'hero_lighting':{'partial'},'npr_lighting':{'partial'},'character_shader':{'partial'},
             'effects':{'partial','no_events','rendered'},'map':{'imported'},'map_lighting':{'partial'},
             'external_map':{'appended'}}
    if not stages<=set(allowed):raise RuntimeError('Unknown requested Blender completion stage')
    identities={row['id'] for row in rows}
    expected_materials={};expected_contracts={}
    if stages & {'npr_lighting','character_shader'}:
        package=data.get('npr_materials',{})
        try:
            for actor in package['entities']:
                from .source_npr_scene import material_set_key
                model=package['models'][material_set_key(actor)]
                expected_materials[actor['entity_id']]={m['material'] for m in model['materials']}
                if model.get('schema_version')==3:
                    for material in model['materials']:expected_contracts[actor['entity_id'],material['material']]=material
        except (KeyError,TypeError):
            raise RuntimeError('Blender completion lacks the current NPR material selection') from None
        if set(expected_materials)!=identities or any(not values for values in expected_materials.values()):
            raise RuntimeError('Blender completion NPR material selection differs from this job')
    for stage in sorted(stages):
        map_result=report.get('map')
        result=(map_result.get('lighting') if isinstance(map_result,dict) else None) if stage=='map_lighting' else report.get(stage)
        if not isinstance(result,dict) or not isinstance(result.get('status'),str):
            raise RuntimeError(f'Blender completion report lacks requested {stage} outcome; the scene was not published.')
        status=result['status']
        if status in ('failed','unavailable') and partial:
            warnings.append(f"Requested {stage} {status}: {result.get('error') or result.get('reason') or 'See stage diagnostics'}")
            continue
        if status not in allowed[stage]:
            raise RuntimeError(f'Blender did not complete requested {stage}: {status}; the scene was not published.')
        if stage=='hero_lighting':
            materials=result.get('materials');count=result.get('material_count')
            if (type(count) is not int or count<=0 or not isinstance(materials,list) or len(materials)!=count
                    or any(not isinstance(m,str) or not m for m in materials) or len(set(materials))!=count
                    or result.get('applied_components')!=data.get('hero_lighting_components',['diffuse','reflections'])
                    or set(result.get('actor_probes',{}))!=identities):
                raise RuntimeError('Blender completion report lacks exact applied hero lighting coverage; the scene was not published.')
        elif stage=='npr_lighting':
            prepared=result.get('preparation',{});actors=prepared.get('actors',[])
            if (not isinstance(actors,list) or any(not isinstance(a,dict) for a in actors)
                    or len(actors)!=len(identities) or {a.get('entity_id') for a in actors}!=identities
                    or any(not a.get('materials') for a in actors)
                    or not prepared.get('materials')
                    or len(prepared['materials'])!=sum(len(m) for m in expected_materials.values())
                    or any(len(a['materials'])!=len(expected_materials[a['entity_id']]) for a in actors)
                    or any({m.get('source_material') for m in a['materials']}!=expected_materials[a['entity_id']] for a in actors)):
                raise RuntimeError('Blender completion report lacks exact applied NPR actor coverage; the scene was not published.')
        elif stage=='character_shader':
            materials=result.get('materials')
            if (not isinstance(materials,list) or not materials or any(not isinstance(m,dict) for m in materials)
                    or len(materials)!=sum(len(m) for m in expected_materials.values())
                    or {m.get('entity_id') for m in materials}!=identities
                    or any({m.get('material') for m in materials if m.get('entity_id')==identity}!=expected
                           for identity,expected in expected_materials.items())):
                raise RuntimeError('Blender completion report lacks exact character shader actor coverage; the scene was not published.')
        elif stage=='map' and (type(result.get('mesh_objects')) is not int or result['mesh_objects']<=0):
            raise RuntimeError('Blender completion report claims map import without mesh objects; the scene was not published.')
        elif stage=='map_lighting' and not result.get('applied'):
            raise RuntimeError('Blender completion report claims static lighting without applied surfaces; the scene was not published.')
        if stage in ('npr_lighting','character_shader') and expected_contracts:
            material_rows=([(a['entity_id'],m['source_material'],m) for a in result['preparation']['actors'] for m in a['materials']]
                           if stage=='npr_lighting' else [(m['entity_id'],m['material'],m) for m in result['materials']])
            for identity,resource,material in material_rows:
                expected=expected_contracts.get((identity,resource))
                if expected is None:continue
                if (any(material.get(key)!=expected[key] for key in ('route','runtime','fidelity_gaps'))
                        or ('expression_evaluation' in expected and material.get('expression_evaluation')!=expected['expression_evaluation'])
                        or (stage=='npr_lighting' and expected['route']=='npr_indirect'
                            and material.get('no_specular_at_full_roughness') is not expected['no_specular_at_full_roughness'])
                        or (stage=='character_shader' and material.get('artist_shader_applied') is not (expected['route']=='npr_indirect'))):
                    raise RuntimeError('Blender completion material route/runtime coverage differs; the scene was not published.')
            gaps=sorted({gap['feature'] for value in expected_contracts.values() for gap in value['fidelity_gaps']})
            if gaps:warnings.append('Static Source material reference leaves these fidelity gaps unresolved: '+', '.join(gaps))
    if shot_hero is not None:
        shot=report.get('camera')
        from .model_tracks import actor_id
        targets={actor_id(e) for e in data['entities'] if str(e.get('hero_id'))==str(shot_hero)}
        if (not isinstance(shot,dict) or shot.get('mode')!='hero follow' or str(shot.get('hero_id'))!=str(shot_hero)
                or shot.get('entity_id') not in targets or type(shot.get('frames')) is not int
                or shot['frames']!=math.ceil(1+(data['end_tick']-data['start_tick'])*fps/data['tick_rate'])):
            raise RuntimeError('Blender completion report lacks the requested hero follow camera; the scene was not published.')
    report['requested_stages']=sorted(stages)
    report['warnings'] = list(dict.fromkeys(warnings))
    if poses_requested:
        coverage = {'evaluated_entities': baked, 'unsupported_entities': len(rows) - baked,
                    'all_requested_entities': baked == len(rows),
                    'scope': 'Actual Blender skeletal bakes for the selected output range'}
        report.setdefault('coverage', {})['skeletal_animation'] = coverage
        data.setdefault('coverage', {})['skeletal_animation'] = dict(coverage)
    return report


def _rename_package_path(source, destination):
    """Allow short Windows sharing locks without weakening atomic publication."""
    for attempt in range(6):
        try:
            return source.rename(destination)
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(.1 * 2**attempt)


def validate_cloth_preparation(data, *, partial=False):
    """Requested cloth must have a complete preparation outcome before Blender.

    Explicit broad partial export can retain a reported failure or unsupported
    layout. Lighting consent never permits absent character/cloth data.
    """
    report=data.get('coverage',{}).get('scene_extras',{}).get('secondary_motion')
    if not isinstance(report,dict) or report.get('status') not in (
            'prepared','prepared_partial','not_applicable','no_supported_entities','failed'):
        raise RuntimeError('Requested cloth has no preparation outcome; the scene was not published.')
    expected={str(entity['id']) for entity in data['entities']}
    seen=set();issues=[];ready=0
    for field in ('supported_entities','unsupported_entities','failed_entities','not_applicable_entities'):
        rows=report.get(field,[])
        if not isinstance(rows,list):raise RuntimeError('Invalid cloth preparation entity coverage.')
        for row in rows:
            if not isinstance(row,dict) or str(row.get('id')) not in expected or str(row['id']) in seen:
                raise RuntimeError('Cloth preparation character identities differ from this job.')
            seen.add(str(row['id']))
            if field=='supported_entities':
                entity=next(entity for entity in data['entities'] if str(entity['id'])==str(row['id']))
                source=entity.get('secondary_motion_file')
                if row.get('status')!='ready_to_bake' or not isinstance(source,str) or not Path(source).is_file():
                    raise RuntimeError('Prepared cloth lacks its requested bake input; the scene was not published.')
                ready+=1
            elif field in ('unsupported_entities','failed_entities'):
                label=row.get('hero_name') or str(row['id'])
                issues.append(str(label)+': '+str(row.get('error') or row.get('reason') or 'cloth unavailable'))
    if seen!=expected and report['status']!='failed':
        raise RuntimeError('Cloth preparation omitted selected characters; the scene was not published.')
    if report['status'] in ('prepared','prepared_partial') and not ready:
        raise RuntimeError('Cloth preparation claims success without bake inputs; the scene was not published.')
    if report['status']=='not_applicable' and (ready or issues):
        raise RuntimeError('Cloth not-applicable outcome conflicts with its entity coverage.')
    if not partial and (issues or report['status'] in ('failed','no_supported_entities','prepared_partial')):
        detail='; '.join(issues) or str(report.get('error') or report.get('reason') or report['status'])
        raise RuntimeError('Requested cloth preparation did not complete: '+detail)
    return report


def publish_package(manifest, destination, overwrite=False):
    """Copy only reusable scene data. Replay bytes and game assets remain separate."""
    owner = {"format": "deadlock-agr-scene", "version": 1}
    if destination.exists():
        marker = destination / "format.json"
        if not overwrite or not marker.is_file() or json.loads(marker.read_text()) != owner:
            raise ValueError(f"Scene package already exists: {destination}. Choose a new output name.")
    temp = Path(tempfile.mkdtemp(prefix=destination.name + "-new-", dir=destination.parent))
    try:
        data = read_scene(manifest)
        for entity in data["entities"]:
            token = hashlib.sha256(entity["id"].encode()).hexdigest()[:16]
            for key, suffix in (("pose_file", "poses.npz"), ("skeleton_file", "skeleton.json")):
                if entity.get(key):
                    target = Path("entities") / token / suffix
                    (temp / target).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(entity[key], temp / target)
                    entity[key] = target.as_posix()
            # Models are resolved by the chosen asset pack on the next import.
            entity.pop("model_file", None)
            entity.pop('model_mesh_groups',None)
            entity.pop('model_material_groups',None)
            entity.pop("secondary_motion_file", None)
        # Recorded events remain portable. Rebuild derived textures/geometry
        # through the installed cache on a later import instead of retaining
        # paths into the temporary conversion job that is about to be removed.
        data.pop("effects", None)
        data.pop("map", None)
        data.pop('hero_lighting', None)
        data.pop('npr_materials', None)
        data.pop('npr_runtime', None)
        data.pop('hero_lighting_components',None)
        data.setdefault("metadata", {})["application"] = "RenderLock"
        data["metadata"]["application_version"] = __version__
        save_scene(data, temp / "manifest.json")
        (temp / "format.json").write_text(json.dumps(owner), encoding="utf-8")
        backup = None
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=destination.name + "-previous-", dir=destination.parent))
            backup.rmdir()
            _rename_package_path(destination, backup)
        try:
            _rename_package_path(temp, destination)
        except BaseException:
            if backup:
                _rename_package_path(backup, destination)
            raise
        if backup:
            shutil.rmtree(backup)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def convert(args):
    from .export_diagnostics import ExportDiagnostics
    diagnostics = ExportDiagnostics(args, log)
    try:
        return _convert(args, diagnostics)
    except BaseException as exc:
        diagnostics.finalize(exc)
        raise


def _convert(args, diagnostics):
    from .release_profile import validate_conversion
    validate_conversion(args)
    started = time.monotonic()
    history_ticks = getattr(args, 'particle_history_ticks', 0)
    if history_ticks < 0:
        raise ValueError('--particle-history-ticks must be nonnegative')
    if history_ticks and (not getattr(args, 'particle_probe', None) or args.start_tick is None):
        raise ValueError('--particle-history-ticks requires --particle-probe and --start-tick')
    extraction_start = max(0, args.start_tick-history_ticks) if args.start_tick is not None else None
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".blend":
        raise ValueError("Output must end in .blend")
    external_map = None
    if getattr(args, 'external_map', None) is not None:
        from .external_map import validate_map_library
        external_map = validate_map_library(args.external_map)
        if Path(external_map['path']) == output:
            raise ValueError('External map: output must differ from the downloaded map library.')
        args.character_only = True  # Read installed lighting if requested; do not duplicate map geometry.
    if getattr(args, 'map_viewport_proxy', False):
        from .external_map import MAP_BLEND_SHA256
        if external_map is None:
            raise ValueError('Viewport proxy: choose the downloaded map before requesting a simplified viewport.')
        if external_map['sha256'] != MAP_BLEND_SHA256:
            raise ValueError('Viewport proxy: only the current downloaded Midtown map supports this option.')
    if args.fps <= 0 or args.fps > 240:
        raise ValueError("Export FPS must be between 1 and 240.")
    if args.start_tick is not None and args.start_tick < 0:
        raise ValueError("Start tick cannot be negative.")
    if args.end_tick is not None and args.start_tick is not None and args.end_tick < args.start_tick:
        raise ValueError("End tick must be greater than or equal to start tick.")
    if not math.isfinite(args.map_margin) or not 0 < args.map_margin <= 1000:
        raise ValueError("Map margin must be greater than 0 and at most 1000 metres.")
    if args.include_map and not args.map_name:
        raise ValueError("--include-map requires the explicit installed --map-name, for example dl_midtown.")
    source_lighting = getattr(args, 'source_lighting', False)
    lighting_probe = getattr(args, 'world_region_probe', None)
    lighting_dotnet = getattr(args, 'lighting_dotnet', None)
    source_hero_lighting = getattr(args,'source_hero_lighting',False)
    source_npr_lighting=getattr(args,'source_npr_lighting',False)
    partial_lighting=bool(args.partial or getattr(args,'partial_lighting',False))
    character_lighting=bool(source_lighting and getattr(args,'character_only',False))
    character_shader=getattr(args,'character_shader',None)
    if character_shader is not None:
        if not source_npr_lighting or not partial_lighting:
            raise ValueError('--character-shader requires --source-npr-lighting and --partial-lighting (or --partial)')
        from .blender_character_shader import validate_character_shader_library
        character_shader=validate_character_shader_library(character_shader)
    if source_npr_lighting and (not source_hero_lighting or not partial_lighting):
        raise ValueError('--source-npr-lighting requires --source-hero-lighting and --partial-lighting (or --partial)')
    if source_npr_lighting and getattr(args,'shot_hero',None) is None:
        raise ValueError('--source-npr-lighting requires --shot-hero for a qualified perspective camera')
    atlas_probe = getattr(args,'probe_atlas_decoder',None)
    cube_probe = getattr(args,'cubemap_decoder',None)
    if source_hero_lighting or character_lighting:
        if not source_lighting:raise ValueError('--source-hero-lighting requires --source-lighting')
        from .source_hero_lighting import validate_hero_decoder_paths
        validate_hero_decoder_paths(atlas_probe,cube_probe,lighting_dotnet)
    elif atlas_probe or cube_probe:
        raise ValueError('Hero decoder options require --source-hero-lighting')
    if source_lighting:
        if not args.include_map or args.assets:
            raise ValueError('--source-lighting requires --include-map and installed-game assets')
        if not partial_lighting:
            raise ValueError('--source-lighting requires --partial-lighting (or --partial) to accept reference lighting limits')
        if not lighting_dotnet or not character_lighting and not lighting_probe:
            raise ValueError('--source-lighting requires --world-region-probe and --lighting-dotnet')
        if not character_lighting:
            from .source_region import regional_command
            regional_command(lighting_probe, lighting_dotnet)
    elif lighting_probe or lighting_dotnet:
        raise ValueError('--world-region-probe and --lighting-dotnet require --source-lighting')
    shot_hero = getattr(args, 'shot_hero', None)
    if shot_hero is not None:
        if shot_hero <= 0:
            raise ValueError('--shot-hero must be a positive hero ID')
        if args.heroes is not None and shot_hero not in args.heroes:
            raise ValueError('--shot-hero must be included in the exported --heroes selection')
    input_path = (args.demo or args.segment).expanduser().resolve()
    if not input_path.exists():
        raise ValueError(f"Input does not exist: {input_path}")
    asset_source = resolve_asset_source(args)
    particle_probe=getattr(args,'particle_probe',None)
    if particle_probe:
        if args.assets or args.no_effects or args.motion_only:
            raise ValueError('--particle-probe requires installed assets, effects and skeletal animation')
        particle_probe=Path(particle_probe).expanduser().resolve()
        from .particle_runtime import particle_command
        particle_dotnet = None
        if particle_probe.suffix.lower() == '.dll':
            requested_dotnet=getattr(args,'dotnet',None) or shutil.which('dotnet')
            if not requested_dotnet:raise ValueError('--particle-probe DLL requires a dotnet executable; specify --dotnet')
            particle_dotnet=executable(requested_dotnet)
        particle_command(particle_probe, particle_dotnet)
    elif getattr(args,'dotnet',None):
        raise ValueError('--dotnet requires --particle-probe')
    if args.assets and (args.include_map or args.secondary_motion):
        raise ValueError("Map and secondary-motion preparation require Installed game mode.")
    assets = asset_source.get("assets")
    if output == input_path:
        raise ValueError("Output cannot replace the input.")
    package = output.with_suffix(".agrscene")
    report_path = output.with_suffix(".agr-report.json")
    if not args.overwrite and any(p.exists() for p in (output, package, report_path)):
        raise ValueError("Output or its companion package already exists. Choose a new name, or explicitly enable --overwrite.")
    engine = executable(args.blender_python or args.blender)
    from .blender_runtime import check_blender
    log('Checking Blender startup and required capabilities...')
    blender_capabilities = check_blender(engine, python_mode=bool(args.blender_python),
                                         character_shader=character_shader is not None)
    if external_map is not None:
        from .external_map import validate_map_blender
        validate_map_blender(blender_capabilities)
    heroes = args.heroes
    log("Hero selection: all heroes in the range." if heroes is None else
        "Hero selection: explicit IDs " + ", ".join(map(str, heroes)))
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="agr-", dir=output.parent))
    diagnostics.work = work
    success = False
    saved_package = False
    stage = "read_segment"
    stage_started = time.monotonic()
    timings = []

    def set_stage(name):
        nonlocal stage, stage_started
        now = time.monotonic()
        timings.append({"stage": stage, "seconds": round(now - stage_started, 3)})
        stage = name
        stage_started = now

    try:
        if args.demo:
            if args.start_tick is None or args.end_tick is None:
                raise ValueError("Replay conversion requires --start-tick and --end-tick.")
            from .replay import extract_replay
            log("[1/4] Reading replay and reconstructing entity state...")
            segment = extract_replay(input_path, work / "extracted", extraction_start, args.end_tick, heroes)
        else:
            log("[1/4] Reading saved scene package...")
            original = read_scene(input_path)
            from .release_profile import validate_scene
            validate_scene(original)
            if history_ticks:
                from .particle_history import history_start
                # Published packages retain the recovered root/pose tracks but
                # advertise the requested output range as their main interval.
                original = dict(original, start_tick=history_start(original))
            data = slice_scene(original, extraction_start, args.end_tick, heroes)
            segment = save_scene(data, work / "extracted" / "segment.json")
            source_dir = input_path if input_path.is_dir() else input_path.parent
            marker = source_dir / "format.json"
            saved_package = marker.is_file() and json.loads(marker.read_text()).get("format") == "deadlock-agr-scene"
            for name in ("raw_animation_state.json", "network_ticks.json", "animation_tables.json", "animation_provenance.json"):
                if (source_dir / name).is_file():
                    shutil.copy2(source_dir / name, segment.parent / name)
        if args.model_constraints and assets is not None:
            constraint_input = read_scene(segment)
            if any(e.get('model_constraints') is None for e in constraint_input['entities']):
                raise ValueError('--model-constraints requires Installed game assets or prepared constraint definitions for every selected entity')
        if args.no_effects:
            # A saved package may carry a render manifest and an earlier job's
            # failure. Explicit omission applies to this job, not just extraction.
            # Keep raw events so a later export can request effects again.
            selected = read_scene(segment)
            selected.pop('effects', None)
            selected.setdefault('scene_options', {})['effects'] = False
            selected.setdefault('coverage', {}).setdefault('scene_extras', {})['particles'] = {
                'status': 'not_requested'}
            save_scene(selected, segment)
        if assets is None:
            from .installed_assets import prepare_installed_assets
            set_stage("prepare_installed_assets")
            selected = read_scene(segment)
            has_cached_poses = any(bool(e.get("pose_file") and e.get("skeleton_file"))
                                   for e in selected["entities"])
            # Match the evaluator's existing routing without moving its strict
            # mixed-cache and model-coverage checks ahead of their normal stage.
            need_animation = not (args.motion_only or saved_package or has_cached_poses)
            log("Preparing selected heroes from the installed game and reusing verified cached assets...")
            assets = prepare_installed_assets(segment_path=segment, work_dir=work,
                                             install_path=asset_source["install_path"],
                                             cache_dir=asset_source["cache_dir"],
                                             converter=asset_source["converter"],
                                             demo=input_path if args.demo else None,
                                             log=log, strict=not args.partial,
                                             map_name=asset_source["map_name"],
                                             need_animation=need_animation,
                                             include_effects=not args.no_effects,
                                             material_repairs=args.material_repairs,
                                             model_constraints=args.model_constraints,
                                             simulate_effects=bool(particle_probe),
                                             include_map=args.include_map,
                                             map_margin=args.map_margin,
                                             source_lighting=source_lighting,
                                             lighting_probe=lighting_probe, lighting_dotnet=lighting_dotnet,
                                             source_hero_lighting=source_hero_lighting,atlas_probe=atlas_probe,cube_probe=cube_probe,
                                             source_npr_lighting=source_npr_lighting,
                                             allow_partial_lighting=partial_lighting,
                                             character_only=bool(getattr(args,'character_only',False)),
                                             secondary_motion=args.secondary_motion)
            assets = Path(assets).resolve()
            if not assets.is_dir():
                raise RuntimeError("Installed asset preparation finished without producing an asset folder.")
        set_stage("model_coverage")
        data = read_scene(segment)
        data.setdefault("metadata", {})["application_version"] = __version__
        if args.secondary_motion:
            stage='validate_cloth_preparation'
            validate_cloth_preparation(data,partial=args.partial or getattr(args,'partial_cloth',False))
            stage='model_coverage'
        models = record_model_coverage(data, assets, segment.parent, heroes)
        save_scene(data, segment)
        log(f"Roster: {models['entity_tracks']} entity tracks; "
            f"{models['model_files_available']} have model files; "
            f"{models['missing_model_tracks']} are missing model files.")
        missing = [e for e in models["entities"] if not e["model_file_available"]]
        if missing:
            labels = list(dict.fromkeys(f"{e['hero_name']} (ID {e['hero_id']})" for e in missing))
            detail = ", ".join(labels)
            if not args.partial:
                raise ValueError(f"Missing model files for {len(missing)} of {len(models['entities'])} "
                                 f"entity tracks: {detail}. Add prepared hero assets, enter explicit "
                                 "Hero IDs to export a subset, or enable partial export to retain "
                                 "all selected tracks with movement placeholders.")
            log("Partial export: retaining missing-model tracks as movement placeholders: " + detail)
        pack_metadata = assets / "asset_pack.json"
        if pack_metadata.is_file():
            pack = json.loads(pack_metadata.read_text(encoding="utf-8"))
            data["metadata"]["asset_pack"] = pack
            tested = pack.get("validated_demo_builds", [])
            actual_build = data["metadata"].get("build", data["metadata"].get("demo_build"))
            if tested and actual_build not in tested:
                if not args.partial:
                    raise ValueError(f"Asset pack is validated for demo builds {tested}, but this replay reports {actual_build}. Select a matching pack; partial export can retain unsupported tracks with diagnostics.")
                data.setdefault("coverage", {})["asset_compatibility"] = "unverified_build"
                log(f"Partial export: asset compatibility with demo build {actual_build} is unverified.")
        if not isinstance(data["metadata"].get("coordinate_system"), dict):
            legacy = data["metadata"].get("coordinate_system")
            if legacy and legacy != "source2_z_up_hammer_units":
                raise ValueError(f"Unsupported coordinate system: {legacy}")
            if legacy:
                data["metadata"]["source_coordinate_system"] = legacy
            data["metadata"]["coordinate_system"] = {"unit_scale": 0.0254, "origin": [0, 0, 0], "world_yaw_degrees": 0, "model_yaw_degrees": 180}
        mode = animation_mode(data, args.motion_only, saved_package, args.partial)
        set_stage("evaluate_animation")
        save_scene(data, segment)
        if mode == "motion":
            log("[2/4] Explicit movement-only export; skeletal evaluation skipped.")
        elif mode == "cached":
            log("[2/4] Using available saved bone tracks and their coverage diagnostics.")
        else:
            from .animation import evaluate_segment
            log("[2/4] Decoding recorded animation and evaluating bone poses...")
            segment = evaluate_segment(segment, work / "animation", assets, demo=input_path if args.demo else None, strict=not args.partial)
        if history_ticks:
            from .particle_history import select_output_with_history
            data = select_output_with_history(read_scene(segment), args.start_tick)
            segment = save_scene(data, work / 'output_segment.json')
            log(f"Recovered particle motion from tick {data['particle_history']['start_tick']}; output starts at {args.start_tick}.")
        log("[3/4] Creating a fresh Blender scene...")
        set_stage("build_blender_scene")
        script = Path(__file__).with_name("blender_scene.py").resolve()
        scene_output = work / "scene.blend"
        command = [engine, str(script)] if args.blender_python else [engine, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script), "--"]
        if external_map is not None and not args.blender_python:
            command.insert(3, '--disable-autoexec')
        command += ["--segment", str(segment), "--assets", str(assets), "--output", str(scene_output), "--fps", str(args.fps)]
        if getattr(args, 'shot_hero', None) is not None:
            command += ['--shot-hero', str(args.shot_hero)]
        if character_shader is not None:
            command += ['--character-shader',str(character_shader)]
        if external_map is not None:
            command += ['--external-map', external_map['path']]
        if getattr(args, 'map_viewport_proxy', False):
            command += ['--map-viewport-proxy']
        if getattr(args,'character_only',False):
            command += ['--character-only']
        if args.partial:
            command += ["--partial"]
        if getattr(args,'partial_lighting',False):
            command += ['--partial-lighting']
        if particle_probe:
            runtime={key:str(asset_source[key]) for key in ('install_path','cache_dir','converter')}
            # These plans, diagnostics and prepared states are part of the
            # effect audit, so they must survive normal temporary-work cleanup.
            effect_artifacts=Path(tempfile.mkdtemp(prefix=output.stem+'-effects-',dir=output.parent))
            runtime.update(probe=str(particle_probe),dotnet=particle_dotnet,output_dir=str(effect_artifacts))
            runtime_path=work/'particle-runtime.json'
            runtime_path.write_text(json.dumps(runtime),encoding='utf-8')
            command += ['--particle-runtime',str(runtime_path)]
        subprocess.run(command, check=True)
        if not scene_output.is_file() or scene_output.stat().st_size == 0:
            raise RuntimeError("Blender finished without producing a scene.")
        set_stage("validate_blender_completion")
        generated = read_scene(segment)
        requested_stages=set()
        character_only=getattr(args,'character_only',False)
        if args.include_map and not character_only:requested_stages.add('map')
        if getattr(args,'source_lighting',False):requested_stages.add('hero_lighting' if character_only else 'map_lighting')
        if getattr(args,'source_hero_lighting',False):requested_stages.add('hero_lighting')
        if getattr(args,'source_npr_lighting',False):requested_stages.add('npr_lighting')
        if character_shader is not None:requested_stages.add('character_shader')
        if particle_probe:requested_stages.add('effects')
        blender_report = read_blender_completion(scene_output.with_suffix('.scene.json'),
            segment=segment, output=scene_output, data=generated, fps=args.fps, partial=args.partial,
            requested_stages=requested_stages,shot_hero=getattr(args,'shot_hero',None),
            partial_cloth=bool(getattr(args,'partial_cloth',False)),
            **({'map_viewport_proxy': True} if getattr(args, 'map_viewport_proxy', False) else {}),
            **({'external_map': external_map} if external_map is not None else {}))
        save_scene(generated, segment)
        log("[4/4] Saving the scene package and coverage report...")
        set_stage("publish_scene")
        generated = read_scene(segment)
        report = {"application": "RenderLock", "version": __version__, "status": "converted", "output": str(output), "scene_package": str(package / "manifest.json"), "start_tick": generated["start_tick"], "end_tick": generated["end_tick"], "tick_rate": generated["tick_rate"], "export_fps": args.fps, "entities": len(generated["entities"]), "coverage": generated.get("coverage", {}), "seconds": round(time.monotonic() - started, 3), "scene_extras": generated.get("coverage", {}).get("scene_extras", {}), "ragdoll": "outside_current_scope", "death_workflow": "user_authored_animation",
                  "map_workflow": {"status": "installed_region" if args.include_map else "external_blend",
                                   "collection": None, "timings_seconds": None},
                  "backward_dash_facing": "not_validated"}
        report['blender_runtime'] = blender_capabilities
        report['export_policy'] = {'partial_animation': bool(args.partial),
                                   'partial_cloth': bool(getattr(args,'partial_cloth',False))}
        report["particles"] = report["scene_extras"].get("particles", {"status": "not_requested"})
        report["map"] = report["scene_extras"].get("map", {"status": "not_requested"})
        report['hero_lighting'] = report['scene_extras'].get('hero_lighting',{'status':'not_requested'})
        report['npr_lighting']=report['scene_extras'].get('npr_materials',{'status':'not_requested'})
        if 'hero_lighting' in generated:
            report['hero_lighting_package'] = generated['hero_lighting']
        report["blender"] = blender_report
        report["blender"]["segment"] = str(package / "manifest.json")
        report["blender"]["output"] = str(output)
        # Mirror the camera/advisory block at the top level so a consumer does
        # not have to know that it currently lives under "blender".
        report["camera"] = blender_report.get("camera")
        report["particles"] = merge_stage_coverage(report["particles"], report["blender"].get("effects"))
        report["map"] = merge_stage_coverage(report["map"], report["blender"].get("map"))
        if 'hero_lighting' in report['blender']:
            report['hero_lighting'] = merge_stage_coverage(report['hero_lighting'],report['blender']['hero_lighting'])
        if 'npr_lighting' in report.get('blender',{}):
            report['npr_lighting']=report['blender']['npr_lighting']
        if 'character_shader' in report.get('blender',{}):
            report['character_shader']=report['blender']['character_shader']
        if getattr(args,'character_only',False):
            report['map_workflow']['status']='external_blend'
            report['map']=dict(status='not_requested',reason='Separate map geometry',
                               prepared_lighting_assets=report['map'])
        if external_map is not None:
            report['map_workflow']['status'] = 'external_blend_appended'
            report['external_map'] = blender_report['external_map']
            report['map'] = dict(blender_report['external_map'], source='downloaded_library',
                                 prepared_lighting_assets=report['map'])
        report['map_workflow']['collection'] = report['map'].get('collection')
        report['map_workflow']['timings_seconds'] = report['map'].get('timings_seconds')
        report['map_workflow_status'] = report['map_workflow']['status']
        if getattr(args, 'map_viewport_proxy', False):
            report['map_proxy'] = blender_report['map_proxy']
        report["secondary_motion"] = merge_secondary_motion_coverage(
            report["scene_extras"].get("secondary_motion", {"status": "not_requested"}),
            report.get("blender", {}).get("entities", []))
        report["warnings"] = scene_coverage_warnings(report)
        if report["warnings"]:
            report["status"] = "converted_with_warnings"
        report["summary"] = scene_completion_message(report)
        report["timings"] = list(timings)
        # Input replay/asset files are never written by the job.
        from .export_publish import publish_export
        publish_export(scene_output, segment, output, report, overwrite=args.overwrite,
                       stage_package=publish_package)
        success = True
        set_stage("complete")
        log(f"Saved: {output}")
        log("Stage timings: " + ", ".join(f"{row['stage']} {row['seconds']:.1f}s" for row in timings))
        log(f"Range: {generated['start_tick']}-{generated['end_tick']} inclusive; {len(generated['entities'])} entity tracks")
        log(f"Coverage: {report_path}")
        log(report["summary"])
        if external_map is not None:
            log('Map appended in RenderLockMap. In Blender, cut in Solid view first with the separate Map Optimizer (Map = RenderLockMap), remove unused map materials/images, then relink retained textures with File > External Data > Find Missing Files. Inspect camera placement before rendering. See MAP_WORKFLOW.md for memory and EEVEE limits. This snapshot may differ from the replay revision.')
        if report["status"] == "converted_with_warnings":
            for warning in report["warnings"]:
                log("WARNING: " + warning)
            log("Completed with coverage warnings. Open the coverage report or AGR_README in Blender for missing/approximate features.")
        return report
    except BaseException as exc:
        diagnostics.context = {"stage": stage,
                               "stage_seconds": round(time.monotonic() - stage_started, 3),
                               "timings": timings}
        raise
    finally:
        if success and not args.keep_work:
            try:
                shutil.rmtree(work)
            except OSError as cleanup_error:
                log(f"Scene saved; temporary files retained at {work}: {cleanup_error}")


def parser():
    ap = argparse.ArgumentParser(prog="agr", description="RenderLock — offline replay-to-Blender converter")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("gui", help="Open the converter window")
    inspect = sub.add_parser("inspect", help="Read replay metadata")
    inspect.add_argument("--demo", type=Path, required=True)
    doctor = sub.add_parser("doctor", help="Show available dependencies")
    doctor.add_argument("--blender")
    compat = sub.add_parser("compat", help="Check installed game resources against qualified identities")
    compat.add_argument("--game-install", type=Path, required=True)
    compat.add_argument("--map-name")
    compat.add_argument("--asset-cache", type=Path)
    cv = sub.add_parser("convert", help="Build a new scene from a replay or saved scene package")
    source = cv.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", type=Path)
    source.add_argument("--segment", type=Path)
    asset_source = cv.add_mutually_exclusive_group()
    asset_source.add_argument("--assets", type=Path, help="Use an existing prepared asset folder")
    asset_source.add_argument("--game-install", type=Path,
                              help="Read assets from this Deadlock installation; auto-discover if neither source is supplied")
    cv.add_argument("--asset-cache", type=Path, help="Folder for generated assets (defaults to your user cache)")
    cv.add_argument("--source2viewer", type=Path, help="Source 2 Viewer CLI executable for asset conversion")
    cv.add_argument("--map-name", help="Installed map archive name, e.g. dl_midtown; never inferred from the demo label 'start'")
    cv.add_argument("--include-map", action="store_true", help="Prepare an explicitly selected installed map region; character beta omits its geometry while retaining requested lighting data")
    cv.add_argument("--map-margin", type=float, default=26.0, help="Extra map margin in metres around selected replay positions")
    cv.add_argument('--source-lighting', action='store_true',
                    help='Partial Source lighting from installed data; use --source-hero-lighting for character probes/reflections and --partial-lighting for lighting-only consent; static-map lighting is separately qualified')
    cv.add_argument('--world-region-probe', type=Path, help='Qualified WorldRegionProbe.dll for --source-lighting')
    cv.add_argument('--lighting-dotnet', type=Path, help='.NET executable for --source-lighting')
    cv.add_argument('--source-npr-lighting',action='store_true',help='Partial NPR indirect with reference assumptions; requires --source-hero-lighting and explicit lighting consent (--partial-lighting preserves strict character data)')
    cv.add_argument('--character-shader',type=Path,help='Qualified user-supplied Elagrimm BLENDER-Deadlock-Shader.blend; partial Eevee Source-lighting bridge and artistic rim; requires --source-npr-lighting')
    cv.add_argument('--character-only',action='store_true',help='Omit map geometry from the Blender scene while retaining prepared lighting assets and coordinates for a separate map')
    cv.add_argument('--external-map',type=Path,help='Append the supported downloaded RenderLockMap.blend; texture relinking and cutting remain manual, independently of Source lighting')
    cv.add_argument('--map-viewport-proxy',action='store_true',help='With the current downloaded map, add an approximate untextured viewport proxy while keeping the full map for rendering')
    cv.add_argument('--source-hero-lighting',action='store_true',help='Experimental partial hero probes/reflections; unit intensity/tint and viewer normalization assumptions; requires --source-lighting')
    cv.add_argument('--probe-atlas-decoder',type=Path,help='Qualified ProbeAtlasDecode.dll for --source-hero-lighting')
    cv.add_argument('--cubemap-decoder',type=Path,help='Qualified CubemapDecode.dll for --source-hero-lighting')
    cv.add_argument("--no-effects", action="store_true", help="Skip recorded particle extraction and supported effect rendering")
    cv.add_argument("--particle-probe",type=Path,help="Experimental recorded-effect simulation using AGR ParticleProbe.dll or portable ParticleProbe.exe")
    cv.add_argument("--dotnet",help="Dotnet executable for --particle-probe")
    cv.add_argument("--material-repairs", action="store_true",
                    help="Apply experimental hero unlit/emission repairs and calibrated glass; Source shader parity is not implied")
    cv.add_argument('--model-constraints', action='store_true',
                    help='Evaluate supported authored bone constraints from installed models; unsupported bindings fail explicitly')
    cv.add_argument("--secondary-motion", "--cloth", dest="secondary_motion", action="store_true",
                    help="Bake supported version-matched cloth from installed character assets; see per-entity coverage and limitations")
    cv.add_argument("--output", type=Path, required=True)
    cv.add_argument("--start-tick", type=int)
    cv.add_argument('--particle-history-ticks', type=int, default=0,
                    help='Extra earlier replay ticks to evaluate for particle history; clip timing is unchanged and older effects remain unsupported')
    cv.add_argument("--end-tick", type=int)
    cv.add_argument("--fps", type=int, default=64)
    cv.add_argument('--shot-hero', type=int, help='Optional hero ID for a fitted follow camera instead of the overview')
    cv.add_argument("--heroes", type=hero_list,
                    help="Optional comma-separated hero IDs; omit to include all heroes in the range")
    cv.add_argument("--blender")
    cv.add_argument("--blender-python", help=argparse.SUPPRESS)
    cv.add_argument("--partial", action="store_true", help="Allow unsupported animation/models with explicit coverage diagnostics")
    cv.add_argument('--partial-cloth',action='store_true',
                    help='Keep supported cloth and report omitted/failed cloth; character models and animation remain strict')
    cv.add_argument('--partial-lighting',action='store_true',help='Accept artistic/reference lighting limits while keeping character models and animation strict')
    cv.add_argument("--motion-only", action="store_true", help="Explicitly export movement without skeletal evaluation")
    cv.add_argument("--overwrite", action="store_true")
    cv.add_argument("--keep-work", action="store_true")
    nr=sub.add_parser('render-npr',help='Render explicit partial NPR frames from a qualified generic-lighting scene and prepared runtime segment')
    nr.add_argument('--blender',type=Path,required=True)
    nr.add_argument('--scene',type=Path,required=True)
    nr.add_argument('--segment',type=Path,required=True)
    nr.add_argument('--output',type=Path,required=True)
    nr.add_argument('--frames',type=int,nargs='+',required=True)
    nr.add_argument('--partial',action='store_true',help='Accept recorded reference lighting assumptions and incomplete Source fidelity')
    return ap


def main(argv=None):
    from .console import configure_stdio
    configure_stdio()
    args = parser().parse_args(argv)
    try:
        if args.command in (None, "gui"):
            from .gui import main as gui_main
            return gui_main()
        elif args.command == "inspect":
            from .replay import inspect_replay
            print(json.dumps(inspect_replay(args.demo), indent=2))
        elif args.command == "doctor":
            import importlib.util
            from .replay import find_native_extractor
            native = find_native_extractor()
            state = {"version": __version__, "python": sys.version, "modules": {m: importlib.util.find_spec(m) is not None for m in ["numpy", "boon", "polars", "snappy", "tkinter"]}, "native_extractor": str(native) if native else None, "blender": args.blender or shutil.which("blender")}
            print(json.dumps(state, indent=2))
        elif args.command == "compat":
            from .compatibility import check_game_install
            report = check_game_install(args.game_install, args.asset_cache, args.map_name)
            print(json.dumps(report, indent=2))
            return 0
        elif args.command == 'render-npr':
            from .npr_render_sequence import render_sequence
            render_sequence(args.blender,args.scene,args.segment,args.output,args.frames,allow_partial=args.partial)
        elif args.command == "convert":
            convert(args)
        return 0
    except KeyboardInterrupt:
        print("Conversion cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"AGR error: {exc}", file=sys.stderr)
        return 1
