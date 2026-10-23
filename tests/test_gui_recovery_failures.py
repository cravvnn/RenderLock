"""Recovery fault containment without mapping windows or starting conversions."""
import json
import queue
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import Mock

import pytest

from agr import gui, gui_export_recovery, gui_layout
from agr.export_recovery import recovery_choices, recovery_summary


ERROR = ('Requested cloth preparation did not complete: Hero 65: Imported-bone solver '
         'requires additional authored operations: m_WorldCollisionNodes, m_Effects; '
         'Hero 84: Imported-bone solver requires additional authored operations: m_WorldCollisionNodes')
FAILURE = dict(stage='validate_cloth_preparation', error=ERROR, type='RuntimeError')


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def controller(tmp_path, monkeypatch):
    for name in ('replay.dem', 'blender.exe', 'Source2Viewer-CLI.exe'):
        (tmp_path / name).touch()  # Existence-only inputs, never executed.
    values = dict(input=str(tmp_path / 'replay.dem'), mode='replay', asset_source='installed',
                  game_install=str(tmp_path), source2viewer=str(tmp_path / 'Source2Viewer-CLI.exe'),
                  blender=str(tmp_path / 'blender.exe'), output=str(tmp_path / 'scene.blend'),
                  start='29504', end='31906', fps='64', heroes='', shot_hero='84',
                  character_only='1', character_look='standard', cloth_enabled='1', effects_enabled='1')
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: Value(value) for key, value in values.items()}
    app.partial, app.partial_cloth, app.overwrite = Value(False), Value(False), Value(True)
    app.status = Value('')
    app.busy, app.close_when_done = True, False
    app.cancel_requested = SimpleNamespace(is_set=lambda: False)
    app.events = queue.Queue()
    app.root = SimpleNamespace(after=Mock())
    app._set_busy = lambda value: setattr(app, 'busy', value)
    app._status_tint = Mock()
    app._append_log = Mock()
    app._save_settings = Mock()
    app._start = Mock()
    app._conversion_command = gui.build_convert_command(values, overwrite=True)
    app._conversion_identity = app._input_identity()
    monkeypatch.setattr(gui_layout, 'set_log_visible', Mock())
    return app


def logged(app):
    return ''.join(call.args[0] for call in app._append_log.call_args_list)


@pytest.mark.parametrize('error_type', [tk.TclError, RuntimeError])
def test_modal_exception_logs_existing_guidance_before_opening_and_keeps_polling(
        controller, tmp_path, monkeypatch, error_type):
    app = controller
    work, bundle = tmp_path / 'retained work', tmp_path / 'support.zip'
    output = ('AGR error: ' + ERROR + '\nRENDERLOCK_DIAGNOSTICS ' + json.dumps(dict(
        failure=FAILURE, work_directory=str(work), status='saved', path=str(bundle))) + '\n')
    command = app._conversion_command.copy()
    choices = recovery_choices(command, FAILURE)
    original_settings = app._current_values()
    before_modal = []
    app.events.put(('log', output))
    app.events.put(('finished', ('convert', 1, output)))
    app.events.put(('log', 'Later queued output remains readable.\n'))

    def fail_modal(actual_app, actual_command, actual_output):
        assert actual_app is app and actual_command == command and actual_output == output
        before_modal.append(logged(app))
        assert not app.busy
        raise error_type('injected recovery construction failure')

    monkeypatch.setattr(gui_export_recovery, 'show_failure', Mock(side_effect=fail_modal))
    app._poll()

    text = logged(app)
    assert recovery_summary(command, FAILURE) in before_modal[0]
    for choice in choices:
        assert choice.title in before_modal[0] and choice.description in before_modal[0]
    assert str(work) in before_modal[0] and str(bundle) in before_modal[0]
    assert f'{error_type.__name__}: injected recovery construction failure' in text
    assert 'Review' in text and 'Export' in text
    assert text.count(ERROR) == 2  # Original fatal and its original JSON, neither replayed.
    assert 'Later queued output remains readable.' in text
    assert app.root.after.call_args_list == [((100, app._poll), {})]
    assert app.status.get().startswith('Convert failed (exit 1)')
    assert not app.busy and not app.partial.get() and not app.partial_cloth.get()
    assert app._current_values() == original_settings and app._conversion_command == command
    app._start.assert_not_called()
    app._save_settings.assert_not_called()
    # The next pass remains functional, too.
    app.events.put(('log', 'Next poll consumed this event.\n'))
    app.root.after.call_args.args[1]()
    assert 'Next poll consumed this event.' in logged(app)
    assert app.root.after.call_count == 2


