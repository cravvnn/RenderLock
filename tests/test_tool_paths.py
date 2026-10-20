"""Portable moves refresh bundled tools without replacing external overrides."""
import json
from pathlib import Path
import tkinter as tk

from agr import gui
from agr.tool_paths import PROGRAM_ROOT_SETTING, reconcile_saved_tools


RELATIVES = {
    "source2viewer": "tools/source2viewer/Source2Viewer-CLI.exe",
    "particle_probe": "tools/particles/ParticleProbe.dll",
    "particle_dotnet": "tools/dotnet/dotnet.exe",
    "lighting_dotnet": "tools/dotnet/dotnet.exe",
    "world_region_probe": "tools/lighting/world/WorldRegionProbe.dll",
    "probe_atlas_decoder": "tools/lighting/atlas/ProbeAtlasDecode.dll",
    "cubemap_decoder": "tools/lighting/cube/CubemapDecode.dll",
}


def bundle(root):
    for relative in set(RELATIVES.values()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return {key: str(root / relative) for key, relative in RELATIVES.items()}


def test_removed_release_root_rebinds_all_bundle_tools(tmp_path):
    current = tmp_path / "moved 雪 & spaces"
    expected = bundle(current)
    old = tmp_path / "RenderLock-0.3.0b6-Windows-x64"
    saved = {key: str(old / relative) for key, relative in RELATIVES.items()}
    result, notices = reconcile_saved_tools(saved, current)
    assert all(result[key] == value for key, value in expected.items())
    assert len(notices) == len(RELATIVES)
    assert not old.exists()


def test_existing_previous_bundle_does_not_keep_using_old_tools(tmp_path):
    old = tmp_path / "old custom folder"
    saved = bundle(old)
    (old / "release_manifest.json").write_text(json.dumps({"kind": "Windows-x64-portable"}))
    current = tmp_path / "new package"
    expected = bundle(current)
    result, _ = reconcile_saved_tools(saved, current)
    assert all(result[key] == value for key, value in expected.items())
    assert all(Path(value).is_file() for value in saved.values())


def test_recorded_root_identifies_removed_renamed_bundle(tmp_path):
    old = tmp_path / "arbitrary old name"
    current = tmp_path / "arbitrary new name"
    expected = bundle(current)
    saved = {key: str(old / relative) for key, relative in RELATIVES.items()}
    saved[PROGRAM_ROOT_SETTING] = str(old)
    result, _ = reconcile_saved_tools(saved, current)
    assert all(result[key] == value for key, value in expected.items())


def test_valid_external_override_and_unidentified_missing_choice_are_preserved(tmp_path):
    current = tmp_path / "package"
    bundle(current)
    external = tmp_path / "my converter/tools/source2viewer/Source2Viewer-CLI.exe"
    external.parent.mkdir(parents=True)
    external.touch()
    missing = tmp_path / "unavailable runtime/dotnet.exe"
    saved = dict(source2viewer=str(external), particle_dotnet=str(missing))
    result, notices = reconcile_saved_tools(saved, current)
    assert result == saved
    assert len(notices) == 1
    assert "particle .NET runtime" in notices[0] and "Advanced" in notices[0]
    assert str(missing) in notices[0]


def test_gui_settings_roundtrip_records_root_and_repairs_on_next_load(tmp_path, monkeypatch):
    old = tmp_path / "old renamed folder"
    current = tmp_path / "new renamed folder"
    saved = bundle(old)
    expected = bundle(current)
    interpreter = tk.Tcl()
    app = gui.AGRApp.__new__(gui.AGRApp)
    app.values = {key: tk.StringVar(interpreter, value=value) for key, value in
                  dict(saved, mode="replay", asset_source="installed").items()}
    path = tmp_path / "settings.json"
    monkeypatch.setattr(gui, "settings_path", lambda: path)
    monkeypatch.setattr(gui, "PROGRAM_ROOT", old)
    app._save_settings()
    assert json.loads(path.read_text())[PROGRAM_ROOT_SETTING] == str(old)
    monkeypatch.setattr(gui, "PROGRAM_ROOT", current)
    app._load_settings()
    assert all(app.values[key].get() == value for key, value in expected.items())
    assert "Updated saved" in app.discovery_note


def test_invalid_disabled_tool_paths_remain_visible_without_crashing_settings(tmp_path):
    saved = dict(particle_probe="bad\x00.dll", lighting_dotnet="bad\x00.exe",
                 tool_program_root="bad\x00root")
    result, notices = reconcile_saved_tools(saved, tmp_path)
    assert result == saved
    assert len(notices) == 2
