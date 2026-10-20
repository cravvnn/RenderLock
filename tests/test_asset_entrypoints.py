"""Exercise asset selection and command assembly without running game conversion."""
import json
from pathlib import Path
import sys
import types

import pytest

from agr import cli, game_assets
from agr.scene_package import read_scene, save_scene
from blender_fixture import write_blender_completion


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr('agr.blender_runtime.check_blender', lambda *a, **k: {'fixture': True})
    install = tmp_path / "Steam Library" / "Deadlock"
    install.mkdir(parents=True)
    converter = tmp_path / "Source2Viewer-CLI.exe"
    converter.write_bytes(b"test placeholder, not executed")
    engine = tmp_path / "blender.exe"
    engine.write_bytes(b"test placeholder, not executed")
    assets = tmp_path / "prepared"
    assets.mkdir()
    (assets / "model.blend").write_bytes(b"test placeholder, not imported")
    hero_ids = [1, 2, 4, 8, 13, 15, 20, 25, 31, 69, 76, 80]
    (assets / "assets.json").write_text(json.dumps({"heroes": {
        str(hero): {"model_file": "model.blend"} for hero in hero_ids}}))
    scene = {"schema_version": 1, "tick_rate": 64, "start_tick": 10, "end_tick": 10,
             "metadata": {}, "markers": [], "entities": [
                 {"id": str(hero), "hero_id": hero, "hero_name": f"Hero {hero}",
                  "samples": [{"tick": 10, "position": [0, 0, 0], "body_angles": [0, 0, 0]}]}
                 for hero in hero_ids]}
    segment = save_scene(scene, tmp_path / "input.json")
    # Keep real validation helpers available to lazily imported NPR/completion
    # code. Replacing the whole module made these tests depend on collection order.
    provider = game_assets
    monkeypatch.setattr(provider, 'discover_installations', lambda: [install])
    monkeypatch.setattr(provider, 'normalize_install_path', lambda path: Path(path).resolve())
    adapter = types.ModuleType("agr.installed_assets")
    adapter.prepare_installed_assets = lambda **kwargs: assets
    monkeypatch.setitem(sys.modules, "agr.installed_assets", adapter)
    def blender(command, check):
        write_blender_completion(command)
    monkeypatch.setattr(cli.subprocess, "run", blender)
    return types.SimpleNamespace(install=install, converter=converter, engine=engine,
                                 assets=assets, segment=segment, provider=provider,
                                 adapter=adapter, root=tmp_path)


def arguments(inputs, *extra):
    return cli.parser().parse_args([
        "convert", "--segment", str(inputs.segment), "--blender", str(inputs.engine),
        "--output", str(inputs.root / "shot.blend"), "--motion-only", *extra])


def test_explicit_prepared_pack_is_backward_compatible(inputs):
    def unexpected(**kwargs):
        pytest.fail("Prepared conversion must not enter installed asset preparation")
    inputs.adapter.prepare_installed_assets = unexpected
    report = cli.convert(arguments(inputs, "--assets", str(inputs.assets)))
    assert report["entities"] == 12
    assert report["coverage"]["hero_models"]["model_files_available"] == 12


def test_cli_sources_are_mutually_exclusive(inputs):
    with pytest.raises(SystemExit):
        arguments(inputs, "--assets", str(inputs.assets), "--game-install", str(inputs.install))


def test_installed_source_prepares_selected_segment_before_coverage(inputs):
    seen = []
    def prepare(**kwargs):
        seen.append(kwargs)
        data = read_scene(kwargs["segment_path"])
        assert len(data["entities"]) == 12
        assert "hero_models" not in data.get("coverage", {})
        data.setdefault("coverage", {})["installed_assets"] = {"status": "test_adapter_marker"}
        save_scene(data, kwargs["segment_path"])
        return inputs.assets
    inputs.adapter.prepare_installed_assets = prepare
    args = arguments(inputs, "--game-install", str(inputs.install), "--source2viewer", str(inputs.converter),
                     "--asset-cache", str(inputs.root / "cache"), "--map-name", "dl_midtown", "--material-repairs")
    report = cli.convert(args)
    assert len(seen) == 1
    assert seen[0]["strict"] is True
    assert seen[0]["demo"] is None
    assert seen[0]["map_name"] == "dl_midtown"
    assert seen[0]["need_animation"] is False  # Explicit --motion-only in this fixture.
    assert seen[0]["material_repairs"] is True
    assert report["entities"] == 12
    assert report["coverage"]["installed_assets"]["status"] == "test_adapter_marker"
    assert report["map"]["status"] == "not_requested"


