"""Distribution limits and bounded preview defaults."""
import tkinter as tk

import pytest

from agr import cli, gui, release_profile


@pytest.mark.parametrize("flag", ["--include-map", "--source-lighting", "--source-hero-lighting",
                                   "--source-npr-lighting", "--character-shader"])
def test_beta_accepts_lab_requests_and_keeps_scenes_character_only(monkeypatch, flag):
    monkeypatch.setattr(release_profile, "character_beta", lambda: True)
    extra = [flag, "library.blend"] if flag == "--character-shader" else [flag]
    args = cli.parser().parse_args(["convert", "--demo", "absent.dem", "--output", "x.blend", *extra])
    release_profile.validate_conversion(args)  # must not raise: the look tools are enabled
    assert args.character_only  # map geometry stays omitted in the beta


def test_beta_omits_map_geometry_from_saved_packages(monkeypatch):
    monkeypatch.setattr(release_profile, "character_beta", lambda: True)
    args = cli.parser().parse_args(["convert", "--segment", "saved.json", "--output", "x.blend"])
    release_profile.validate_conversion(args)
    assert args.character_only


@pytest.mark.parametrize("key", ["hero_lighting", "npr_runtime"])
def test_beta_saved_package_accepts_lighting_inputs(monkeypatch, key):
    monkeypatch.setattr(release_profile, "character_beta", lambda: True)
    release_profile.validate_scene({key: {}})  # must not raise: saved look packages are allowed


def test_beta_gui_keeps_lighting_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(release_profile, "character_beta", lambda: True)
    for name in ("input.dem", "blender.exe", "Source2Viewer-CLI.exe"):
        (tmp_path / name).touch()
    values = dict(input=str(tmp_path / "input.dem"), blender=str(tmp_path / "blender.exe"),
        source2viewer=str(tmp_path / "Source2Viewer-CLI.exe"), game_install=str(tmp_path),
        output=str(tmp_path / "x.blend"), start="12000", end="12120", fps="64",
        map_enabled="1", source_lighting="1", source_hero_lighting="1", source_npr_lighting="1",
        character_shader="missing.blend", mode="replay")
    # The lighting flag must reach validation instead of being silently
    # stripped; the missing regional exporter then fails explicitly.
    with pytest.raises(ValueError, match="WorldRegionProbe"):
        gui.build_convert_command(values)
    assert values["map_enabled"] == "1"  # Original preferences survive.


@pytest.mark.parametrize("start,end,expected", [("", "", ("0", "120")),
    ("12000", "", ("12000", "12120")), ("146150", "", ("146150", "146208")),
    ("12000", "12150", ("12000", "12150"))])
def test_inspect_prefers_short_preview_and_preserves_user_range(start, end, expected):
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(master=interpreter, value=value) for key, value in (("start", start), ("end", end))}
    app._apply_metadata(dict(total_ticks=146208))
    assert tuple(app.values[key].get() for key in ("start", "end")) == expected
