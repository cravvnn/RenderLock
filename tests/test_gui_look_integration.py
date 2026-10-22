"""The real command builder resolves UI look choices without broad loss consent."""
from pathlib import Path
import tkinter as tk

import pytest

from agr import cli, gui, release_profile
from agr.export_recovery import recovery_choices


@pytest.fixture(autouse=True)
def helper_identities(monkeypatch):
    # These tests exercise argv and policy integration. Decoder byte identities
    # are covered by their own qualification tests; no native helper is run here.
    from agr import source_hero_lighting
    monkeypatch.setattr(source_hero_lighting, 'qualified_probe_helpers', lambda *_: None)


def values_for(tmp_path):
    for name in ('replay.dem', 'blender.exe', 'Source2Viewer-CLI.exe', 'ProbeAtlasDecode.dll', 'CubemapDecode.dll', 'dotnet.exe'):
        (tmp_path / name).touch()
    return dict(input=str(tmp_path / 'replay.dem'), mode='replay', asset_source='installed',
                game_install=str(tmp_path), source2viewer=str(tmp_path / 'Source2Viewer-CLI.exe'),
                blender=str(tmp_path / 'blender.exe'), output=str(tmp_path / 'scene.blend'),
                start='10', end='12', fps='24', character_only='1', character_look='source',
                source_lighting='0', source_hero_lighting='0', source_npr_lighting='0',
                map_enabled='0', map_name='dl_midtown', map_margin='26',
                probe_atlas_decoder=str(tmp_path / 'ProbeAtlasDecode.dll'),
                cubemap_decoder=str(tmp_path / 'CubemapDecode.dll'), lighting_dotnet=str(tmp_path / 'dotnet.exe'),
                character_shader=str(tmp_path / 'remembered-library.blend'),
                cloth_enabled='0', effects_enabled='1')


def test_standard_keeps_stored_library_but_omits_its_effective_arguments(tmp_path, monkeypatch):
    values = values_for(tmp_path)
    values.update(character_look='standard', map_enabled='1', source_lighting='1',
                  source_hero_lighting='1', source_npr_lighting='1')
    original = dict(values)
    monkeypatch.setattr(release_profile, 'character_beta', lambda: True)
    parsed = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert not any((parsed.source_lighting, parsed.source_hero_lighting,
                    parsed.source_npr_lighting, parsed.include_map, parsed.partial))
    assert parsed.character_shader is None and values == original


def test_source_preset_derives_map_and_probe_requirements_in_one_command(tmp_path):
    values = values_for(tmp_path)
    parsed = cli.parser().parse_args(gui.build_convert_command(values, lighting_consent=True)[3:])
    assert parsed.source_lighting and parsed.source_hero_lighting and parsed.include_map
    assert parsed.partial_lighting and not parsed.partial and not parsed.partial_cloth
    assert not parsed.source_npr_lighting and parsed.character_shader is None
    assert parsed.shot_hero is None and parsed.heroes is None
    assert (parsed.start_tick, parsed.end_tick, parsed.fps) == (10, 12, 24)
    assert values['map_enabled'] == '0' and values['character_shader'].endswith('remembered-library.blend')


@pytest.mark.parametrize('partial', [False, True])
def test_broad_animation_loss_does_not_grant_lighting_consent(tmp_path, partial):
    with pytest.raises(ValueError, match='partial lighting limits'):
        gui.build_convert_command(values_for(tmp_path), partial=partial)


def test_elagrimm_preset_resolves_flags_but_keeps_explicit_camera_and_library(tmp_path, monkeypatch):
    from agr import blender_character_shader
    values = values_for(tmp_path)
    values.update(character_look='elagrimm_source', shot_hero='84')
    monkeypatch.setattr(blender_character_shader, 'validate_character_shader_library', lambda path: Path(path))
    parsed = cli.parser().parse_args(gui.build_convert_command(values, lighting_consent=True)[3:])
    assert all((parsed.source_lighting, parsed.source_hero_lighting, parsed.source_npr_lighting,
                parsed.include_map, parsed.partial_lighting))
    assert parsed.character_shader == Path(values['character_shader']) and parsed.shot_hero == 84
    assert parsed.heroes is None and not parsed.partial
    values['shot_hero'] = ''
    with pytest.raises(ValueError, match='Choose a hero'):
        gui.build_convert_command(values, lighting_consent=True)


def test_prepared_source_choice_explains_action_without_disabled_checkbox_chain(tmp_path):
    values = values_for(tmp_path)
    values.update(asset_source='prepared', assets=str(tmp_path))
    with pytest.raises(ValueError, match='Standard materials'):
        gui.build_convert_command(values, lighting_consent=True)


def controller_for(tmp_path):
    interpreter = tk.Tcl()
    values = values_for(tmp_path)
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(interpreter, value=value) for key, value in values.items()}
    app.partial = tk.BooleanVar(interpreter, value=False)
    app.partial_cloth = tk.BooleanVar(interpreter, value=False)
    app.lighting_consent = tk.BooleanVar(interpreter, value=True)
    app.overwrite = tk.BooleanVar(interpreter, value=False)
    app.status = tk.StringVar(interpreter)
    app.busy = False
    app._conversion_command = gui.build_convert_command(values, lighting_consent=True)
    app._conversion_identity = app._input_identity()
    log, starts = [], []
    app._append_log = log.append
    app._save_settings = lambda: None
    app._start = lambda *args: starts.append(args)
    return app, starts, log, values


def test_optional_retry_does_not_reintroduce_or_drop_preset_dependencies(tmp_path):
    app, starts, log, values = controller_for(tmp_path)
    choices = recovery_choices(app._conversion_command,
        dict(stage='build_blender_scene', error='Particle simulation has unresolved diagnostics'))
    choice = next(choice for choice in choices if choice.id == 'without_effects')
    app._retry_conversion(choice)
    assert len(starts) == 1, log
    parsed = cli.parser().parse_args(starts[0][0][3:])
    assert parsed.no_effects and parsed.source_lighting and parsed.source_hero_lighting
    assert parsed.partial_lighting and not parsed.partial and app.lighting_consent.get()
    assert app.values['character_look'].get() == 'source'
    assert app.values['character_shader'].get() == values['character_shader']


def test_no_source_retry_preserves_selected_map_arguments_for_character_only_job(tmp_path):
    app, starts, log, _values = controller_for(tmp_path)
    choices = recovery_choices(app._conversion_command, dict(stage='prepare_installed_assets',
        error='Installed game compatibility check failed for hero lighting before asset preparation: probe atlas changed'))
    choice = next(choice for choice in choices if choice.id == 'without_source_lighting')
    app._retry_conversion(choice)
    assert len(starts) == 1, log
    parsed = cli.parser().parse_args(starts[0][0][3:])
    assert parsed.include_map and parsed.character_only
    assert not any((parsed.source_lighting, parsed.source_hero_lighting, parsed.source_npr_lighting, parsed.partial))
    assert app.values['character_look'].get() == 'custom'
