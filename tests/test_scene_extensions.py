"""Independent orchestration/portability tests; no game files or Blender required."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agr import cli
from agr.scene_extensions import prepare_scene_extensions
from agr.scene_package import read_scene, save_scene


@pytest.fixture(autouse=True)
def lighting_inventory_stub(monkeypatch):
    # Orchestration fixtures have no installed map; selection/budgets have their
    # own tests. Tests below can replace this stub to exercise early failure.
    from agr import source_hero_lighting
    monkeypatch.setattr(source_hero_lighting, 'preflight_hero_lighting', lambda *a, **k: {})


def test_oversized_lighting_region_stops_before_map_or_other_optional_conversion(tmp_path, monkeypatch):
    from agr import source_hero_lighting, source_region, installed_effects
    from agr.source_probe_assets import validate_probe_budget, LightingRegionError
    original = scene()
    manifest = save_scene(original, tmp_path / 'segment.json')
    def fail(*args, **kwargs):
        validate_probe_budget(76, 555673328)
    monkeypatch.setattr(source_hero_lighting, 'preflight_hero_lighting', fail)
    monkeypatch.setattr(source_region, 'prepare_source_map', lambda *a, **k: pytest.fail('Map conversion started'))
    monkeypatch.setattr(installed_effects, 'prepare_installed_effects', lambda *a, **k: pytest.fail('Effects conversion started'))
    with pytest.raises(LightingRegionError):
        prepare_scene_extensions(manifest, tmp_path, object(), object(), [],
            include_map=True, source_lighting=True, source_hero_lighting=True,
            prepare_npr_materials=True, log=lambda _: None)
    saved = json.loads(manifest.read_text())
    report = json.loads((tmp_path / 'scene_extension_report.json').read_text())
    assert saved['entities'] == original['entities']
    assert saved['coverage']['scene_extras'] == report
    assert report['hero_lighting']['stage'] == 'preflight'
    assert report['hero_lighting']['details']['selected_probe_count'] == 76
    assert report['npr_materials']['status'] == 'unavailable'
    assert 'hero_lighting' not in saved and 'npr_runtime' not in saved


def scene():
    return {"schema_version": 1, "tick_rate": 64, "start_tick": 10, "end_tick": 11,
            "metadata": {}, "markers": [{"tick": 10, "label": "recorded event"}],
            "entities": [{"id": "84:333", "hero_id": 13, "hero_name": "Haze",
                          "samples": [{"tick": tick, "position": [tick, 2, 3],
                                       "body_angles": [0, 180, 0]} for tick in (10, 11)]}],
            "particle_instances": [{"id": "12:1", "created_tick": 10,
                                    "resource": "particles/test_muzzle.vpcf_c"}]}


@pytest.mark.parametrize("failed_stage", ["particles", "map", "secondary_motion"])
def test_optional_failure_preserves_hero_tracks_and_continues_other_stages(tmp_path, monkeypatch, failed_stage):
    from agr import installed_effects, installed_map, installed_cloth, particle_events, game_assets
    original = scene()
    original["entities"][0].update(model_file="cached-model.glb", pose_file="poses.npz", skeleton_file="rig.json")
    manifest = save_scene(original, tmp_path / "segment.json")
    pack = tmp_path / "assets"
    pack.mkdir()
    calls, logs = [], []
    provider = SimpleNamespace(map_name=None, resources={}, install_path=tmp_path / "game", cache_dir=tmp_path / "cache")
    mounted = []
    def mount(install, cache, *, map_name):
        assert install == provider.install_path and cache == provider.cache_dir
        mounted.append(map_name)
        return SimpleNamespace(map_name=map_name)
    monkeypatch.setattr(game_assets, "GameAssetProvider", mount)

    def entry(stage):
        calls.append(stage)
        if failed_stage == stage:
            raise RuntimeError(stage + " fixture failure")

    def effects(path, *args, **kwargs):
        entry("particles")
        data = json.loads(Path(path).read_text())
        data["effects"] = {"manifest_file": str(pack / "effects.json"), "status": "ready"}
        Path(path).write_text(json.dumps(data))
        return {"status": "ready", "prepared_layers": ["muzzle"]}

    def map_geometry(data, map_provider, *args, **kwargs):
        entry("map")
        assert map_provider is not provider and map_provider.map_name == "dl_midtown"
        assert kwargs["margin_metres"] == 31.5
        return {"status": "ready", "model_file": str(pack / "map.gltf")}

    def cloth(data, *args, **kwargs):
        entry("secondary_motion")
        data["entities"][0]["secondary_motion_file"] = str(pack / "hoses.json")
        return {"status": "prepared", "supported_entities": ["84:333"]}

    monkeypatch.setattr(installed_effects, "prepare_installed_effects", effects)
    monkeypatch.setattr(installed_map, "prepare_installed_map", map_geometry)
    monkeypatch.setattr(installed_cloth, "prepare_cloth_assets", cloth)
    monkeypatch.setattr(particle_events, "extract_particle_events", lambda *a, **k: pytest.fail("Saved events must be reused"))
    report = prepare_scene_extensions(manifest, pack, provider, object(), [],
                                      include_map=True, map_name="dl_midtown", map_margin=31.5,
                                      secondary_motion=True, log=logs.append)
    saved = json.loads(manifest.read_text())
    assert calls == ["particles", "map", "secondary_motion"]
    assert mounted == ["dl_midtown"]
    assert report[failed_stage]["status"] == "failed"
    assert report[failed_stage]["error_type"] == "RuntimeError"
    assert all(report[name]["status"] != "failed" for name in report if name != failed_stage)
    assert saved["coverage"]["scene_extras"] == report
    assert json.loads((pack / "scene_extension_report.json").read_text()) == report
    for key in ("id", "samples", "model_file", "pose_file", "skeleton_file"):
        assert saved["entities"][0][key] == original["entities"][0][key]
    assert saved["particle_instances"] == original["particle_instances"]
    assert any("Hero export continues" in message for message in logs)


def test_missing_optional_map_archive_does_not_block_particles_or_hero_export(tmp_path, monkeypatch):
    from agr import game_assets, installed_map, installed_effects
    original = scene()
    manifest = save_scene(original, tmp_path / "segment.json")
    calls = []
    provider = SimpleNamespace(install_path=tmp_path / "game", cache_dir=tmp_path / "cache", map_name=None, resources={})
    def missing_archive(install, cache, *, map_name):
        calls.append((install, cache, map_name))
        raise FileNotFoundError("dl_midtown.vpk is missing")
    monkeypatch.setattr(game_assets, "GameAssetProvider", missing_archive)
    monkeypatch.setattr(installed_map, "prepare_installed_map", lambda *a, **k: pytest.fail("Map mount already failed"))
    monkeypatch.setattr(installed_effects, "prepare_installed_effects", lambda *a, **k: {"status": "ready"})
    report = prepare_scene_extensions(manifest, tmp_path, provider, None, [],
                                      include_map=True, map_name="dl_midtown", log=lambda _: None)
    assert calls == [(provider.install_path, provider.cache_dir, "dl_midtown")]
    assert report["particles"]["status"] == "ready"
    assert report["map"]["status"] == "failed"
    assert report["map"]["error_type"] == "FileNotFoundError"
    saved = json.loads(manifest.read_text())
    assert saved["entities"] == original["entities"]
    assert saved["scene_options"]["map_name"] == "dl_midtown"
    assert "map" not in saved


def test_recorded_events_survive_effect_preparation_failure(tmp_path, monkeypatch):
    from agr import particle_events, installed_effects
    original = scene()
    original.pop("particle_instances")
    manifest = save_scene(original, tmp_path / "segment.json")
    records = [{"tick": 10, "type": "create"}]
    instances = [{"id": "7:1", "created_tick": 10, "resource": "particles/new.vpcf_c"}]
    seen = []

    def extract(demo, start, end, **kwargs):
        seen.append((demo, start, end))
        return {"records": records, "status": "complete", "record_count": 1}

    def timeline(raw, resources, start, end, **kwargs):
        assert raw is records and resources == {"resources": "fixture"}
        assert kwargs["entities"] == original["entities"]
        return {"instances": instances, "instance_count": 1}

    monkeypatch.setattr(particle_events, "extract_particle_events", extract)
    monkeypatch.setattr(particle_events, "compile_particle_timeline", timeline)
    def fail(*args, **kwargs):
        raise OSError("converter unavailable")
    monkeypatch.setattr(installed_effects, "prepare_installed_effects", fail)
    demo = tmp_path / "replay.dem"
    report = prepare_scene_extensions(manifest, tmp_path,
        SimpleNamespace(map_name=None, resources={"resources": "fixture"}), object(), [], demo=demo, log=lambda _: None)
    saved = json.loads(manifest.read_text())
    assert seen == [(demo, 10, 11)]
    assert saved["particle_instances"] == instances
    assert saved["particle_extraction"] == {"status": "complete", "record_count": 1}
    assert report["particles"]["status"] == "failed"
    assert saved["entities"] == original["entities"]


def test_disabled_stages_clear_previous_derived_references_but_keep_recorded_events(tmp_path, monkeypatch):
    from agr import installed_effects, installed_map, installed_cloth
    original = scene()
    original.update(effects={"manifest_file": "/old-job/effects.json"}, map={"model_file": "/old-job/map.gltf"})
    original["entities"][0]["secondary_motion_file"] = "/old-job/hoses.json"
    manifest = save_scene(original, tmp_path / "segment.json")
    for module, attribute in ((installed_effects, "prepare_installed_effects"),
                              (installed_map, "prepare_installed_map"),
                              (installed_cloth, "prepare_cloth_assets")):
        monkeypatch.setattr(module, attribute, lambda *a, **k: pytest.fail("Disabled stage was run"))
    report = prepare_scene_extensions(manifest, tmp_path, SimpleNamespace(map_name="dl_midtown"), None, [],
                                      include_effects=False, include_map=False, secondary_motion=False, log=lambda _: None)
    saved = json.loads(manifest.read_text())
    assert all(value["status"] == "not_requested" for value in report.values())
    assert "map" not in saved and "effects" not in saved
    assert "secondary_motion_file" not in saved["entities"][0]
    assert saved["particle_instances"] == original["particle_instances"]
    assert saved["markers"] == original["markers"]
    assert saved["scene_options"] == {"effects": False, "map": False, "map_name": "dl_midtown",
                                       "map_margin_metres": 26.0, "secondary_motion": False,
                                       "source_lighting": "not_requested", "npr_materials": "not_requested"}


def test_old_saved_package_without_events_reports_unavailable(tmp_path, monkeypatch):
    from agr import installed_effects
    original = scene()
    original.pop("particle_instances")
    manifest = save_scene(original, tmp_path / "segment.json")
    monkeypatch.setattr(installed_effects, "prepare_installed_effects", lambda *a, **k: pytest.fail("No recorded events"))
    report = prepare_scene_extensions(manifest, tmp_path, SimpleNamespace(map_name=None), None, [], log=lambda _: None)
    assert report["particles"]["status"] == "unavailable"
    assert "original .dem" in report["particles"]["reason"]
    assert json.loads(manifest.read_text())["entities"] == original["entities"]


@pytest.fixture
def gui_fields(tmp_path):
    demo, blender, converter = (tmp_path / name for name in ("replay.dem", "blender.exe", "Source2Viewer-CLI.exe"))
    for path in (demo, blender, converter):
        path.write_bytes(b"test fixture; not executed")
    install = tmp_path / "Steam Library" / "Deadlock"
    install.mkdir(parents=True)
    return {"input": str(demo), "blender": str(blender), "source2viewer": str(converter),
            "asset_source": "installed", "game_install": str(install), "output": str(tmp_path / "shot.blend"),
            "mode": "replay", "start": "10", "end": "11", "fps": "64", "heroes": "",
            "effects_enabled": "1", "map_enabled": "1", "map_name": "dl_midtown", "map_margin": "31.5"}


def test_gui_scene_settings_round_trip_through_cli_parser(gui_fields):
    from agr.gui import build_convert_command
    args = cli.parser().parse_args(build_convert_command(gui_fields)[3:])
    assert args.include_map and args.map_name == "dl_midtown" and args.map_margin == 31.5
    assert not args.no_effects and args.secondary_motion
    disabled = dict(gui_fields, effects_enabled="0", map_enabled="0", map_name="", map_margin="not needed")
    args = cli.parser().parse_args(build_convert_command(disabled)[3:])
    assert args.no_effects and not args.include_map and args.map_name is None


def test_gui_partial_source_lighting_round_trip(gui_fields,monkeypatch):
    from agr.gui import build_convert_command
    from agr import source_region
    monkeypatch.setattr(source_region,'regional_command',lambda *args:([],{}))
    enabled=dict(gui_fields,source_lighting='1',world_region_probe='WorldRegionProbe.dll',lighting_dotnet='dotnet.exe')
    args=cli.parser().parse_args(build_convert_command(enabled)[3:])
    assert args.source_lighting and not args.partial
    assert args.world_region_probe==Path('WorldRegionProbe.dll')
    assert args.lighting_dotnet==Path('dotnet.exe')
    with pytest.raises(ValueError,match='requires installed assets'):
        build_convert_command(dict(enabled,map_enabled='0'))


def test_gui_hero_lighting_round_trip_and_required_map_option(gui_fields,monkeypatch):
    from agr.gui import build_convert_command
    from agr import source_region,source_hero_lighting
    monkeypatch.setattr(source_region,'regional_command',lambda *a:([],{}))
    calls=[];monkeypatch.setattr(source_hero_lighting,'validate_hero_decoder_paths',lambda *a:calls.append(a))
    values=dict(gui_fields,source_lighting='1',source_hero_lighting='1',world_region_probe='WorldRegionProbe.dll',lighting_dotnet='dotnet.exe',probe_atlas_decoder='ProbeAtlasDecode.dll',cubemap_decoder='CubemapDecode.dll')
    args=cli.parser().parse_args(build_convert_command(values)[3:])
    assert args.source_hero_lighting and args.probe_atlas_decoder==Path('ProbeAtlasDecode.dll')
    assert args.cubemap_decoder==Path('CubemapDecode.dll') and calls
    with pytest.raises(ValueError,match='requires Partial'):build_convert_command(dict(values,source_lighting='0'))


def test_hero_lighting_preparation_is_preserved_and_failure_propagates(tmp_path,monkeypatch):
    from agr import source_region,source_hero_lighting,game_assets
    manifest=save_scene(scene(),tmp_path/'segment.json');pack=tmp_path/'pack';pack.mkdir()
    provider=SimpleNamespace(install_path=tmp_path/'game',cache_dir=tmp_path/'cache',map_name='test')
    monkeypatch.setattr(game_assets,'GameAssetProvider',lambda *a,**k:provider)
    monkeypatch.setattr(source_region,'prepare_source_map',lambda *a,**k:{'status':'ready'})
    package={'mode':'partial_hero_indirect_reference','assumptions':{'explicit':True},'limits':['reference'],'plan':{'probes':[{}]}}
    monkeypatch.setattr(source_hero_lighting,'prepare_hero_lighting',lambda *a,**k:package)
    kwargs=dict(include_effects=False,include_map=True,source_lighting=True,source_hero_lighting=True,atlas_probe='atlas',cube_probe='cube',log=lambda _:None)
    report=prepare_scene_extensions(manifest,pack,provider,None,[],**kwargs)
    assert json.loads(manifest.read_text())['hero_lighting']==package
    assert report['hero_lighting']['status']=='prepared_partial'
    assert json.loads((pack/'scene_extension_report.json').read_text())==report
    assert json.loads(manifest.read_text())['coverage']['scene_extras']==report
    def fail(*a,**k):raise ValueError('Changed source')
    monkeypatch.setattr(source_hero_lighting,'prepare_hero_lighting',fail)
    with pytest.raises(ValueError,match='Changed source'):prepare_scene_extensions(manifest,pack,provider,None,[],**kwargs)
    assert 'hero_lighting' not in json.loads(manifest.read_text())
    assert json.loads((pack/'scene_extension_report.json').read_text())['hero_lighting']['status']=='failed'
    assert json.loads(manifest.read_text())['coverage']['scene_extras']==json.loads((pack/'scene_extension_report.json').read_text())


def test_hero_provider_failure_is_recorded_and_propagates(tmp_path,monkeypatch):
    from agr import source_region,game_assets
    manifest=save_scene(scene(),tmp_path/'segment.json');pack=tmp_path/'pack';pack.mkdir()
    provider=SimpleNamespace(install_path=tmp_path/'game',cache_dir=tmp_path/'cache',map_name='test')
    mounts=[]
    def mount(*args,**kwargs):
        mounts.append(True)
        if len(mounts)>1:raise OSError('Provider unavailable')
        return provider
    monkeypatch.setattr(game_assets,'GameAssetProvider',mount)
    monkeypatch.setattr(source_region,'prepare_source_map',lambda *a,**k:{'status':'ready'})
    with pytest.raises(OSError,match='Provider unavailable'):
        prepare_scene_extensions(manifest,pack,provider,None,[],include_effects=False,
            include_map=True,source_lighting=True,source_hero_lighting=True,log=lambda _:None)
    report=json.loads((pack/'scene_extension_report.json').read_text())
    assert report['hero_lighting']=={'status':'failed','stage':'preparation',
        'error':'Provider unavailable','error_type':'OSError'}
    assert 'hero_lighting' not in json.loads(manifest.read_text())
    assert json.loads(manifest.read_text())['coverage']['scene_extras']==report


def test_partial_lighting_uses_regional_preparation_not_whole_map(tmp_path,monkeypatch):
    from agr import source_region, installed_map, game_assets
    manifest=save_scene(scene(),tmp_path/'segment.json');pack=tmp_path/'pack';pack.mkdir()
    provider=SimpleNamespace(install_path=tmp_path/'game',cache_dir=tmp_path/'cache',map_name='test')
    monkeypatch.setattr(game_assets,'GameAssetProvider',lambda *a,**k:provider)
    monkeypatch.setattr(installed_map,'prepare_installed_map',lambda *a,**k:pytest.fail('Whole-map exporter used'))
    calls=[]
    def prepare(*args,**kwargs):
        calls.append(kwargs)
        return {'status':'ready','lighting':{'allow_partial':True,'mode':'static_diffuse'}}
    monkeypatch.setattr(source_region,'prepare_source_map',prepare)
    report=prepare_scene_extensions(manifest,pack,provider,None,[],include_effects=False,
        include_map=True,source_lighting=True,lighting_probe='helper',lighting_dotnet='dotnet',log=lambda _:None)
    saved=json.loads(manifest.read_text())
    assert calls[0]['allow_partial'] is True and calls[0]['probe']=='helper'
    assert report['map']['lighting']['mode']=='static_diffuse'
    assert saved['scene_options']['source_lighting']=='partial_static_diffuse'
    assert saved['entities']==scene()['entities']


@pytest.mark.parametrize("margin", ["nan", "inf", "0", "-2", "1001", "invalid"])
def test_gui_rejects_invalid_map_margin_before_starting_conversion(gui_fields, margin):
    from agr.gui import build_convert_command
    with pytest.raises(ValueError, match="Map margin"):
        build_convert_command(dict(gui_fields, map_margin=margin))


def test_gui_requires_explicit_map_name(gui_fields):
    from agr.gui import build_convert_command
    with pytest.raises(ValueError, match="installed map name"):
        build_convert_command(dict(gui_fields, map_name=""))


@pytest.mark.parametrize("options, message", [(["--include-map"], "explicit installed --map-name"),
                                             (["--map-margin", "nan"], "Map margin")])
def test_cli_scene_validation_precedes_input_parsing_or_asset_discovery(tmp_path, options, message):
    args = cli.parser().parse_args(["convert", "--demo", str(tmp_path / "missing.dem"),
                                   "--output", str(tmp_path / "shot.blend"), *options])
    with pytest.raises(ValueError, match=message):
        cli.convert(args)
    assert not list(tmp_path.glob("agr-*"))


def test_published_package_retains_events_options_and_poses_without_derived_import_paths(tmp_path):
    work = tmp_path / "transient-job"
    work.mkdir()
    pose, skeleton = work / "poses.npz", work / "rig.json"
    pose.write_bytes(b"portable pose fixture")
    skeleton.write_text("{}")
    original = scene()
    original["entities"][0].update(pose_file=str(pose), skeleton_file=str(skeleton),
                                   model_file=str(work / "model.glb"), secondary_motion_file=str(work / "hoses.json"))
    original.update(effects={"manifest_file": str(work / "effects.json"), "report_file": str(work / "effects-report.json")},
                    map={"model_file": str(work / "map.gltf"), "report_file": str(work / "map-report.json")},
                    scene_options={"effects": True, "map": True, "map_name": "dl_midtown",
                                   "map_margin_metres": 26.0, "secondary_motion": False},
                    particle_extraction={"status": "complete", "record_count": 1})
    manifest = save_scene(original, work / "segment.json")
    original['hero_lighting']={'derived_cache_path':str(work/'hero-lighting')}
    original['npr_materials']={'derived_cache_path':str(work/'npr-materials')}
    manifest = save_scene(original, work / 'segment.json')
    destination = tmp_path / "shot.agrscene"
    cli.publish_package(manifest, destination)
    package = json.loads((destination / "manifest.json").read_text())
    assert "effects" not in package and "map" not in package
    assert 'hero_lighting' not in package
    assert 'npr_materials' not in package
    assert "model_file" not in package["entities"][0] and "secondary_motion_file" not in package["entities"][0]
    for key in ("particle_instances", "particle_extraction", "scene_options", "markers"):
        assert package[key] == original[key]
    assert package["entities"][0]["samples"] == original["entities"][0]["samples"]
    assert not Path(package["entities"][0]["pose_file"]).is_absolute()
    # Delete the original work inputs to prove packaged tracks are independent.
    pose.unlink()
    skeleton.unlink()
    restored = read_scene(destination)
    assert Path(restored["entities"][0]["pose_file"]).read_bytes() == b"portable pose fixture"
    assert Path(restored["entities"][0]["skeleton_file"]).read_text() == "{}"
    unchanged = json.loads(manifest.read_text())
    assert unchanged["effects"] == original["effects"]
    assert unchanged["entities"][0]["secondary_motion_file"] == original["entities"][0]["secondary_motion_file"]


def test_saved_017_null_particle_names_repaired_before_preparation(tmp_path, monkeypatch):
    from agr import installed_effects, particle_events
    from agr.vendor.resource_hash import resource_id
    original = scene()
    path = 'particles/weapon_fx/haze/haze_muzzle_flash.vpcf'
    recorded = {'id': '7:1', 'created_tick': 10, 'resource': None,
                'resource_hash': str(resource_id(path)),
                'events': [{'tick': 10, 'type': 'CREATE', 'data': {'index': 7}}]}
    original['particle_instances'] = [recorded]
    manifest = save_scene(original, tmp_path / 'segment.json')
    seen = []
    def prepare(path, *args, **kwargs):
        loaded = json.loads(Path(path).read_text())
        seen.extend(loaded['particle_instances'])
        return {'status': 'prepared_supported_subset'}
    monkeypatch.setattr(installed_effects, 'prepare_installed_effects', prepare)
    monkeypatch.setattr(particle_events, 'extract_particle_events', lambda *a, **k: pytest.fail('Saved hashes do not require a demo scan'))
    provider = SimpleNamespace(map_name=None, resources={path + '_c': object()})
    report = prepare_scene_extensions(manifest, tmp_path, provider, None, [], log=lambda _: None)
    saved = json.loads(manifest.read_text())
    assert report['particles']['status'] == 'prepared_supported_subset'
    assert seen == [dict(recorded, resource=path)]
    assert saved['entities'] == original['entities']
    assert saved['particle_resource_resolution']['newly_resolved'] == 1
    assert saved['particle_resource_resolution']['unresolved_after'] == 0
    assert original['particle_instances'][0]['resource'] is None


def test_npr_scene_stage_records_preparation_and_propagates_failure(tmp_path,monkeypatch):
    from agr import source_region,source_hero_lighting,source_npr_scene,game_assets
    manifest=save_scene(scene(),tmp_path/'segment.json')
    provider=SimpleNamespace(install_path=tmp_path/'game',cache_dir=tmp_path/'cache',map_name='test')
    monkeypatch.setattr(game_assets,'GameAssetProvider',lambda *a,**k:provider)
    monkeypatch.setattr(source_region,'prepare_source_map',lambda *a,**k:{'status':'ready'})
    monkeypatch.setattr(source_hero_lighting,'prepare_hero_lighting',lambda *a,**k:
        dict(mode='partial',assumptions=[],limits=[],plan={'probes':[]}))
    package=dict(status='prepared_requires_blender_validation',entities=[{'entity_id':'a'}],models={'hash':{}},limits=['test'])
    monkeypatch.setattr(source_npr_scene,'prepare_scene_npr_materials',lambda *a,**k:package)
    kwargs=dict(include_effects=False,include_map=True,source_lighting=True,
        source_hero_lighting=True,prepare_npr_materials=True,apply_npr_indirect=True,log=lambda _:None)
    result=prepare_scene_extensions(manifest,tmp_path,provider,None,[],**kwargs)
    assert result['npr_materials']['shader_applied'] is False
    assert json.loads(manifest.read_text())['npr_materials']==package
    assert json.loads(manifest.read_text())['npr_runtime']['assume_camera_forward'] is True
    def fail(*a,**k):raise ValueError('unqualified material')
    monkeypatch.setattr(source_npr_scene,'prepare_scene_npr_materials',fail)
    with pytest.raises(ValueError,match='unqualified'):
        prepare_scene_extensions(manifest,tmp_path,provider,None,[],**kwargs)
    saved=json.loads(manifest.read_text())
    assert 'npr_materials' not in saved
    assert saved['coverage']['scene_extras']['npr_materials']['status']=='failed'


def test_npr_gui_option_requires_partial_and_propagates(gui_fields,monkeypatch):
    from agr import source_region,source_hero_lighting
    from agr.gui import build_convert_command
    monkeypatch.setattr(source_region,'regional_command',lambda *a:[])
    monkeypatch.setattr(source_hero_lighting,'validate_hero_decoder_paths',lambda *a:None)
    values=dict(gui_fields,source_lighting='1',source_hero_lighting='1',source_npr_lighting='1',world_region_probe='world',lighting_dotnet='dotnet',probe_atlas_decoder='atlas',cubemap_decoder='cube')
    with pytest.raises(ValueError,match='NPR indirect'):build_convert_command(values)
    with pytest.raises(ValueError,match='Follow camera'):build_convert_command(values,partial=True)
    args=cli.parser().parse_args(build_convert_command(dict(values,shot_hero='65'),partial=True)[3:])
    assert args.partial and args.source_npr_lighting and args.source_hero_lighting


def test_npr_cli_rejects_implicit_partial_before_input_work(tmp_path):
    args=cli.parser().parse_args(['convert','--demo',str(tmp_path/'missing.dem'),'--output',str(tmp_path/'scene.blend'),'--source-npr-lighting'])
    with pytest.raises(ValueError,match='source-npr-lighting'):cli.convert(args)
    assert not list(tmp_path.glob('agr-*'))


def test_installed_npr_rejects_missing_map_before_allocating(tmp_path):
    from agr.installed_assets import prepare_installed_assets
    with pytest.raises(ValueError,match='NPR preparation'):
        prepare_installed_assets(tmp_path/'missing.json',tmp_path/'work',tmp_path/'game',tmp_path/'cache',tmp_path/'converter',strict=False,source_hero_lighting=True,source_npr_lighting=True)
    assert not (tmp_path/'work').exists()


def test_character_shader_gui_requires_npr_and_roundtrips_separate_map(gui_fields,monkeypatch):
    from agr import source_region,source_hero_lighting,blender_character_shader
    from agr.gui import build_convert_command
    values=dict(gui_fields,character_shader='shader.blend',character_only='1')
    with pytest.raises(ValueError,match='Character shader'):
        build_convert_command(values,partial=True)
    monkeypatch.setattr(source_region,'regional_command',lambda *a:[])
    monkeypatch.setattr(source_hero_lighting,'validate_hero_decoder_paths',lambda *a:None)
    monkeypatch.setattr(blender_character_shader,'validate_character_shader_library',lambda p:Path(p))
    values.update(source_lighting='1',source_hero_lighting='1',source_npr_lighting='1',
                  world_region_probe='world',lighting_dotnet='dotnet',probe_atlas_decoder='atlas',
                  cubemap_decoder='cube',shot_hero='65')
    args=cli.parser().parse_args(build_convert_command(values,partial=True)[3:])
    assert args.character_shader==Path('shader.blend') and args.character_only


def test_character_shader_cli_rejects_implicit_lighting_before_allocating(tmp_path):
    args=cli.parser().parse_args(['convert','--demo',str(tmp_path/'missing.dem'),
        '--output',str(tmp_path/'scene.blend'),'--character-shader',str(tmp_path/'shader.blend')])
    with pytest.raises(ValueError,match='character-shader'):cli.convert(args)
    assert not list(tmp_path.glob('agr-*'))