def test_adapter_failure_keeps_segment_and_stage(inputs):
    def fail(**kwargs):
        (kwargs["work_dir"] / "asset_diagnostic.json").write_text('{"missing": ["hero/model"]}')
        raise ValueError("Unresolved hero resource")
    inputs.adapter.prepare_installed_assets = fail
    args = arguments(inputs, "--game-install", str(inputs.install), "--source2viewer", str(inputs.converter))
    with pytest.raises(ValueError, match="Unresolved hero resource"):
        cli.convert(args)
    failure = next(inputs.root.glob("agr-*/failure.json"))
    assert json.loads(failure.read_text())["stage"] == "prepare_installed_assets"
    assert (failure.parent / "extracted" / "segment.json").is_file()
    assert (failure.parent / "asset_diagnostic.json").is_file()
    assert not args.output.exists()


def test_animation_mismatch_failure_preserves_structured_details(inputs):
    from agr.animation import AnimationError, _bone_name_aliases

    def fail(**kwargs):
        _bone_name_aliases(['root', 'arm'], ['root', 'tip'], component='bones',
                           context={'skeleton':'models/test.vnmskel', 'graph':'graphs/test.vnmgraph',
                                    'demo_tick':10, 'server_tick':100})
    inputs.adapter.prepare_installed_assets = fail
    args = arguments(inputs, '--game-install', str(inputs.install), '--source2viewer', str(inputs.converter))
    with pytest.raises(AnimationError) as caught:
        cli.convert(args)
    failure = json.loads(next(inputs.root.glob('agr-*/failure.json')).read_text())
    assert failure['stage'] == 'prepare_installed_assets'
    assert failure['type'] == 'AnimationError'
    assert failure['error'] == str(caught.value)
    assert failure['details'] == caught.value.details
    assert failure['details']['recorded_name'] == 'arm'
    assert failure['details']['asset_name'] == 'tip'
    assert failure['details']['demo_tick'] == 10
    assert not args.output.exists()


def test_partial_is_forwarded_without_pruning_the_roster(inputs):
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return inputs.assets
    inputs.adapter.prepare_installed_assets = prepare
    report = cli.convert(arguments(inputs, "--source2viewer", str(inputs.converter), "--partial"))
    assert calls[0]["strict"] is False
    assert report["entities"] == 12


def test_fresh_installed_conversion_requests_animation_resources(inputs, monkeypatch):
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return inputs.assets
    inputs.adapter.prepare_installed_assets = prepare
    monkeypatch.setattr("agr.animation.evaluate_segment", lambda segment, *args, **kwargs: segment)
    args = arguments(inputs, "--source2viewer", str(inputs.converter))
    args.motion_only = False
    cli.convert(args)
    assert calls[0]["need_animation"] is True


def test_baked_segment_reuses_pose_files_without_raw_animation_inputs(inputs, monkeypatch):
    """A plain saved manifest with evaluated tracks requires only new geometry."""
    import numpy as np
    skeleton = inputs.root / "rig.json"
    skeleton.write_text(json.dumps({"m_boneIDs": ["root"], "m_parentIndices": [-1]}))
    poses = inputs.root / "poses.npz"
    np.savez(poses, q=np.array([[[0., 0., 0., 1.]]]), t=np.zeros((1, 1, 3)),
             s=np.ones((1, 1)), ticks=np.array([10]))
    data = read_scene(inputs.segment)
    for entity in data["entities"]:
        entity.update(pose_file=str(poses), skeleton_file=str(skeleton))
    save_scene(data, inputs.segment)
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        assert not (kwargs["segment_path"].parent / "raw_animation_state.json").exists()
        return inputs.assets
    inputs.adapter.prepare_installed_assets = prepare
    def unexpected(*args, **kwargs):
        pytest.fail("Cached conversion must not evaluate raw animation again")
    monkeypatch.setattr("agr.animation.evaluate_segment", unexpected)
    args = arguments(inputs, "--source2viewer", str(inputs.converter))
    args.motion_only = False
    report = cli.convert(args)
    assert calls[0]["need_animation"] is False
    assert report["entities"] == 12