def test_guidance_uses_failed_argv_even_when_current_cloth_checkbox_changed(controller, monkeypatch):
    app = controller
    app.values['cloth_enabled'].set('0')
    monkeypatch.setattr(gui_export_recovery, 'show_failure', Mock(return_value=None))
    app._show_conversion_failure('AGR error: ' + ERROR)
    for choice in recovery_choices(app._conversion_command, FAILURE):
        assert choice.title in logged(app) and choice.description in logged(app)
    assert app.values['cloth_enabled'].get() == '0'
    app._start.assert_not_called()
    app.root.after.assert_not_called()


def test_unknown_failure_logs_no_cloth_suggestion_and_still_keeps_polling(controller, monkeypatch):
    app = controller
    failure = dict(stage='read_segment', error='Unknown input failure')
    output = 'RENDERLOCK_DIAGNOSTICS ' + json.dumps(dict(failure=failure))
    monkeypatch.setattr(gui_export_recovery, 'show_failure', Mock(side_effect=tk.TclError('modal unavailable')))
    app.events.put(('finished', ('convert', 1, output)))
    app._poll()
    assert recovery_choices(app._conversion_command, failure) == []
    assert recovery_summary(app._conversion_command, failure) in logged(app)
    assert 'Retry with supported cloth only' not in logged(app)
    assert 'Keep supported cloth' not in logged(app)
    app.root.after.assert_called_once_with(100, app._poll)
    app._start.assert_not_called()
    assert not app.partial.get() and not app.partial_cloth.get()


@pytest.mark.parametrize('selected_id', [None, 'supported_cloth', 'without_cloth'])
def test_only_an_explicit_modal_choice_schedules_the_exact_retry(controller, monkeypatch, selected_id):
    app = controller
    command = app._conversion_command.copy()
    choices = recovery_choices(command, FAILURE)
    selected = next((choice for choice in choices if choice.id == selected_id), None)
    monkeypatch.setattr(gui_export_recovery, 'show_failure', Mock(return_value=selected))
    app.events.put(('finished', ('convert', 1, 'AGR error: ' + ERROR)))
    app._poll()
    app._start.assert_not_called()
    assert not app.partial.get() and not app.partial_cloth.get()
    if selected is None:
        app.root.after.assert_called_once_with(100, app._poll)
        app._save_settings.assert_not_called()
        assert app.values['cloth_enabled'].get() == '1'
        return
    assert [call.args[0] for call in app.root.after.call_args_list] == [0, 100]
    app.root.after.call_args_list[0].args[1]()
    app._start.assert_called_once()
    assert app._start.call_args.args[0] == list(selected.command)
    assert not app.partial.get() and '--partial' not in selected.command
    if selected_id == 'supported_cloth':
        assert selected.command == tuple(command + ['--partial-cloth'])
        assert app.partial_cloth.get() and app.values['cloth_enabled'].get() == '1'
    else:
        assert '--secondary-motion' not in selected.command and not app.partial_cloth.get()
        assert app.values['cloth_enabled'].get() == '0'


@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit])
def test_modal_base_exceptions_propagate_without_retry(controller, monkeypatch, error_type):
    monkeypatch.setattr(gui_export_recovery, 'show_failure', Mock(side_effect=error_type('stop')))
    with pytest.raises(error_type):
        controller._show_conversion_failure('AGR error: ' + ERROR)
    controller._start.assert_not_called()
    controller.root.after.assert_not_called()


@pytest.mark.parametrize('stage', [None, ''])
def test_exact_legacy_cloth_fatal_with_absent_stage_offers_two_scoped_choices(controller, stage):
    command = controller._conversion_command
    failure = {'error': ERROR} if stage is None else {'stage': stage, 'error': ERROR}
    choices = recovery_choices(command, failure)
    assert [choice.id for choice in choices] == ['supported_cloth', 'without_cloth']
    assert choices[0].command == tuple(command + ['--partial-cloth'])
    assert choices[0].partial_override is None and '--partial' not in choices[0].command
    legacy, _, _ = gui_export_recovery.failure_details('AGR error: ' + ERROR)
    assert recovery_choices(command, legacy) == choices


@pytest.mark.parametrize('stage', ['read_segment', 'evaluate_animation', 'model_coverage',
                                   'unknown', 'validate_cloth_preparation_extra', None, []])
def test_explicit_other_or_invalid_stage_cannot_use_legacy_cloth_fallback(controller, stage):
    assert recovery_choices(controller._conversion_command, dict(stage=stage, error=ERROR)) == []


@pytest.mark.parametrize('error', [
    'Requested cloth preparation did not complete:Hero 84: unsupported',
    'Requested cloth preparation did not complete',
    'requested cloth preparation did not complete: Hero 84: unsupported',
    'Other failure: Requested cloth preparation did not complete: Hero 84: unsupported',
    'Cloth preparation character identities differ from this job.',
    'Generated cloth proxy lacks an explicit vertex-map identity',
    'Task dependency references an unavailable slot',
    'Animation skeleton mismatch', 'Unknown cloth failure',
])
def test_unknown_or_core_errors_without_stage_never_get_cloth_choices(controller, error):
    assert recovery_choices(controller._conversion_command, dict(error=error)) == []


