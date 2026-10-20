"""Selections and inspected rosters stay bound to the user's current input."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from types import SimpleNamespace

import pytest

from agr import cli, gui
from agr.scene_package import save_scene


def command_values(tmp_path):
    for name in ("replay.dem", "blender.exe", "Source2Viewer-CLI.exe"):
        (tmp_path / name).touch()
    return dict(input=str(tmp_path / "replay.dem"), mode="replay", asset_source="installed",
                game_install=str(tmp_path), source2viewer=str(tmp_path / "Source2Viewer-CLI.exe"),
                blender=str(tmp_path / "blender.exe"), output=str(tmp_path / "result.blend"),
                start="10", end="20", fps="64", heroes="", heroes_empty="0", shot_hero="")


def test_explicit_none_cannot_become_all_after_settings_roundtrip(tmp_path, monkeypatch):
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    values = dict(command_values(tmp_path), heroes_empty="1")
    app.values = {k: tk.StringVar(interpreter, value=v) for k, v in values.items()}
    monkeypatch.setattr(gui, "settings_path", lambda: tmp_path / "settings.json")
    app._save_settings()
    app.values["heroes_empty"].set("0")
    app._load_settings()
    with pytest.raises(ValueError, match="at least one character"):
        gui.build_convert_command(app._current_values())


@pytest.mark.parametrize("selected,expected", [("", None), ("11,2,11", [11, 2])])
def test_character_selection_reaches_real_cli_parser(tmp_path, selected, expected):
    values = dict(command_values(tmp_path), heroes=selected)
    args = cli.parser().parse_args(gui.build_convert_command(values)[3:])
    assert args.heroes == expected


def test_follow_camera_excluded_character_is_explained_before_start(tmp_path):
    values = dict(command_values(tmp_path), heroes="2", shot_hero="11")
    with pytest.raises(ValueError, match="follow camera hero must be selected"):
        gui.build_convert_command(values)
    values["heroes"] = "2,11"
    assert cli.parser().parse_args(gui.build_convert_command(values)[3:]).shot_hero == 11


def controller(tmp_path):
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    values = dict(command_values(tmp_path), start="", end="")
    app.values = {k: tk.StringVar(interpreter, value=v) for k, v in values.items()}
    app.status = tk.StringVar(interpreter)
    app.input_summary = tk.StringVar(interpreter)
    loaded, cleared = [], []
    app.character_picker = SimpleNamespace(has_roster=True, hero_ids={11}, load=loaded.append,
        clear=lambda **kw: cleared.append(kw), set_busy=lambda busy: None)
    app.follow_rosters,app.follow_clears=[],[]
    app.follow_picker=SimpleNamespace(load=app.follow_rosters.append,
        clear=lambda **kw:app.follow_clears.append(kw),set_busy=lambda busy:None)
    app.root = SimpleNamespace(after=lambda *args: None, destroy=lambda: None)
    app.events = queue.Queue()
    app.cancel_requested = threading.Event()
    app.close_when_done = False
    app.busy = False
    app._set_busy = lambda busy: setattr(app, "busy", busy)
    app._append_log = lambda text: None
    app._save_settings = lambda: None
    app._inspect_source = app._input_identity()
    app._roster_source = None
    app._roster_complete = False
    return app, loaded, cleared


@pytest.mark.parametrize("change", ["path", "mode", "contents"])
def test_late_inspection_result_cannot_populate_a_changed_input(tmp_path, change):
    app, loaded, cleared = controller(tmp_path)
    if change == "path":
        other = tmp_path / "other.dem"
        other.touch()
        app.values["input"].set(str(other))
    elif change == "mode":
        app.values["mode"].set("segment")
    else:
        Path(app.values["input"].get()).write_bytes(b"changed file")
    app._input_changed()
    app.events.put(("finished", ("inspect", 0, json.dumps(dict(total_ticks=1000, heroes=[{"hero_id": 11}])))))
    app._poll()
    assert loaded == [] and cleared == [{"reset_selection": True}, {"reset_selection": False}]
    assert app.follow_rosters==[] and app.follow_clears==[{"reset_selection":True},{"reset_selection":False}]
    assert app.values["end"].get() == ""
    assert "Input changed" in app.status.get()


def test_successful_inspection_loads_checklist_and_keeps_short_preview(tmp_path):
    app, loaded, _ = controller(tmp_path)
    metadata = dict(total_ticks=1000, heroes=[{"hero_id": 11, "hero_name": "Dynamo"}])
    app.events.put(("finished", ("inspect", 0, json.dumps(metadata))))
    app._poll()
    assert loaded == [metadata]
    assert app.follow_rosters==[metadata]
    assert app._roster_source == app._input_identity()
    assert (app.values["start"].get(), app.values["end"].get()) == ("0", "120")


@pytest.mark.parametrize("start,end,expected", [("60000", "60030", ("0", "120")),
                                              ("100", "60030", ("100", "220")),
                                              ("100", "50", ("100", "220")),
                                              ("100", "130", ("100", "130"))])
def test_inspection_repairs_out_of_range_ticks_retained_from_another_recording(tmp_path, start, end, expected):
    app, _, _ = controller(tmp_path)
    app.values["start"].set(start)
    app.values["end"].set(end)
    app._apply_metadata(dict(total_ticks=1000, heroes=[{"hero_id": 11}]))
    assert (app.values["start"].get(), app.values["end"].get()) == expected


def test_file_replaced_after_inspection_requires_refresh_before_export(tmp_path, monkeypatch):
    app, _, cleared = controller(tmp_path)
    app._roster_source = app._input_identity()
    app.values["heroes"].set("11")
    path = Path(app.values["input"].get())
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    errors, starts = [], []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kw: errors.append(args))
    app._start = lambda *args: starts.append(args)
    app._convert()
    app._convert()
    assert not starts and len(errors) == 2 and "changed since inspection" in errors[0][1]
    assert cleared == [{"reset_selection": False}, {"reset_selection": False}]
    assert app.follow_clears==[{"reset_selection":False},{"reset_selection":False}]
    assert app.values["heroes"].get() == "11"


@pytest.mark.parametrize("shot", ["2", "+999", "9_99"])
def test_known_absent_follow_hero_is_rejected_even_with_all_selected(tmp_path, monkeypatch, shot):
    app, _, _ = controller(tmp_path)
    app._apply_metadata(dict(total_ticks=1000, heroes=[{"hero_id": 11}]))
    app.values["shot_hero"].set(shot)
    errors, starts = [], []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kw: errors.append(args))
    app._start = lambda *args: starts.append(args)
    app._convert()
    assert not starts and "not listed" in errors[0][1]


def test_browsing_a_recording_automatically_inspects_it(tmp_path, monkeypatch):
    app, _, _ = controller(tmp_path)
    selected = str(tmp_path / "replay.dem")
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: selected)
    calls = []
    app._inspect = lambda: calls.append(app.values["input"].get())
    app._browse_input()
    assert calls == [selected]
    assert app.values["output"].get().endswith("result.blend")


def test_saved_package_inspection_lists_names_and_repeated_hero_tracks(tmp_path):
    app, _, _ = controller(tmp_path)
    scene = dict(schema_version=1, tick_rate=64, start_tick=10, end_tick=20, entities=[
        dict(id=identity, hero_id=11, hero_name="Dynamo", samples=[
            dict(tick=10, position=[0, 0, 0], body_angles=[0, 0, 0])])
        for identity in ("12:34", "56:78")])
    path = save_scene(scene, tmp_path / "manifest.json")
    app.values["input"].set(str(path))
    app.values["mode"].set("segment")
    calls = []
    app._start = lambda command, operation: calls.append((command, operation))
    app._inspect()
    assert calls[0][1] == "inspect"
    result = subprocess.run(calls[0][0], capture_output=True, text=True, check=True,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    metadata = json.loads(result.stdout)
    assert metadata["roster_source"] == "saved_package_tracks"
    assert [e["hero_name"] for e in metadata["roster"]] == ["Dynamo", "Dynamo"]
    assert [e["id"] for e in metadata["roster"]] == ["12:34", "56:78"]