@pytest.mark.parametrize('fault', ['missing', 'unreadable', 'other_job', 'missing_character'])
def test_invalid_blender_completion_cannot_replace_existing_scene_or_publish_companions(inputs, monkeypatch, fault):
    args = arguments(inputs, '--assets', str(inputs.assets), '--overwrite')
    args.output.write_bytes(b'previous user scene')
    def blender(command, check):
        report = write_blender_completion(command)
        path = Path(command[command.index('--output') + 1]).with_suffix('.scene.json')
        if fault == 'missing':
            path.unlink()
        elif fault == 'unreadable':
            path.write_text('{interrupted report')
        else:
            if fault == 'other_job':
                report['output'] = str(inputs.root / 'other-scene.blend')
            else:
                report['entities'].pop()
            path.write_text(json.dumps(report))
    monkeypatch.setattr(cli.subprocess, 'run', blender)
    with pytest.raises(RuntimeError, match='Blender completion report'):
        cli.convert(args)
    assert args.output.read_bytes() == b'previous user scene'
    assert not args.output.with_suffix('.agrscene').exists()
    assert not args.output.with_suffix('.agr-report.json').exists()
    failure = json.loads(next(inputs.root.glob('agr-*/failure.json')).read_text())
    assert failure['stage'] == 'validate_blender_completion'


@pytest.mark.parametrize('stage',['hero_lighting','npr_lighting','effects','cloth','camera'])
@pytest.mark.parametrize('partial',[False,True])
def test_dropped_requested_stage_cannot_publish_even_partial(inputs,monkeypatch,stage,partial):
    data=read_scene(inputs.segment)
    if stage=='hero_lighting':data['hero_lighting']={'status':'prepared'}
    elif stage=='npr_lighting':
        data['npr_runtime']={'mode':'partial_indirect_reference'}
        data['npr_materials']={'entities':[{'entity_id':e['id'],'model_sha256':'model'} for e in data['entities']],
                               'models':{'model':{'materials':[{'material':'body.vmat_c'}]}}}
    elif stage=='effects':data['effects']={'manifest_file':'effects.json'}
    elif stage=='cloth':
        definition=inputs.root/'cloth.json';definition.write_text('{}')
        data['entities'][0]['secondary_motion_file']=str(definition)
    save_scene(data,inputs.segment)
    extra=['--assets',str(inputs.assets),'--overwrite']
    if partial:extra.append('--partial')
    if stage=='camera':extra+=['--shot-hero','15']
    args=arguments(inputs,*extra);args.output.write_bytes(b'previous final scene')
    with pytest.raises(RuntimeError,match='completion|requested'):
        cli.convert(args)
    assert args.output.read_bytes()==b'previous final scene'
    assert not args.output.with_suffix('.agrscene').exists()
    assert not args.output.with_suffix('.agr-report.json').exists()
    failure=json.loads(next(inputs.root.glob('agr-*/failure.json')).read_text())
    assert failure['stage']=='validate_blender_completion'


@pytest.mark.parametrize('partial',[False,True])
def test_failed_requested_effect_stage_requires_explicit_broad_partial(inputs,monkeypatch,partial):
    data=read_scene(inputs.segment);data['effects']={'manifest_file':'effects.json'};save_scene(data,inputs.segment)
    def blender(command,check):write_blender_completion(command,effects={'status':'failed','error':'Decoder process exited1'})
    monkeypatch.setattr(cli.subprocess,'run',blender)
    args=arguments(inputs,'--assets',str(inputs.assets),*(['--partial'] if partial else ['--partial-lighting']))
    if not partial:
        with pytest.raises(RuntimeError,match='did not complete requested effects'):cli.convert(args)
        assert not args.output.exists()
    else:
        report=cli.convert(args)
        assert report['status']=='converted_with_warnings'
        assert any('Requested effects failed' in warning for warning in report['warnings'])


def test_blender_capability_failure_precedes_conversion_work_and_assets(inputs, monkeypatch):
    from agr.blender_runtime import BlenderPreflightError
    calls = []
    def reject(engine, **options):
        calls.append((engine, options))
        raise BlenderPreflightError('Selected Blender lacks the required animation API')
    monkeypatch.setattr('agr.blender_runtime.check_blender', reject)
    inputs.adapter.prepare_installed_assets = lambda **kwargs: pytest.fail('Assets must not be converted')
    args = arguments(inputs, '--game-install', str(inputs.install), '--source2viewer', str(inputs.converter))
    with pytest.raises(BlenderPreflightError, match='required animation API'):
        cli.convert(args)
    assert calls == [(str(inputs.engine), dict(python_mode=False, character_shader=False))]
    assert not list(inputs.root.glob('agr-*'))
    assert not args.output.exists()


