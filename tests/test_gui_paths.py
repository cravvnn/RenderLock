"""Desktop selections must not change when the worker uses the app directory."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from agr.gui_paths import normalize_gui_paths, resolve_gui_path


def test_relative_selection_and_output_survive_a_different_worker_directory(tmp_path):
    launch = tmp_path / "launch with spaces"
    worker = tmp_path / "application"
    launch.mkdir()
    worker.mkdir()
    selected = launch / "recording.json"
    selected.write_text('{"from":"selected recording"}', encoding="utf-8")
    # Same filename in the application directory must not change the selection.
    (worker / selected.name).write_text('{"from":"wrong recording"}', encoding="utf-8")
    values = {"input": "recording.json", "output": "exports/result.blend"}
    normalized = normalize_gui_paths(values, base=launch)
    result = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path;import sys;print(Path(sys.argv[1]).read_text())",
         normalized["input"]], cwd=worker, capture_output=True, text=True, check=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert json.loads(result.stdout) == {"from": "selected recording"}
    assert Path(normalized["output"]) == launch / "exports" / "result.blend"
    assert values["input"] == "recording.json"


def test_home_paths_are_resolved_before_validation(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Path.expanduser uses the platform's user-home lookup; supply it explicitly.
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    selected = home / "recording.dem"
    selected.write_bytes(b"replay")
    assert resolve_gui_path("  ~/recording.dem  ").is_file()
    assert normalize_gui_paths({"output": "~/scene.blend"})["output"] == str(home / "scene.blend")


def test_optional_empty_paths_and_non_path_fields_are_preserved(tmp_path):
    values = {"particle_dotnet": "  ", "character_shader": "", "map_name": "dl_midtown",
              "heroes": "11,2", "start": " 1000 ", "check_updates": "1"}
    normalized = normalize_gui_paths(values, base=tmp_path)
    assert normalized == dict(values, particle_dotnet="")


def test_absolute_path_spellings_are_preserved_in_settings(tmp_path):
    absolute = tmp_path.as_posix() + "/existing cache"
    assert normalize_gui_paths({"asset_cache": "  " + absolute + "  "})["asset_cache"] == absolute


@pytest.mark.parametrize("bad_path", ["bad\x00path.dll", "~renderlock_nonexistent_user_67439/probe.dll"])
def test_inactive_bad_tools_do_not_block_normalizing_a_valid_selection(tmp_path, bad_path):
    values = {"input": "recording.dem", "simulate_effects": "0", "source_lighting": "0",
              "particle_probe": bad_path, "world_region_probe": bad_path,
              "character_shader": bad_path}
    normalized = normalize_gui_paths(values, base=tmp_path)
    assert normalized["input"] == str(tmp_path / "recording.dem")
    for key in ("particle_probe", "world_region_probe", "character_shader"):
        assert normalized[key] and values[key] == bad_path
        if "\x00" in bad_path:
            assert normalized[key] == bad_path


def test_optional_tool_home_lookup_failure_keeps_saved_value(monkeypatch, tmp_path):
    original = Path.expanduser

    def unknown_user(path):
        if str(path).startswith("~missing"):
            raise RuntimeError("Could not determine home directory")
        return original(path)

    monkeypatch.setattr(Path, "expanduser", unknown_user)
    values = normalize_gui_paths({"input": "recording.dem", "particle_probe": "~missing/probe.dll"}, base=tmp_path)
    assert values["input"] == str(tmp_path / "recording.dem")
    assert values["particle_probe"] == "~missing/probe.dll"


def test_optional_tool_resolution_oserror_does_not_discard_the_saved_value(monkeypatch, tmp_path):
    original = Path.resolve

    def inaccessible(path, *args, **kwargs):
        if path.name == "offline-probe.dll":
            raise OSError("Network location unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", inaccessible)
    values = normalize_gui_paths({"input": "recording.dem", "particle_probe": "offline-probe.dll"}, base=tmp_path)
    assert values["input"] == str(tmp_path / "recording.dem")
    assert values["particle_probe"] == "offline-probe.dll"


def test_malformed_json_path_values_are_left_for_settings_type_filter(tmp_path):
    values = {"input": None, "blender": False, "particle_probe": [], "output": "scene.blend"}
    normalized = normalize_gui_paths(values, base=tmp_path)
    assert normalized["input"] is None
    assert normalized["blender"] is False
    assert normalized["particle_probe"] == []
    assert normalized["output"] == str(tmp_path / "scene.blend")
