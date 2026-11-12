"""Headless command/preferences checks; no Tk widgets or native dialogs opened."""
import json
from pathlib import Path
import tkinter as tk

import pytest

from agr import cli, gui
from agr.gui_paths import normalize_gui_paths
from test_gui_character_workflow import command_values


@pytest.fixture
def values(tmp_path):
    path = tmp_path / 'captured-slides.json';path.write_text('{}')
    probe = tmp_path / 'simulator.exe';probe.touch()
    return dict(command_values(tmp_path), start='17920', end='18240', fps='60',
                effects_enabled='1', simulate_effects='1', particle_probe=str(probe),
                particle_history_ticks='62', model_constraints='1', cloth_enabled='0',
                captured_slide_inputs=str(path))


def test_gui_uses_normal_cli_and_preserves_both_optional_input_files(values, tmp_path):
    observations = tmp_path / 'observations.json';observations.write_text('{}')
    selected = dict(values, particle_observations=str(observations))
    before = dict(selected)
    args = cli.parser().parse_args(gui.build_convert_command(selected, partial=True)[3:])
    assert args.captured_slide_inputs == Path(values['captured_slide_inputs'])
    assert args.particle_observations == observations
    assert args.start_tick == 17920 and args.end_tick == 18240 and args.particle_history_ticks == 62
    assert args.partial and args.model_constraints and args.particle_probe and not args.no_effects
    assert selected == before


@pytest.mark.parametrize('key,value', [('effects_enabled','0'), ('simulate_effects','0'),
    ('model_constraints','0'), ('asset_source','prepared'), ('start','17921'),
    ('end','18239'), ('particle_history_ticks','0'), ('particle_history_ticks','bad')])
def test_invalid_scope_or_disabled_dependencies_are_explained(values, key, value, tmp_path):
    changed = dict(values, **{key: value})
    if key == 'asset_source':
        changed['assets'] = str(tmp_path)
    with pytest.raises(ValueError, match='Captured slide inputs|history ticks'):
        gui.build_convert_command(changed, partial=True)


def test_selecting_capture_does_not_supply_partial_consent(values):
    with pytest.raises(ValueError, match='explicitly select --partial'):
        gui.build_convert_command(values)
    values['captured_slide_inputs'] = ''
    command = gui.build_convert_command(values)
    assert '--captured-slide-inputs' not in command and '--partial' not in command


def test_missing_file_is_explained_without_starting_work(values, tmp_path):
    values['captured_slide_inputs'] = str(tmp_path / 'missing.json')
    with pytest.raises(ValueError, match='existing captured slide file in Advanced'):
        gui.build_convert_command(values, partial=True)


def test_capture_path_roundtrip_survives_working_directory_change_without_remembering_consent(
        values, tmp_path, monkeypatch):
    interpreter = tk.Tcl()  # Tcl variables only; no Tk package/display/widget.
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(master=interpreter, value=value) for key,value in values.items()}
    app.partial = tk.BooleanVar(master=interpreter, value=False)
    app._append_log = lambda text: pytest.fail(text)
    settings = tmp_path / 'preferences' / 'settings.json'
    monkeypatch.setattr(gui, 'settings_path', lambda: settings)
    selected = Path(values['captured_slide_inputs'])
    app.values['captured_slide_inputs'].set(selected.name)
    monkeypatch.chdir(tmp_path)
    app._save_settings()
    assert json.loads(settings.read_text())['captured_slide_inputs'] == str(selected)
    worker = tmp_path / 'another-directory';worker.mkdir();monkeypatch.chdir(worker)
    app.values['captured_slide_inputs'].set('')
    app._load_settings()
    assert app.values['captured_slide_inputs'].get() == str(selected)
    assert not app.partial.get()
    with pytest.raises(ValueError, match='explicitly select --partial'):
        gui.build_convert_command(app._current_values())
    assert normalize_gui_paths({'captured_slide_inputs':'next.json'},base=worker)[
        'captured_slide_inputs'] == str(worker/'next.json')


def test_browse_and_cancel_only_change_the_selected_field(values, monkeypatch):
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.root = object()
    app.values = {k:tk.StringVar(master=interpreter,value=v) for k,v in values.items()}
    before = app._current_values()
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: '')
    app._browse_captured_slide_inputs()
    assert app._current_values() == before
    monkeypatch.setattr(gui.filedialog, 'askopenfilename', lambda **kwargs: 'next-capture.json')
    app._browse_captured_slide_inputs()
    assert app._current_values() == dict(before,captured_slide_inputs='next-capture.json')


def test_explicit_without_effects_retry_clears_supplied_inputs_in_real_controller(values, tmp_path):
    from agr.export_recovery import recovery_choices
    observations = tmp_path/'observations.json';observations.write_text('{}')
    selected = dict(values, particle_observations=str(observations))
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {k:tk.StringVar(master=interpreter,value=v)for k,v in selected.items()}
    app.partial = tk.BooleanVar(master=interpreter,value=True)
    app.partial_cloth = tk.BooleanVar(master=interpreter,value=False)
    app.overwrite = tk.BooleanVar(master=interpreter,value=False)
    app.status = tk.StringVar(master=interpreter)
    app.busy = False
    app._conversion_command = gui.build_convert_command(selected,partial=True)
    app._conversion_identity = app._input_identity()
    app._append_log = lambda _: None
    app._save_settings = lambda: None
    starts=[];app._start=lambda *a:starts.append(a)
    choice = recovery_choices(app._conversion_command,dict(stage='build_blender_scene',
                              error='Particle simulation has unresolved diagnostics'))[0]
    app._retry_conversion(choice)
    assert len(starts)==1
    parsed = cli.parser().parse_args(starts[0][0][3:])
    assert parsed.no_effects and parsed.captured_slide_inputs is None and parsed.particle_observations is None
    assert app.values['captured_slide_inputs'].get()==app.values['particle_observations'].get()==''
    assert app.values['start'].get()=='17920'and app.values['end'].get()=='18240'
    assert app.partial.get()  # No new consent was invented.
