import json
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

import pytest

from agr import gui, gui_theme, gui_widgets
from agr.export_recovery import RecoveryChoice, recovery_choices
from agr.gui_export_recovery import failure_details, show_failure
from agr.gui_widgets import RoundedButton


ERROR = 'Requested cloth preparation did not complete: Rat King: unsupported world collisions'


def test_failure_details_uses_exact_saved_paths_and_handles_missing_failure_file(tmp_path):
    work = tmp_path / 'retained export'
    work.mkdir()
    bundle = tmp_path / 'user logs' / 'support.zip'
    failure = dict(error=ERROR, stage='validate_cloth_preparation')
    output = ('Diagnostic files retained at: ' + str(work) + '\nSupport ZIP: ' + str(bundle) + '\n'
              'RENDERLOCK_DIAGNOSTICS ' + json.dumps(dict(status='saved', path=str(bundle),
                  work_directory=str(work), failure=failure)) + '\nAGR error: ' + ERROR)
    assert failure_details(output) == (failure, work, bundle)
    (work / 'failure.json').write_text('{incomplete')
    assert failure_details(output)[0] == failure
    (work / 'failure.json').write_text(json.dumps(dict(error='Stale evidence retained unchanged', stage='read_segment')))
    assert failure_details(output)[0] == failure
    assert failure_details('AGR error: early startup failure')[0]['error'] == 'early startup failure'


def controller(tmp_path):
    interpreter = tk.Tcl()
    for name in ('replay.dem', 'blender.exe', 'Source2Viewer-CLI.exe'):
        (tmp_path / name).touch()
    values = dict(input=str(tmp_path / 'replay.dem'), mode='replay', asset_source='installed',
                  game_install=str(tmp_path), source2viewer=str(tmp_path / 'Source2Viewer-CLI.exe'),
                  blender=str(tmp_path / 'blender.exe'), output=str(tmp_path / 'scene.blend'),
                  start='10', end='12', fps='64', cloth_enabled='1', effects_enabled='1')
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(interpreter, value=value) for key, value in values.items()}
    app.partial = tk.BooleanVar(interpreter, value=False)
    app.partial_cloth = tk.BooleanVar(interpreter, value=False)
    app.overwrite = tk.BooleanVar(interpreter, value=False)
    app.status = tk.StringVar(interpreter)
    app.busy = False
    app._conversion_command = gui.build_convert_command(values)
    app._conversion_identity = app._input_identity()
    app._append_log = lambda _text: None
    app._save_settings = lambda: None
    starts = []
    app._start = lambda *args: starts.append(args)
    return app, starts


def test_explicit_cloth_retry_preserves_full_animation_and_other_job_arguments(tmp_path):
    app, starts = controller(tmp_path)
    choices = recovery_choices(app._conversion_command, dict(error=ERROR, stage='validate_cloth_preparation'))
    choice = next(choice for choice in choices if choice.id == 'without_cloth')
    app._retry_conversion(choice)
    assert len(starts) == 1 and starts[0][0] == list(choice.command)
    assert app.values['cloth_enabled'].get() == '0'
    assert not app.partial.get() and not app.partial_cloth.get()
    assert '--no-effects' not in starts[0][0]