def test_legacy_fallback_still_requires_requested_cloth(controller):
    command = [arg for arg in controller._conversion_command if arg != '--secondary-motion']
    assert recovery_choices(command, dict(error=ERROR)) == []
    choices = recovery_choices(controller._conversion_command + ['--partial-cloth'], dict(error=ERROR))
    assert [choice.id for choice in choices] == ['without_cloth']


@pytest.mark.parametrize('after_grab', [False, True])
def test_display_failure_cleans_owned_modal_without_touching_another_grab(monkeypatch, after_grab):
    # Simulate modal display/grab state; never call a real grab or map a window.
    other_window = object()
    state = {'grab': other_window}
    window = SimpleNamespace()

    def destroy():
        if state['grab'] is window:
            state['grab'] = None

    window.destroy = Mock(side_effect=destroy)
    create = Mock(return_value=window)

    def display(*args):
        assert args[3] is window
        if after_grab:
            state['grab'] = window
        raise tk.TclError('injected display failure')

    monkeypatch.setattr(gui_export_recovery.tk, 'Toplevel', create)
    monkeypatch.setattr(gui_export_recovery, '_show_failure_window', display)
    root = object()
    with pytest.raises(tk.TclError, match='injected display failure'):
        gui_export_recovery.show_failure(SimpleNamespace(root=root), [], 'AGR error: Unknown failure')
    create.assert_called_once_with(root)
    window.destroy.assert_called_once_with()
    assert state['grab'] is (None if after_grab else other_window)


def test_already_closed_modal_preserves_the_explicit_choice(monkeypatch):
    choice = recovery_choices(['python', '-m', 'agr', 'convert', '--cloth'], FAILURE)[0]
    window = SimpleNamespace(destroy=Mock(side_effect=tk.TclError('window already destroyed')))
    monkeypatch.setattr(gui_export_recovery.tk, 'Toplevel', Mock(return_value=window))
    monkeypatch.setattr(gui_export_recovery, '_show_failure_window', Mock(return_value=choice))
    assert gui_export_recovery.show_failure(SimpleNamespace(root=object()),
        list(choice.command), 'AGR error: ' + ERROR) is choice
    window.destroy.assert_called_once_with()


@pytest.mark.parametrize('error_type,already_closed', [
    (tk.TclError, False), (RuntimeError, False), (KeyboardInterrupt, False), (tk.TclError, True),
])
def test_failed_withdrawn_modal_destroys_only_its_owned_window(monkeypatch, error_type, already_closed):
    # No initialize(), update(), mainloop(), native chrome, mapping, focus or grab.
    root = tk.Tk()
    root.withdraw()
    sibling = tk.Toplevel(root)
    sibling.withdraw()
    created = []
    real_toplevel = tk.Toplevel

    def create(parent):
        window = real_toplevel(parent)
        created.append(window)
        return window

    def fail_builder(parent, **_kwargs):
        assert parent is created[0] and parent.state() == 'withdrawn'
        assert root.state() == sibling.state() == 'withdrawn'
        if already_closed:
            parent.destroy()
        raise error_type('injected builder error before deiconify')

    forbidden = Mock(side_effect=AssertionError('This test must never map, focus, or grab a window'))
    prepare = Mock(return_value=False)
    monkeypatch.setattr(gui_export_recovery.tk, 'Toplevel', create)
    monkeypatch.setattr(real_toplevel, 'deiconify', forbidden)
    monkeypatch.setattr(real_toplevel, 'wait_visibility', forbidden)
    monkeypatch.setattr(real_toplevel, 'grab_set', forbidden)
    monkeypatch.setattr(real_toplevel, 'focus_set', forbidden)
    monkeypatch.setattr(gui_export_recovery.WindowChrome, 'prepare', prepare)
    monkeypatch.setattr(gui_export_recovery.WindowChrome, 'finish', forbidden)
    monkeypatch.setattr(gui_export_recovery.theme, 'brand_window', lambda _window: None)
    monkeypatch.setattr(gui_export_recovery.theme, 'brand_emblem', lambda _window: None)
    monkeypatch.setattr(gui_export_recovery, 'Card', fail_builder)
    try:
        with pytest.raises(error_type, match='injected builder error before deiconify'):
            gui_export_recovery.show_failure(SimpleNamespace(root=root),
                ['python', '-m', 'agr', 'convert', '--secondary-motion'], 'AGR error: ' + ERROR)
        prepare.assert_called_once()
        forbidden.assert_not_called()
        assert len(created) == 1 and not created[0].winfo_exists()
        assert root.winfo_exists() and sibling.winfo_exists()
        assert root.winfo_children() == [sibling]
    finally:
        root.destroy()
