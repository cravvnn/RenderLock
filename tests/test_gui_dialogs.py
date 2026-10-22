from types import SimpleNamespace
import tkinter as tk
from tkinter import messagebox as native_messagebox

import pytest

from agr import gui_dialogs, gui_theme, gui_widgets
from agr.gui_chrome import TitleBar, WindowChrome
from agr.gui_widgets import Card, RoundedButton


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@pytest.fixture
def root(monkeypatch):
    gui_widgets._CORNER_CACHE.clear()
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    gui_theme.initialize(window)
    window.geometry('700x500+10+10')
    window.update()
    monkeypatch.setattr(native_messagebox, '_show',
                        lambda *args, **kwargs: pytest.fail('Native messagebox fallback used'))
    callback_errors = []
    window.report_callback_exception = lambda *error: callback_errors.append(error)
    try:
        yield window
    finally:
        if gui_dialogs._exists(window):
            window.destroy()
        gui_widgets._CORNER_CACHE.clear()
        assert not callback_errors, callback_errors


def drive(root, show, action):
    """Exercise a real modal without hanging pytest on a failing Tk callback."""
    errors, observations = [], []

    def interact():
        window = next(child for child in root.winfo_children() if isinstance(child, tk.Toplevel))
        try:
            observations.append(action(window))
        except BaseException as exc:
            errors.append(exc)
        finally:
            if gui_dialogs._exists(window):
                window.destroy()

    root.after(150, interact)
    timeout = root.after(3000, root.destroy)
    try:
        result = show()
        if errors:
            raise errors[0]
        assert observations, 'Dialog did not become interactive before the timeout'
        return result
    finally:
        if gui_dialogs._exists(root):
            root.after_cancel(timeout)


def button(window, label):
    return next(widget for widget in descendants(window)
                if isinstance(widget, RoundedButton) and widget.cget('text') == label)


@pytest.mark.parametrize(('show', 'labels'), [
    (gui_dialogs.showerror, ['OK']),
    (gui_dialogs.showinfo, ['OK']),
    (gui_dialogs.askyesno, ['No', 'Yes']),
])
def test_real_dialog_uses_shared_branding_and_waits_for_explicit_button(root, show, labels):
    def inspect(window):
        widgets = list(descendants(window))
        assert root.grab_current() is window
        assert isinstance(window.chrome, WindowChrome)
        assert any(isinstance(widget, Card) for widget in widgets)
        assert window.icon.width() > 0 and window.logo.width() > 0
        if window.chrome.active:
            assert isinstance(window.title_bar, TitleBar)
        assert sorted(widget.cget('text') for widget in widgets
                      if isinstance(widget, RoundedButton)) == labels
        button(window, labels[0])._invoke()

    result = drive(root, lambda: show('Check settings', 'An actionable message', parent=root), inspect)
    assert result == (False if show is gui_dialogs.askyesno else 'ok')
    assert root.grab_current() is None
    assert not any(isinstance(widget, tk.Toplevel) for widget in root.winfo_children())


@pytest.mark.parametrize(('action', 'expected'), [
    ('Yes', True), ('No', False), ('escape', False), ('close', False), ('destroy', False),
    ('return', False),
])
def test_question_result_and_cancellation(root, action, expected):
    def interact(window):
        if action in ('Yes', 'No'):
            button(window, action)._invoke()
        elif action == 'escape':
            window.focus_force()
            root.update()
            window.event_generate('<Escape>')
            assert not gui_dialogs._exists(window)
        elif action == 'close':
            window.tk.call(window.protocol('WM_DELETE_WINDOW'))
        elif action == 'destroy':
            window.destroy()
        else:
            # The initial button is deliberately No. Return activates that focused button.
            assert window.focus_lastfor() is button(window, 'No')
            button(window, 'No').focus_force()
            root.update()
            button(window, 'No').event_generate('<Return>')
            assert not gui_dialogs._exists(window)

    result = drive(root, lambda: gui_dialogs.askyesno('Stop?', 'Cancel this job?', parent=root), interact)
    assert result is expected


def test_long_message_is_preserved_scrollable_and_keeps_actions_on_screen(root, monkeypatch):
    monkeypatch.setattr(gui_theme, 'SCALE', 2.0)
    message = ('A long diagnostic line with a complete path and Unicode: D:/retained/\u03b1\n' * 150
               + 'X' * 4000)

    def inspect(window):
        text = next(widget for widget in descendants(window) if isinstance(widget, tk.Text))
        assert text.get('1.0', 'end-1c') == message
        assert text.cget('wrap') == 'word' and text.cget('state') == 'disabled'
        assert text.yview()[1] < 1
        text.yview_moveto(1)
        assert text.yview()[0] > 0
        assert window.winfo_width() <= window.winfo_screenwidth()
        assert window.winfo_height() <= window.winfo_screenheight()
        ok = button(window, 'OK')
        assert ok.winfo_rooty() + ok.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        ok._invoke()

    assert drive(root, lambda: gui_dialogs.showerror('Diagnostic details', message, parent=root), inspect) == 'ok'


def test_existing_default_root_and_child_parent_use_same_interpreter(root):
    child = tk.Frame(root)
    child.pack()
    for parent in (None, child):
        def inspect(window):
            assert window.master is root
            assert window.tk is root.tk
            button(window, 'OK')._invoke()

        assert drive(root, lambda: gui_dialogs.showinfo('Ready', 'Details', parent=parent), inspect) == 'ok'