def test_supported_cloth_retry_with_follow_camera_and_overwrite_is_a_real_retry(tmp_path):
    app, starts = controller(tmp_path)
    interpreter = app.values['input']._root
    app.values['shot_hero'] = tk.StringVar(master=interpreter, value='84')
    app.values['heroes'] = tk.StringVar(master=interpreter, value='84')
    app.overwrite.set(True)
    app._conversion_command = gui.build_convert_command(app._current_values(), overwrite=True)
    choices = recovery_choices(app._conversion_command, dict(error=ERROR, stage='validate_cloth_preparation'))
    choice = next(choice for choice in choices if choice.id == 'supported_cloth')
    app._retry_conversion(choice)
    assert len(starts) == 1 and starts[0][0] == list(choice.command)
    assert app.values['cloth_enabled'].get() == '1'
    assert app.partial_cloth.get() and not app.partial.get()
    assert '--overwrite' in starts[0][0] and '--shot-hero' in starts[0][0]

    # A later optional failure must still offer another explicit choice. The
    # controller stores the helper's argv order while the builder has its own.
    app._conversion_command = starts[0][0]
    starts.clear()
    choices = recovery_choices(app._conversion_command, dict(stage='build_blender_scene',
                               error='Particle simulation has unresolved diagnostics'))
    choice = next(choice for choice in choices if choice.id == 'without_effects')
    app._retry_conversion(choice)
    assert len(starts) == 1 and starts[0][0] == list(choice.command)
    assert '--partial-cloth' in starts[0][0] and '--partial' not in starts[0][0]
    assert '--no-effects' in starts[0][0] and app.values['effects_enabled'].get() == '0'


@pytest.mark.parametrize('changed', ['input', 'settings'])
def test_retry_rejects_a_changed_job_before_applying_compromises(tmp_path, changed):
    app, starts = controller(tmp_path)
    choice = RecoveryChoice('without_cloth', 'Retry without cloth', 'Cloth is omitted',
                            tuple(arg for arg in app._conversion_command if arg != '--secondary-motion'),
                            (('cloth_enabled', '0'),))
    if changed == 'input':
        Path(app.values['input'].get()).write_bytes(b'changed replay')
    else:
        app.values['end'].set('13')
    app._retry_conversion(choice)
    assert not starts and app.values['cloth_enabled'].get() == '1'
    assert 'changed' in app.status.get()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@pytest.mark.parametrize('select_retry', [False, True])
def test_branded_recovery_window_requires_a_choice_and_displays_diagnostic_path(tmp_path, select_retry):
    gui_widgets._CORNER_CACHE.clear()
    root = tk.Tk()
    root.geometry('700x700+30000+30000')
    gui_theme.initialize(root)
    root.update()
    bundle = tmp_path / 'renderlock-support.zip'
    bundle.touch()
    command = ['python', '-m', 'agr', 'convert', '--secondary-motion']
    observations = []

    def interact():
        window = next(widget for widget in root.winfo_children() if isinstance(widget, tk.Toplevel))
        buttons = [widget for widget in descendants(window) if isinstance(widget, RoundedButton)]
        entries = [widget for widget in descendants(window) if isinstance(widget, tk.Entry)]
        assert window.icon.width() > 0 and window.logo.width() > 0
        assert entries[0].get() == str(bundle)
        assert entries[0].cget('readonlybackground') == gui_theme.FIELD
        assert root.grab_current() == window
        for button in buttons:
            assert button.winfo_y() >= 0
            assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        observations.append(True)
        if select_retry:
            next(button for button in buttons if button.cget('text') == 'Continue without cloth')._invoke()
        else:
            next(button for button in buttons if button.cget('text') == 'Review settings')._invoke()

    root.after(250, interact)
    # Bound this real-Tk smoke check even if a widget callback fails.
    timer = root.after(3000, lambda: [widget.destroy() for widget in root.winfo_children()
                                   if isinstance(widget, tk.Toplevel)])
    try:
        result = show_failure(SimpleNamespace(root=root, _append_log=lambda _text: None), command,
                              'AGR error: ' + ERROR + '\nSupport ZIP: ' + str(bundle) + '\n'
                              'RENDERLOCK_DIAGNOSTICS ' + json.dumps(dict(status='saved', path=str(bundle),
                                  failure=dict(error=ERROR, stage='validate_cloth_preparation'))))
        assert observations
        assert (result.id if result else None) == ('without_cloth' if select_retry else None)
    finally:
        root.after_cancel(timer)
        root.destroy()
        gui_widgets._CORNER_CACHE.clear()
