"""Exercise asset selection and command assembly without running game conversion."""
import json
from pathlib import Path
import sys
import types

import pytest

from agr import cli
from agr.scene_package import read_scene, save_scene


@pytest.fixture
def inputs(tmp_path, monkeypatch):
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
    provider = types.ModuleType("agr.game_assets")
    provider.discover_installations = lambda: [install]
    provider.normalize_install_path = lambda path: Path(path).resolve()
    adapter = types.ModuleType("agr.installed_assets")
    adapter.prepare_installed_assets = lambda **kwargs: assets
    monkeypatch.setitem(sys.modules, "agr.game_assets", provider)
    monkeypatch.setitem(sys.modules, "agr.installed_assets", adapter)
    def blender(command, check):
        Path(command[command.index("--output") + 1]).write_bytes(b"simulated test scene")
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
