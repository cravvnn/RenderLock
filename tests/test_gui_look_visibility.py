"""Contextual Lighting lab fields, using real Tk controls without AGRApp.

The root stays withdrawn: geometry-manager checks exercise show/hide behavior,
not screen pixels. Dummy helper files establish presence only.
"""
from pathlib import Path
import re
from types import SimpleNamespace
import tkinter as tk

import pytest

from agr import gui_layout, gui_theme, gui_widgets


LOOKS = ("standard", "source", "elagrimm_source", "custom")
TOOLS = ("probe_atlas_decoder", "cubemap_decoder", "lighting_dotnet")
PATHS = ("character_shader", "world_region_probe", *TOOLS)


@pytest.fixture
def look_app(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    # Corner images belong to a Tcl interpreter; do not reuse another test's.
    monkeypatch.setattr(gui_widgets, "_CORNER_CACHE", {})
    try:
        body = tk.Frame(root, background=gui_theme.CARD)
        body.pack()
        stored = dict(character_look="standard", asset_source="installed",
                      source_lighting="1", source_hero_lighting="0",
                      source_npr_lighting="1")
        for key, name in (
            ("character_shader", "Elagrimm.blend"),
            ("world_region_probe", "WorldRegionProbe.dll"),
            ("probe_atlas_decoder", "ProbeAtlasDecode.dll"),
            ("cubemap_decoder", "CubemapDecode.dll"),
            ("lighting_dotnet", "dotnet.exe"),
        ):
            path = tmp_path / name
            path.write_bytes(b"Synthetic file, not a qualified library or executable.")
            stored[key] = str(path)
        app = SimpleNamespace(
            root=root, busy=False,
            values={key: tk.StringVar(root, value=value) for key, value in stored.items()},
            lighting_consent=tk.BooleanVar(root, value=False),
            look_description=tk.StringVar(root),
            look_tools_status=tk.StringVar(root),
            look_tools_expanded=False,
        )
        app.look_switch = gui_widgets.SegmentedControl(
            body, options=tuple(zip(("Standard", "Source", "Elagrimm + Source", "Custom"), LOOKS)),
            variable=app.values["character_look"], theme=gui_theme.palette(),
        )
        app.look_switch.grid(row=0, column=0)
        for row, name in enumerate(("look_custom_fields", "look_shader_fields",
                                    "look_consent_fields", "look_tools_fields"), start=1):
            frame = tk.Frame(body, background=gui_theme.CARD)
            frame.grid(row=row, column=0, sticky="ew")
            setattr(app, name, frame)
        for row, key in enumerate(("source_lighting", "source_hero_lighting", "source_npr_lighting")):
            gui_widgets.CheckBox(
                app.look_custom_fields, text=key, variable=app.values[key],
                onvalue="1", offvalue="0", theme=gui_theme.palette(),
            ).grid(row=row, column=0)
        tk.Entry(app.look_shader_fields, textvariable=app.values["character_shader"]).grid(row=0, column=0)
        app.consent_check = gui_widgets.CheckBox(
            app.look_consent_fields, text="Accept partial lighting limits",
            variable=app.lighting_consent, theme=gui_theme.palette(),
        )
        app.consent_check.grid(row=0, column=0)
        tk.Label(app.look_tools_fields, textvariable=app.look_tools_status).grid(row=0, column=0)
        app.look_tools_toggle = gui_widgets.RoundedButton(
            app.look_tools_fields, text="Advanced tool overrides", theme=gui_theme.palette(),
            command=lambda: gui_layout.toggle_look_tool_overrides(app),
        )
        app.look_tools_toggle.grid(row=1, column=0)
        app.look_tool_overrides = tk.Frame(app.look_tools_fields, background=gui_theme.CARD)
        app.look_tool_overrides.grid(row=2, column=0)
        for row, key in enumerate(TOOLS):
            tk.Entry(app.look_tool_overrides, textvariable=app.values[key]).grid(row=row, column=0)
        gui_layout.refresh_character_look(app)
        yield app
    finally:
        root.destroy()


def _paths(app):
    return {key: app.values[key].get() for key in PATHS}


def _select(app, mode):
    # Exercise the original segmented control without mapping or focusing it.
    current = LOOKS.index(app.values["character_look"].get())
    app.look_switch._step(LOOKS.index(mode) - current)
    gui_layout.refresh_character_look(app, changed=True)


def _assert_visible(widget, expected):
    assert widget.winfo_manager() == ("grid" if expected else "")
    assert (widget in widget.master.grid_slaves()) is expected


def _assert_tool_status(app, *, found):
    text = app.look_tools_status.get().lower()
    assert text
    if found:
        assert "not found" not in text
        assert re.search(r"\b(found|detected)\b", text), text
    else:
        assert "not found" in text, text
    assert not re.search(r"\b(qualified|validated|verified|compatible|ready)\b", text), text


@pytest.mark.parametrize("mode,shader,tools,manual,consent", [
    ("standard", False, False, False, False),
    ("source", False, True, False, True),
    ("elagrimm_source", True, True, False, True),
    ("custom", True, True, True, True),
])
def test_fields_follow_look_even_with_existing_saved_paths(look_app, mode, shader, tools, manual, consent):
    app = look_app
    before = _paths(app)
    assert all(Path(path).is_file() for path in before.values())
    _select(app, mode)
    _assert_visible(app.look_shader_fields, shader)
    _assert_visible(app.look_tools_fields, tools)
    _assert_visible(app.look_custom_fields, manual)
    _assert_visible(app.look_consent_fields, consent)
    _assert_visible(app.look_tool_overrides, False)
    assert app.look_tools_expanded is False
    assert _paths(app) == before


@pytest.mark.parametrize("mode", LOOKS[1:])
def test_advanced_overrides_open_and_close_without_losing_consent_or_paths(look_app, mode):
    app = look_app
    _select(app, mode)
    app.consent_check._toggle()
    assert app.lighting_consent.get() is True
    before = _paths(app)
    closed_text = app.look_tools_toggle.cget("text")

    gui_layout.toggle_look_tool_overrides(app)
    _assert_visible(app.look_tool_overrides, True)
    assert app.look_tools_expanded is True
    assert "hide" in app.look_tools_toggle.cget("text").lower()
    gui_layout.refresh_character_look(app)
    _assert_visible(app.look_tool_overrides, True)
    assert app.lighting_consent.get() is True

    gui_layout.toggle_look_tool_overrides(app)
    _assert_visible(app.look_tool_overrides, False)
    assert app.look_tools_expanded is False
    assert app.look_tools_toggle.cget("text") == closed_text
    assert app.lighting_consent.get() is True
    assert _paths(app) == before


@pytest.mark.parametrize("initial,target", [
    ("source", "standard"),
    ("elagrimm_source", "source"),
    ("custom", "elagrimm_source"),
    ("source", "custom"),
])
def test_mode_change_collapses_overrides_and_resets_only_consent(look_app, initial, target):
    app = look_app
    _select(app, initial)
    gui_layout.toggle_look_tool_overrides(app)
    app.consent_check._toggle()
    assert app.look_tools_expanded is True and app.lighting_consent.get() is True
    before = {key: var.get() for key, var in app.values.items()}

    _select(app, target)
    assert app.values["character_look"].get() == target
    assert app.lighting_consent.get() is False
    assert app.look_tools_expanded is False
    _assert_visible(app.look_tool_overrides, False)
    assert {key: var.get() for key, var in app.values.items()} == dict(before, character_look=target)
    gui_layout.refresh_character_look(app)
    _assert_visible(app.look_tool_overrides, False)


@pytest.mark.parametrize("expanded", [False, True])
def test_busy_state_prevents_changing_advanced_overrides(look_app, expanded):
    app = look_app
    _select(app, "source")
    if expanded:
        gui_layout.toggle_look_tool_overrides(app)
    app.consent_check._toggle()
    before = _paths(app)
    before_text = app.look_tools_toggle.cget("text")
    app.busy = True
    gui_layout.toggle_look_tool_overrides(app)
    assert app.look_tools_expanded is expanded
    _assert_visible(app.look_tool_overrides, expanded)
    assert app.look_tools_toggle.cget("text") == before_text
    assert app.lighting_consent.get() is True
    assert _paths(app) == before


@pytest.mark.parametrize("key", TOOLS)
def test_tool_status_uses_file_presence_without_claiming_qualification(look_app, tmp_path, key):
    app = look_app
    _select(app, "source")
    _assert_tool_status(app, found=True)
    missing = tmp_path / f"missing-{key}"
    directory = tmp_path / f"directory-{key}"
    directory.mkdir()
    for value in ("", str(missing), str(directory)):
        app.values[key].set(value)
        before = _paths(app)
        gui_layout.refresh_character_look(app)
        _assert_tool_status(app, found=False)
        assert _paths(app) == before

    app.values[key].set(str(missing))
    missing.write_bytes(b"Intentionally invalid executable bytes: presence is not qualification.")
    before = _paths(app)
    gui_layout.refresh_character_look(app)
    _assert_tool_status(app, found=True)
    assert _paths(app) == before
    _assert_visible(app.look_tool_overrides, False)