def test_artistic_lighting_acceptance_does_not_allow_incompatible_character_animation(inputs,monkeypatch):
    from agr.animation import AnimationError
    calls=[]
    def prepare(**kwargs):
        calls.append(('assets',kwargs['strict'],kwargs['allow_partial_lighting']))
        return inputs.assets
    inputs.adapter.prepare_installed_assets=prepare
    def animation(*args,**kwargs):
        calls.append(('animation',kwargs['strict']))
        raise AnimationError('Recorded bone indices differ from the installed skeleton')
    monkeypatch.setattr('agr.animation.evaluate_segment',animation)
    args=arguments(inputs,'--source2viewer',str(inputs.converter),'--partial-lighting')
    args.motion_only=False
    with pytest.raises(AnimationError,match='bone indices differ'):
        cli.convert(args)
    assert calls==[('assets',True,True),('animation',True)]
    assert not args.output.exists()


@pytest.mark.parametrize('partial', [False, True])
def test_missing_pose_result_never_retains_cached_evaluated_coverage(inputs, monkeypatch, partial):
    import numpy as np
    np.savez(inputs.root / 'cached.npz', ticks=np.array([1]),
             q=np.array([[[0., 0., 0., 1.]]]), t=np.zeros((1, 1, 3)), s=np.ones((1, 1)))
    (inputs.root / 'rig.json').write_text(json.dumps(dict(names=['root'], parents=[-1])))
    data = read_scene(inputs.segment)
    for entity in data['entities']:
        entity.update(pose_file=str(inputs.root / 'cached.npz'), skeleton_file=str(inputs.root / 'rig.json'))
    data['coverage'] = {'skeletal_animation': {'evaluated_entities': 12, 'unsupported_entities': 0}}
    save_scene(data, inputs.segment)
    def blender(command, check):
        result = write_blender_completion(command)
        for row in result['entities']:
            row['pose'] = dict(status='unsupported', sample_count=0,
                               reason='No recorded bone poses cover the selected range.')
        Path(command[command.index('--output') + 1]).with_suffix('.scene.json').write_text(json.dumps(result))
    monkeypatch.setattr(cli.subprocess, 'run', blender)
    args = arguments(inputs, '--assets', str(inputs.assets), *(['--partial'] if partial else []))
    args.motion_only = False
    if not partial:
        with pytest.raises(RuntimeError, match='did not complete skeletal animation'):
            cli.convert(args)
        assert not args.output.exists()
    else:
        report = cli.convert(args)
        coverage = report['coverage']['skeletal_animation']
        assert coverage['evaluated_entities'] == 0 and coverage['unsupported_entities'] == 12
        assert not coverage['all_requested_entities']
        assert report['status'] == 'converted_with_warnings'
        assert any('skeletal animation unavailable; imported rest pose' in warning for warning in report['warnings'])
        saved = read_scene(args.output.with_suffix('.agrscene'))
        assert saved['coverage']['skeletal_animation'] == coverage


def test_unique_discovered_install_is_selected(inputs):
    result = cli.resolve_asset_source(arguments(inputs, "--source2viewer", str(inputs.converter)))
    assert result["install_path"] == inputs.install


@pytest.mark.parametrize("count", [0, 2])
def test_absent_or_ambiguous_install_needs_explicit_folder(inputs, count):
    inputs.provider.discover_installations = lambda: [inputs.install] * count
    with pytest.raises(ValueError, match="--game-install"):
        cli.resolve_asset_source(arguments(inputs, "--source2viewer", str(inputs.converter)))


def test_invalid_converter_fails_before_extraction_work_is_created(inputs):
    args = arguments(inputs, "--game-install", str(inputs.install), "--source2viewer", str(inputs.root / "missing.exe"))
    with pytest.raises(ValueError, match="command-line exporter"):
        cli.convert(args)
    assert not list(inputs.root.glob("agr-*"))


def test_desktop_viewer_is_rejected(inputs):
    gui = inputs.root / "Source2Viewer.exe"
    gui.write_bytes(b"test")
    with pytest.raises(ValueError, match="desktop viewer"):
        cli.resolve_asset_source(arguments(inputs, "--source2viewer", str(gui)))