def test_withdrawn_parent_does_not_block_visibility(root):
    root.withdraw()
    assert drive(root, lambda: gui_dialogs.askyesno('Continue?', 'Details', parent=root),
                 lambda window: button(window, 'No')._invoke()) is False


def test_previous_modal_grab_and_focus_are_restored(root):
    entry = tk.Entry(root)
    entry.pack()
    entry.focus_force()
    root.update()
    root.grab_set()
    assert drive(root, lambda: gui_dialogs.askyesno('Continue?', 'Details', parent=root),
                 lambda window: button(window, 'Yes')._invoke()) is True
    assert root.grab_current() is root
    assert root.focus_lastfor() is entry
    root.grab_release()


def test_newer_grab_is_not_replaced_on_return(root):
    newer = tk.Toplevel(root)
    newer.update()

    def interact(window):
        button(window, 'No')._invoke()
        newer.grab_set()

    # Exclude the pre-existing owner from drive's dialog lookup.
    original = root.winfo_children
    root.winfo_children = lambda: [child for child in original() if child is not newer]
    try:
        assert drive(root, lambda: gui_dialogs.askyesno('Continue?', 'Details', parent=root), interact) is False
        assert root.grab_current() is newer
    finally:
        root.winfo_children = original
        newer.destroy()


@pytest.mark.parametrize('phase', ['mapping', 'waiting'])
def test_owner_destruction_safely_cancels_without_returning_yes(root, phase):
    if phase == 'mapping':
        root.after_idle(root.destroy)
    else:
        root.after(150, root.destroy)
    assert gui_dialogs.askyesno('Continue?', 'Details', parent=root) is False


def test_no_root_requires_existing_owner_without_creating_a_native_dialog(monkeypatch):
    monkeypatch.setattr(tk, '_default_root', None)
    monkeypatch.setattr(tk, 'Tk', lambda: pytest.fail('Unexpected Tk interpreter'))
    monkeypatch.setattr(native_messagebox, '_show', lambda *args, **kwargs: pytest.fail('Native dialog'))
    with pytest.raises(RuntimeError, match='existing Tk window'):
        gui_dialogs.askyesno('Continue?', 'Details')


@pytest.mark.parametrize('first', [True, False])
def test_completion_is_idempotent_and_only_an_explicit_yes_accepts(first):
    destroys = []
    dialog = gui_dialogs._MessageDialog.__new__(gui_dialogs._MessageDialog)
    dialog.window = SimpleNamespace(winfo_exists=lambda: not destroys, destroy=lambda: destroys.append(True))
    dialog.result, dialog.finished = False, False
    assert dialog.choose(first) == 'break'
    assert dialog.cancel() == 'break'
    dialog.choose(not first)
    assert dialog.result is first
    assert len(destroys) == 1


def test_work_area_queries_the_owner_monitor_through_existing_chrome():
    queries = []
    api = SimpleNamespace(wintypes=SimpleNamespace(HWND=lambda value: value),
                          maximized_area=lambda hwnd: queries.append(hwnd) or (-2560, 40, 2560, 1400))
    parent = SimpleNamespace(wm_frame=lambda: '0x1234')
    assert gui_dialogs._work_area(parent, SimpleNamespace(api=api)) == (-2560, 40, 0, 1440)
    assert queries == [0x1234]


@pytest.mark.parametrize('api', [None, SimpleNamespace(
    wintypes=SimpleNamespace(HWND=lambda value: value), maximized_area=lambda hwnd: None)])
def test_work_area_fallback_preserves_virtual_desktop_origin(api):
    parent = SimpleNamespace(wm_frame=lambda: '0x1234', winfo_vrootx=lambda: -2560,
                             winfo_vrooty=lambda: -1200, winfo_vrootwidth=lambda: 6400,
                             winfo_vrootheight=lambda: 2640)
    assert gui_dialogs._work_area(parent, SimpleNamespace(api=api)) == (-2560, -1200, 3840, 1440)


@pytest.mark.parametrize(('area', 'origin', 'visible', 'expected'), [
    ((1920, 40, 3840, 1080), (2300, 200), True, '620x300+2440+400'),
    ((-2560, 0, 0, 1440), (-2500, 100), True, '620x300+-2360+300'),
    ((0, -1200, 1920, 0), (100, -1150), True, '620x300+240+-950'),
    ((1920, 40, 3840, 1080), (3800, 1000), True, '620x300+3220+780'),
    ((-2560, 40, 0, 1440), (0, 0), False, '620x300+-1590+590'),
])
def test_geometry_stays_in_owner_work_area_including_negative_origins(monkeypatch, area, origin,
                                                                    visible, expected):
    monkeypatch.setattr(gui_theme, 'SCALE', 1.0)
    placements = []
    dialog = gui_dialogs._MessageDialog.__new__(gui_dialogs._MessageDialog)
    dialog.width, dialog.max_height, dialog.work_area = 620, area[3] - area[1] - 80, area
    dialog.window = SimpleNamespace(update_idletasks=lambda: None, winfo_reqheight=lambda: 300,
                                    geometry=placements.append)
    dialog.parent = SimpleNamespace(winfo_viewable=lambda: visible, winfo_rootx=lambda: origin[0],
                                    winfo_rooty=lambda: origin[1], winfo_width=lambda: 900,
                                    winfo_height=lambda: 700)
    dialog._position()
    assert placements == [expected]
