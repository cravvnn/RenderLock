"""Prepare optional scene stages while retaining explicit, independent coverage."""
from __future__ import annotations

import json
from pathlib import Path


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_scene_extensions(segment_path, pack, provider, decoder, bindings, *,
                             demo=None, include_effects=True, include_map=False,
                             map_name=None, map_margin=26.0, secondary_motion=False, log=print,
                             simulate_effects=False, source_lighting=False,
                             lighting_probe=None, lighting_dotnet=None, source_hero_lighting=False,
                             atlas_probe=None, cube_probe=None, prepare_npr_materials=False, apply_npr_indirect=False,
                             character_only=False, npr_material_plan=None):
    if apply_npr_indirect and not prepare_npr_materials:
        raise ValueError('NPR application requires material preparation')
    if prepare_npr_materials and not source_hero_lighting:
        raise ValueError('NPR material preparation requires hero Source lighting')
    if source_hero_lighting and not source_lighting:
        raise ValueError('Hero Source lighting requires Source map lighting')
    if source_lighting and not include_map:
        raise ValueError('Partial Source lighting requires map preparation')
    prepare_hero=bool(source_hero_lighting or character_only and source_lighting)
    components=['diffuse','reflections'] if source_hero_lighting else ['diffuse']
    report = {name: {"status": "not_requested"}
              for name in ("particles", "map", "secondary_motion", "hero_lighting", "npr_materials")}
    data = _read(segment_path)
    data["scene_options"] = {"effects": bool(include_effects), "map": bool(include_map),
                             "map_name": map_name or getattr(provider, "map_name", None),
                             "map_margin_metres": float(map_margin),
                             "secondary_motion": bool(secondary_motion)}
    data['scene_options']['source_lighting'] = 'partial_static_diffuse' if source_lighting else 'not_requested'
    data.pop("effects", None)
    data.pop("map", None)
    data.pop('hero_lighting', None)
    data.pop('npr_materials', None)
    data.pop('npr_runtime', None)
    data.pop('hero_lighting_components',None)
    data['scene_options']['npr_materials']='prepare_partial' if prepare_npr_materials else 'not_requested'
    if prepare_hero:
        data['scene_options']['hero_lighting']='partial_indirect_reference'
        data['hero_lighting_components']=components
    for entity in data["entities"]:
        entity.pop("secondary_motion_file", None)
    data.setdefault("coverage", {})["scene_extras"] = report
    _write(segment_path, data)

    if prepare_hero:
        from .source_hero_lighting import preflight_hero_lighting
        log('Checking Source/NPR lighting region before map preparation...')
        try:
            preflight_hero_lighting(data, provider, decoder, map_name=map_name,
                                    margin_metres=map_margin)
        except Exception as exc:
            report['hero_lighting'] = dict(status='failed', stage='preflight',
                                           error=str(exc), error_type=type(exc).__name__)
            if isinstance(getattr(exc, 'details', None), dict):
                report['hero_lighting']['details'] = exc.details
            if prepare_npr_materials:
                report['npr_materials'] = dict(status='unavailable', stage='preflight',
                                              reason='Required hero Source lighting was not prepared.')
            data['coverage']['scene_extras'] = report
            _write(segment_path, data)
            _write(Path(pack) / 'scene_extension_report.json', report)
            raise

    def stage(name, callback):
        log(f"Preparing scene {name.replace('_', ' ')}...")
        try:
            result = callback()
            report[name] = result if isinstance(result, dict) else {"status": "prepared"}
        except Exception as exc:
            report[name] = {"status": "failed", "stage": "preparation",
                            "error": str(exc), "error_type": type(exc).__name__}
            log(f"WARNING: Scene {name} was not prepared: {exc}. Hero export continues; see the coverage report.")
        _write(Path(pack) / "scene_extension_report.json", report)

    if include_effects:
        def particles():
            from .particle_events import (extract_particle_events, compile_particle_timeline,
                                          resolve_particle_instance_resources)
            from .installed_effects import prepare_installed_effects
            selected = _read(segment_path)
            if demo is not None:
                raw = extract_particle_events(demo, selected["start_tick"], selected["end_tick"], progress=log)
                timeline = compile_particle_timeline(raw["records"], provider.resources,
                    selected["start_tick"], selected["end_tick"],
                    tick_rate=selected["tick_rate"], entities=selected["entities"])
                selected["particle_instances"] = timeline["instances"]
                selected["particle_extraction"] = {key: value for key, value in raw.items() if key != "records"}
                _write(Path(pack) / "particle_timeline_report.json", {key: value for key, value in timeline.items() if key != "instances"})
                _write(segment_path, selected)
            elif "particle_instances" not in selected:
                return {"status": "unavailable", "reason": "Saved package has no recorded particle events. Convert the original .dem to add effects."}
            else:
                # 0.1.7 saved valid hashes/events but could leave all resource
                # names null. Repair those names against the current inventory
                # without resampling movement or requiring another demo scan.
                selected["particle_instances"], resolution = resolve_particle_instance_resources(
                    selected["particle_instances"], provider.resources)
                selected["particle_resource_resolution"] = resolution
                _write(segment_path, selected)
                log(f"Saved particle resources: {resolution['newly_resolved']} names repaired; "
                    f"{resolution['unresolved_after']} remain unresolved.")
            return prepare_installed_effects(segment_path, pack, provider, decoder, log=log,attachments_only=simulate_effects)
        stage("particles", particles)
    if include_map:
        def map_geometry():
            from .installed_map import prepare_installed_map
            from .game_assets import GameAssetProvider
            selected = _read(segment_path)
            map_provider = GameAssetProvider(provider.install_path, provider.cache_dir,
                map_name=map_name or getattr(provider, "map_name", None))
            if character_only and source_lighting:
                from .source_hero_lighting import prepare_character_lighting_descriptor
                descriptor=prepare_character_lighting_descriptor(selected,map_provider,decoder,
                    margin_metres=map_margin)
            elif source_lighting:
                from .source_region import prepare_source_map
                descriptor = prepare_source_map(selected,map_provider,decoder,probe=lighting_probe,
                    dotnet=lighting_dotnet,margin_metres=map_margin,allow_partial=True,log=log)
            else:
                descriptor = prepare_installed_map(selected, map_provider, decoder, margin_metres=map_margin, log=log)
            selected["map"] = descriptor
            _write(segment_path, selected)
            return descriptor
        stage("map", map_geometry)
    if prepare_hero:
        from .source_hero_lighting import prepare_hero_lighting, actor_lighting_regions
        from .game_assets import GameAssetProvider
        log('Preparing explicit partial hero Source lighting...')
        try:
            selected=_read(segment_path)
            map_provider=GameAssetProvider(provider.install_path,provider.cache_dir,map_name=map_name or getattr(provider,'map_name',None))
            package=prepare_hero_lighting(selected.get('map',{}),map_provider,decoder,
                atlas_probe=atlas_probe,cube_probe=cube_probe,dotnet=lighting_dotnet,allow_partial=True,
                source_regions=actor_lighting_regions(selected,map_margin))
            selected['hero_lighting']=package
            _write(segment_path,selected)
            report['hero_lighting']={'status':'prepared_partial','mode':package['mode'],
                'assumptions':package['assumptions'],'limits':package['limits'],
                'probe_count':len(package['plan']['probes']),'coverage':'requires_evaluated_surface_validation',
                'requested_components':components,'applied_components':[]}
        except Exception as exc:
            report['hero_lighting']={'status':'failed','stage':'preparation','error':str(exc),'error_type':type(exc).__name__}
            _write(Path(pack)/'scene_extension_report.json',report)
            failed = _read(segment_path)
            failed.pop('hero_lighting', None)
            failed.setdefault('coverage', {})['scene_extras'] = report
            _write(segment_path, failed)
            raise
    if prepare_npr_materials:
        from .source_npr_scene import prepare_scene_npr_materials
        try:
            selected=_read(segment_path)
            package=prepare_scene_npr_materials(selected,provider,decoder,
                manifest_dir=Path(segment_path).parent,allow_partial=True,plan=npr_material_plan)
            selected['npr_materials']=package
            if apply_npr_indirect:
                selected['npr_runtime']=dict(mode='partial_indirect_reference',assume_camera_forward=True,assume_white_instance_color=True)
            _write(segment_path,selected)
            report['npr_materials']={'status':package['status'],
                'entity_count':len(package['entities']),'model_count':len(package['models']),
                'shader_applied':False,'limits':package['limits']}
        except Exception as exc:
            report['npr_materials']={'status':'failed','stage':'preparation',
                'error':str(exc),'error_type':type(exc).__name__}
            _write(Path(pack)/'scene_extension_report.json',report)
            failed=_read(segment_path);failed.pop('npr_materials',None)
            failed.setdefault('coverage',{})['scene_extras']=report
            _write(segment_path,failed)
            raise
    if secondary_motion:
        def cloth():
            from .installed_cloth import prepare_cloth_assets
            selected = _read(segment_path)
            result = prepare_cloth_assets(selected, bindings, provider, decoder, pack, log=log)
            _write(segment_path, selected)
            return result
        stage("secondary_motion", cloth)
    data = _read(segment_path)
    data.setdefault("coverage", {})["scene_extras"] = report
    _write(segment_path, data)
    _write(Path(pack) / "scene_extension_report.json", report)
    return report