def test_cache_cannot_write_under_install(inputs):
    with pytest.raises(ValueError, match="outside"):
        cli.resolve_asset_source(arguments(inputs, "--source2viewer", str(inputs.converter),
                                           "--asset-cache", str(inputs.install / "cache")))


def test_installed_options_not_silently_ignored_with_prepared_pack(inputs):
    with pytest.raises(ValueError, match="only to the installed"):
        cli.resolve_asset_source(arguments(inputs, "--assets", str(inputs.assets), "--map-name", "dl_midtown"))


def test_selected_python_environment_symlink_is_preserved(tmp_path):
    base = tmp_path / "base-python"
    base.write_bytes(b"test interpreter")
    selected = tmp_path / "venv" / "bin" / "python"
    selected.parent.mkdir(parents=True)
    try:
        selected.symlink_to(base)
    except OSError as exc:
        pytest.skip(f"Test account cannot create symlinks: {exc}")
    assert cli.executable(str(selected)) == str(selected.absolute())
    assert Path(cli.executable(str(selected))) != base.resolve()


def gui_values(inputs, **changes):
    return {"mode": "segment", "input": str(inputs.segment), "assets": str(inputs.assets),
            "game_install": str(inputs.install), "source2viewer": str(inputs.converter),
            "asset_cache": str(inputs.root / "cache"), "blender": str(inputs.engine),
            "output": str(inputs.root / "gui.blend"), "start": "10", "end": "10",
            "fps": "64", "heroes": "", **changes}


def test_new_gui_defaults_to_installed_and_legacy_settings_to_prepared():
    from agr.gui import asset_source_mode
    assert asset_source_mode({}) == "installed"
    assert asset_source_mode({"assets": "D:/existing assets"}) == "prepared"
    assert asset_source_mode({"asset_source": "installed", "assets": "D:/existing assets"}) == "installed"


def test_real_gui_settings_migration_and_new_fields_persist(tmp_path, monkeypatch):
    from agr import gui
    import tkinter as tk
    interpreter = tk.Tcl()  # Settings use Tcl variables and require no graphics/display server.
    defaults = {"mode": "replay", "assets": "", "asset_source": "installed",
                "game_install": "", "source2viewer": "", "asset_cache": ""}
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(master=interpreter, value=value) for key, value in defaults.items()}
    app._append_log = lambda message: pytest.fail(message)
    settings = tmp_path / "config" / "settings.json"
    monkeypatch.setattr(gui, "settings_path", lambda: settings)
    app._load_settings()
    assert app.values["asset_source"].get() == "installed"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"assets": "D:/old prepared folder", "mode": "replay"}))
    app._load_settings()
    assert app.values["asset_source"].get() == "prepared"
    expected = {"asset_source": "installed", "game_install": "E:/Steam/Deadlock",
                "source2viewer": "D:/tools/Source2Viewer-CLI.exe", "asset_cache": "D:/AGR Cache"}
    for key, value in expected.items():
        app.values[key].set(value)
    app._save_settings()
    saved = json.loads(settings.read_text())
    assert all(saved[key] == value for key, value in expected.items())
    for key in expected:
        app.values[key].set("")
    app._load_settings()
    assert all(app.values[key].get() == value for key, value in expected.items())


def test_gui_installed_argv_does_not_add_legacy_asset_folder(inputs):
    from agr.gui import build_convert_command
    command = build_convert_command(gui_values(inputs, asset_source="installed"))
    assert "--game-install" in command and "--assets" not in command
    assert command[command.index("--game-install") + 1] == str(inputs.install)
    assert "--heroes" not in command
    # Values containing spaces remain single argv items; no shell string concatenation.
    assert "Steam Library" in command[command.index("--game-install") + 1]


def test_gui_legacy_prepared_argv_preserved(inputs):
    from agr.gui import build_convert_command
    command = build_convert_command(gui_values(inputs))
    assert "--assets" in command and "--game-install" not in command
    assert "--source2viewer" not in command


def test_gui_invalid_converter_names_clear_fix(inputs):
    from agr.gui import build_convert_command
    with pytest.raises(ValueError, match="command-line exporter"):
        build_convert_command(gui_values(inputs, asset_source="installed", source2viewer=""))
