"""Saved preferences and actual argv for the reusable-map workflow."""
import json
import tkinter as tk
import pytest

from agr import cli, gui


def settings_app():
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    defaults = {"mode": "replay", "asset_source": "installed", "map_enabled": "0",
                "map_name": "dl_midtown", "map_margin": "26", "asset_cache": "",
                "game_install": "", "cloth_enabled": "1",
                "simulate_effects": "0", "particle_probe": "", "particle_dotnet": "",
                "map_workflow_revision": gui.MAP_WORKFLOW_REVISION}
    app.values = {key: tk.StringVar(master=interpreter, value=value) for key, value in defaults.items()}
    app._append_log = lambda message: (_ for _ in ()).throw(AssertionError(message))
    return app


def test_existing_map_default_migrates_once_without_discarding_user_paths(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    previous = {"mode": "segment", "asset_source": "installed", "map_enabled": "1",
                "map_name": "maps/old_patch/world.vwrld_c", "map_margin": "42",
                "asset_cache": "E:/existing cache", "game_install": "D:/Steam/Deadlock"}
    path.write_text(json.dumps(previous))
    monkeypatch.setattr(gui, "settings_path", lambda: path)
    app = settings_app()
    app._load_settings()
    assert app.values["map_enabled"].get() == "0"
    for key in previous.keys() - {"map_enabled"}:
        assert app.values[key].get() == previous[key]
    app._save_settings()
    saved = json.loads(path.read_text())
    assert saved["map_workflow_revision"] == gui.MAP_WORKFLOW_REVISION
    assert saved["map_enabled"] == "0"
    # An explicit map opt-in and cloth opt-out must survive subsequent launches.
    app.values["map_enabled"].set("1")
    app.values["cloth_enabled"].set("0")
    app._save_settings()
    reopened = settings_app()
    reopened._load_settings()
    assert reopened.values["map_enabled"].get() == "1"
    assert reopened.values["cloth_enabled"].get() == "0"
    assert reopened.values["asset_cache"].get() == previous["asset_cache"]


def test_fresh_settings_keep_map_off_and_cloth_on(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "settings_path", lambda: tmp_path / "absent.json")
    app = settings_app()
    app._load_settings()
    assert app.values["map_enabled"].get() == "0"
    assert app.values["cloth_enabled"].get() == "1"


def test_reusable_map_command_skips_map_even_when_saved_map_fields_are_invalid(tmp_path):
    for name in ("input.dem", "blender.exe", "Source2Viewer-CLI.exe"):
        (tmp_path / name).write_bytes(b"fixture")
    values = {"input": str(tmp_path / "input.dem"), "mode": "replay", "asset_source": "installed",
              "game_install": str(tmp_path), "source2viewer": str(tmp_path / "Source2Viewer-CLI.exe"),
              "blender": str(tmp_path / "blender.exe"), "output": str(tmp_path / "result.blend"),
              "start": "112960", "end": "113080", "fps": "64", "map_enabled": "0",
              "map_name": "", "map_margin": "unused", "effects_enabled": "1", "cloth_enabled": "1"}
    argv = gui.build_convert_command(values)
    parsed = cli.parser().parse_args(argv[3:])
    assert not parsed.include_map and parsed.secondary_motion and not parsed.no_effects
    assert "--map-name" not in argv and "--map-margin" not in argv
    disabled = cli.parser().parse_args(gui.build_convert_command(dict(values, cloth_enabled="0"))[3:])
    assert not disabled.secondary_motion
    constrained = cli.parser().parse_args(gui.build_convert_command(dict(values, model_constraints='1'))[3:])
    assert constrained.model_constraints
    assert not parsed.model_constraints
    assert not parsed.material_repairs
    repaired = cli.parser().parse_args(gui.build_convert_command(dict(values, material_repairs="1"))[3:])
    assert repaired.material_repairs


def test_prepared_assets_do_not_receive_installed_cloth_or_map_flags(tmp_path):
    for name in ("manifest.json", "blender.exe"):
        (tmp_path / name).write_bytes(b"fixture")
    values = {"input": str(tmp_path / "manifest.json"), "mode": "segment", "asset_source": "prepared",
              "assets": str(tmp_path), "blender": str(tmp_path / "blender.exe"),
              "output": str(tmp_path / "result.blend"), "start": "1", "end": "2", "fps": "64",
              "map_enabled": "1", "cloth_enabled": "1"}
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert not parsed.include_map and not parsed.secondary_motion


def particle_values(tmp_path):
    for name in ('input.json', 'blender.exe', 'Source2Viewer-CLI.exe', 'ParticleProbe.dll', 'dotnet.exe'):
        (tmp_path / name).write_bytes(b'fixture')
    return {'input': str(tmp_path / 'input.json'), 'mode': 'segment', 'asset_source': 'installed',
            'game_install': str(tmp_path), 'source2viewer': str(tmp_path / 'Source2Viewer-CLI.exe'),
            'blender': str(tmp_path / 'blender.exe'), 'output': str(tmp_path / 'result.blend'),
            'start': '15300', 'end': '15420', 'fps': '64', 'effects_enabled': '1',
            'simulate_effects': '1', 'particle_probe': str(tmp_path / 'ParticleProbe.dll'),
            'particle_dotnet': str(tmp_path / 'dotnet.exe')}


def test_gui_simulation_selects_actual_cli_backend_and_preserves_other_stages(tmp_path):
    values = particle_values(tmp_path)
    values.update(map_enabled='1', map_name='dl_midtown', material_repairs='1', cloth_enabled='1')
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert parsed.particle_probe == tmp_path / 'ParticleProbe.dll'
    assert parsed.dotnet == values['particle_dotnet']
    assert not parsed.no_effects and parsed.include_map and parsed.material_repairs and parsed.secondary_motion


def test_gui_portable_simulation_needs_no_dotnet(tmp_path):
    values = particle_values(tmp_path)
    probe = tmp_path / 'ParticleProbe.exe'
    probe.touch()
    values.update(particle_probe=str(probe), particle_dotnet='stale missing runtime')
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert parsed.particle_probe == probe
    assert parsed.dotnet is None


@pytest.mark.parametrize('value', ['-1', '1.5', 'invalid'])
def test_gui_rejects_invalid_particle_history(tmp_path, value):
    values = particle_values(tmp_path)
    values['particle_history_ticks'] = value
    with pytest.raises(ValueError, match='history ticks'):
        gui.build_convert_command(values)


def test_gui_particle_history_reaches_cli_without_changing_output_range(tmp_path):
    values = particle_values(tmp_path)
    values['particle_history_ticks'] = '36'
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert (parsed.start_tick, parsed.end_tick, parsed.particle_history_ticks) == (15300, 15420, 36)


def test_follow_camera_selection_reaches_cli_without_filtering_roster(tmp_path):
    values = particle_values(tmp_path)
    values['shot_hero'] = '11'
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert parsed.shot_hero == 11
    assert parsed.heroes is None
    values['shot_hero'] = ''
    assert cli.parser().parse_args(gui.build_convert_command(values)[3:]).shot_hero is None


@pytest.mark.parametrize('value', ['0', '-1', '11,12', '1.5'])
def test_follow_camera_rejects_invalid_hero_selection(tmp_path, value):
    values = particle_values(tmp_path)
    values['shot_hero'] = value
    with pytest.raises(ValueError, match='camera hero'):
        gui.build_convert_command(values)


@pytest.mark.parametrize('change', [{'simulate_effects': '0'}, {'effects_enabled': '0'},
                                  {'asset_source': 'prepared'}])
def test_disabled_simulation_ignores_stale_tools(tmp_path, change):
    values = particle_values(tmp_path)
    values.update(particle_probe='missing', particle_dotnet='missing', assets=str(tmp_path), **change)
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert parsed.particle_probe is None and parsed.dotnet is None


@pytest.mark.parametrize('key,match', [('particle_probe', 'ParticleProbe'), ('particle_dotnet', '.NET')])
def test_enabled_simulation_requires_existing_tools(tmp_path, key, match):
    values = particle_values(tmp_path)
    values[key] = str(tmp_path / 'missing')
    with pytest.raises(ValueError, match=match):
        gui.build_convert_command(values)


def test_simulator_paths_and_explicit_choice_survive_settings(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    monkeypatch.setattr(gui, 'settings_path', lambda: path)
    app = settings_app()
    for key, value in {'simulate_effects': '1', 'particle_probe': 'D:/sim/ParticleProbe.dll',
                       'particle_dotnet': 'D:/runtime/dotnet.exe'}.items():
        app.values[key].set(value)
    app._save_settings()
    reopened = settings_app()
    reopened._load_settings()
    for key in ('simulate_effects', 'particle_probe', 'particle_dotnet'):
        assert reopened.values[key].get() == app.values[key].get()
