"""Motion policy and timer lifecycles, without creating any native windows."""
import json
from types import SimpleNamespace
from weakref import WeakSet

import pytest

from agr import gui, gui_backdrop, gui_theme, gui_widgets, update_window


class Canvas:
    def __init__(self):
        self.jobs = {}
        self.bounds = None
        self.state = "hidden"
        self.serial = 0
        self.alive = True

    def bind(self, *args, **kw):
        pass

    def after(self, delay, callback):
        self.serial += 1
        self.jobs[self.serial] = callback
        return self.serial

    def after_cancel(self, job):
        del self.jobs[job]

    def create_rectangle(self, *bounds, **kw):
        self.bounds = bounds
        return 1

    def coords(self, item, *bounds):
        self.bounds = bounds

    def itemconfigure(self, item, **kw):
        self.state = kw.get("state", self.state)

    def delete(self, item):
        self.bounds = None

    def winfo_exists(self):
        return self.alive

    def winfo_toplevel(self):
        return self


@pytest.fixture(autouse=True)
def motion_policy(monkeypatch):
    monkeypatch.setattr(gui_theme, "_motion_disabled", False)
    monkeypatch.setattr(gui_theme, "_motion_listeners", WeakSet())
    monkeypatch.setattr(gui_theme.sys, "platform", "linux")
    monkeypatch.delenv("D2B_UI_MOTION", raising=False)
    monkeypatch.setattr(gui_widgets.BoilClock, "members", [])
    monkeypatch.setattr(gui_widgets.BoilClock, "job", None)
    monkeypatch.setattr(gui_widgets.BoilClock, "root", None)


def test_app_switch_never_overrides_environment(monkeypatch):
    assert gui_theme.motion_enabled()
    gui_theme.set_motion_disabled(True)
    assert not gui_theme.motion_enabled()
    monkeypatch.setenv("D2B_UI_MOTION", "0")
    gui_theme.set_motion_disabled(False)
    assert not gui_theme.motion_enabled()


def test_app_switch_never_overrides_system_preference(monkeypatch):
    monkeypatch.setattr(gui_theme, "_system_motion_enabled", lambda: False)
    gui_theme.set_motion_disabled(False)
    assert not gui_theme.motion_enabled()


@pytest.mark.parametrize("contents, expected", [(None, False), ("broken", False),
    ('[]', False), ('{}', False), ('{"disable_ui_animations":"1"}', True),
    ('{"disable_ui_animations":"0"}', False)])
def test_standalone_preference_load(tmp_path, monkeypatch, contents, expected):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(gui_theme, "settings_path", lambda: path)
    if contents is not None:
        path.write_text(contents)
    assert gui_theme.saved_motion_disabled() is expected


def test_app_change_saves_immediately_and_reloads(tmp_path, monkeypatch):
    import tkinter as tk
    tcl = tk.Tcl()  # a real Tcl interpreter; no Tk/display
    path = tmp_path / "settings.json"
    monkeypatch.setattr(gui, "settings_path", lambda: path)
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(tcl, value=value) for key, value in
        {"mode": "replay", "asset_source": "installed", "disable_ui_animations": "1"}.items()}
    app._motion_changed()
    assert not gui_theme.motion_enabled()
    assert json.loads(path.read_text())["disable_ui_animations"] == "1"
    app.values["disable_ui_animations"].set("0")
    app._load_settings()
    assert app.values["disable_ui_animations"].get() == "1"


@pytest.mark.parametrize("kind", ["canvas", "paper"])
def test_active_progress_stops_timers_but_stays_visible(kind):
    canvas = Canvas()
    if kind == "canvas":
        progress = gui_widgets.CanvasProgress(canvas, theme={"ACCENT": "#c6a15b"})
        progress.place(10, 210, 30)
        progress.grid()
    else:
        progress = update_window._PaperProgress(canvas, 200, 3)
        progress.place(10, 30)
    progress.start()
    assert len(canvas.jobs) == 1
    progress.start()
    assert len(canvas.jobs) == 1
    gui_theme.set_motion_disabled(True)
    assert not canvas.jobs and progress._job is None
    assert canvas.bounds[:3] == (10, 30, 210)
    if kind == "canvas":
        assert canvas.state == "normal"
        progress.place(20, 320, 35)
        assert canvas.bounds[:3] == (20, 35, 320)
    gui_theme.set_motion_disabled(False)
    assert len(canvas.jobs) == 1
    progress.stop()
    assert not canvas.jobs
    gui_theme.set_motion_disabled(True)
    gui_theme.set_motion_disabled(False)
    assert not canvas.jobs  # stopped indicators must not restart


def test_clock_freezes_without_advancing_then_resumes_once():
    member = Canvas()
    advanced = []
    member.advance = lambda: advanced.append(1)
    gui_widgets.BoilClock.register(member)
    assert len(member.jobs) == 1
    gui_theme.set_motion_disabled(True)
    assert not member.jobs
    gui_widgets.BoilClock.tick()  # a stale callback still cannot advance
    assert not advanced and not member.jobs
    gui_theme.set_motion_disabled(False)
    assert len(member.jobs) == 1
    gui_theme.set_motion_disabled(False)
    assert len(member.jobs) == 1
    member.alive = False
    gui_theme.set_motion_disabled(True)
    assert not member.jobs and not gui_widgets.BoilClock.members


def test_backdrop_discards_queued_opacity_frames():
    canvas = Canvas()
    backdrop = gui_backdrop.Backdrop.__new__(gui_backdrop.Backdrop)
    backdrop.root = canvas
    backdrop._render_job = canvas.after(30, lambda: None)
    backdrop._queue = [80, 60, 40, 20]
    backdrop.frozen = False
    gui_theme.set_motion_disabled(True)
    backdrop.motion_changed()
    assert not backdrop._queue and len(canvas.jobs) == 1  # one static repaint
    assert backdrop._level() == 100


def test_update_paper_cancels_build_and_boil_on_disable():
    canvas = Canvas()
    window = update_window.UpdateWindow.__new__(update_window.UpdateWindow)
    window.canvas = canvas
    window._queue = ["pending"]
    window._boil_job = canvas.after(1000, lambda: None)
    window._build_job = canvas.after(15, lambda: None)
    window._mode = "progress"
    window._sources = {"progress": object()}
    gui_theme.set_motion_disabled(True)
    window.motion_changed()
    assert not canvas.jobs and not window._queue
    assert window._boil_job is window._build_job is None
