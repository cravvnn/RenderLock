"""Small local desktop launcher for the RenderLock command line pipeline."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, ttk
from . import __version__, gui_theme
from . import gui_dialogs as messagebox
from .cli import default_asset_cache, discover_source2viewer
from .gui_paths import normalize_gui_paths, resolve_gui_path


PROGRAM_ROOT = Path(__file__).resolve().parents[1]
MAP_WORKFLOW_REVISION = "separate-map-v1"


def discover_blender() -> str:
    """Find a local executable without launching Blender or changing user settings."""
    found = shutil.which("blender")
    if found:
        return str(Path(found).resolve())
    candidates: list[Path] = []
    if sys.platform == "win32":
        roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Blender Foundation",
                 Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Blender Foundation"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots += [Path(local) / "Programs" / "Blender Foundation",
                      Path(local) / "Blender Foundation"]
        for base in roots:
            try:
                candidates.extend(base.glob("Blender */blender.exe"))
                if (base / "blender.exe").is_file():
                    candidates.append(base / "blender.exe")
            except OSError:
                continue
        # Prefer the most recent numbered installation when several are present.
        candidates.sort(key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parent.name)),
                        reverse=True)
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Blender.app/Contents/MacOS/Blender"),
                      Path.home() / "Applications/Blender.app/Contents/MacOS/Blender"]
    return next((str(path.resolve()) for path in candidates if path.is_file()), "")


def discover_assets() -> str:
    candidates = [PROGRAM_ROOT / "assets"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / "assets")
    return next((str(path.resolve()) for path in candidates if path.is_dir()), "")


def discover_particle_runtime() -> tuple[str, str]:
    """Find bundled or development tools without running or downloading them."""
    portable = PROGRAM_ROOT / 'tools/particles/ParticleProbe.exe'
    if sys.platform == 'win32' and portable.is_file():
        return str(portable.resolve()), ''
    probes = [PROGRAM_ROOT / 'tools/particles/ParticleProbe.dll',
              PROGRAM_ROOT / 'research/ParticleProbe/bin/Release/net10.0/ParticleProbe.dll']
    probe = next((str(p.resolve()) for p in probes if p.is_file()), '')
    return probe, _discover_dotnet()


def _discover_dotnet() -> str:
    """Prefer a bundled runtime, then the development toolchain, then PATH."""
    name = 'dotnet.exe' if sys.platform == 'win32' else 'dotnet'
    for base in (PROGRAM_ROOT / '.toolchains/dotnet', PROGRAM_ROOT / 'tools/dotnet'):
        candidate = base / name
        if candidate.is_file():
            return str(candidate.resolve())
    return shutil.which('dotnet') or ''


def discover_lighting_kit() -> dict[str, str]:
    """Find a bundled character-look helper kit without running anything."""
    base = PROGRAM_ROOT / 'tools' / 'lighting'
    result: dict[str, str] = {}
    for key, folder, name in (('world_region_probe', 'world', 'WorldRegionProbe.dll'),
                              ('probe_atlas_decoder', 'atlas', 'ProbeAtlasDecode.dll'),
                              ('cubemap_decoder', 'cube', 'CubemapDecode.dll')):
        path = base / folder / name
        if path.is_file():
            result[key] = str(path.resolve())
    dotnet = _discover_dotnet()
    if dotnet:
        result['lighting_dotnet'] = dotnet
    return result


def settings_path() -> Path:
    """Keep per-user paths out of the distributable program directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "RenderLock" / "settings.json"


def asset_source_mode(values: dict[str, str]) -> str:
    """Older settings explicitly selected a prepared pack; preserve that choice."""
    mode = values.get("asset_source")
    if mode in {"installed", "prepared"}:
        return mode
    return "prepared" if values.get("assets", "").strip() else "installed"


def migrate_scene_settings(values: dict[str, str]) -> dict[str, str]:
    """Disable the old automatic map default once, retaining all saved paths.

    Later launches preserve an explicit opt-in made in the separate-map workflow.
    """
    migrated = dict(values)
    if migrated.get("map_workflow_revision") != MAP_WORKFLOW_REVISION:
        migrated["map_enabled"] = "0"
    migrated["map_workflow_revision"] = MAP_WORKFLOW_REVISION
    from .gui_look import migrate_look_settings
    return migrate_look_settings(migrated)


def build_convert_command(values: dict[str, str], *, partial: bool = False,
                          overwrite: bool = False, partial_cloth: bool = False,
                          lighting_consent: bool = False) -> list[str]:
    """Validate editable fields and build argv without shell interpretation."""
    values = normalize_gui_paths(values)
    if values.get('external_map_enabled') == '1':
        values['character_only'] = '1'
    if values.get("heroes_empty", "0") == "1":
        raise ValueError("Select at least one character to export, or choose All.")
    source = values.get("input", "").strip()
    if not source or not Path(source).is_file():
        raise ValueError("Choose an existing replay or saved package manifest.")
    asset_mode = asset_source_mode(values)
    if asset_mode == "prepared":
        assets = values.get("assets", "").strip()
        if not assets or not Path(assets).is_dir():
            raise ValueError("Choose the folder containing your prepared game assets.")
        asset_args = ["--assets", assets]
    else:
        install = values.get("game_install", "").strip()
        if not install or not Path(install).is_dir():
            raise ValueError("Choose your Deadlock installation folder. In Steam, use Manage → Browse local files.")
        converter = values.get("source2viewer", "").strip()
        if not converter or not Path(converter).is_file():
            raise ValueError("Choose the Source 2 Viewer command-line exporter (Source2Viewer-CLI.exe).")
        if Path(converter).name.lower() in {"source2viewer.exe", "source2viewer"}:
            raise ValueError("Select Source2Viewer-CLI.exe, the command-line exporter, instead of the desktop viewer.")
        asset_args = ["--game-install", install, "--source2viewer", converter]
        cache = values.get("asset_cache", "").strip()
        if cache:
            if Path(cache).exists() and not Path(cache).is_dir():
                raise ValueError("Choose a folder for the asset cache.")
            asset_args += ["--asset-cache", cache]
    blender = values.get("blender", "").strip()
    if not blender or not Path(blender).is_file():
        raise ValueError("Choose the Blender executable (blender.exe on Windows).")
    output = values.get("output", "").strip()
    if not output or Path(output).suffix.lower() != ".blend":
        raise ValueError("Choose an output filename ending in .blend.")
    if Path(output).is_dir():
        raise ValueError("The output must be a file, not a folder.")
    output_path = Path(output)
    if not overwrite and any(path.exists() for path in
                             (output_path, output_path.with_suffix(".agrscene"),
                              output_path.with_suffix(".agr-report.json"))):
        raise ValueError("That output or its companion package/report already exists. Choose a new name or explicitly enable overwrite.")
    if Path(output).resolve() == Path(source).resolve():
        raise ValueError("The output must differ from the input file.")
    try:
        start = int(values.get("start", ""))
        end = int(values.get("end", ""))
        fps = int(values.get("fps", ""))
    except ValueError as exc:
        raise ValueError("Start tick, end tick, and FPS must be whole numbers.") from exc
    if start < 0 or end < start:
        raise ValueError("Ticks must be nonnegative, with end tick at least start tick.")
    if not 1 <= fps <= 240:
        raise ValueError("FPS must be between 1 and 240.")
    mode = values.get("mode", "replay")
    if mode not in {"replay", "segment"}:
        raise ValueError("Choose Replay or Saved package as the input type.")
    command = [sys.executable, "-m", "agr", "convert",
               "--demo" if mode == "replay" else "--segment", source,
               *asset_args, "--blender", blender,
               "--start-tick", str(start), "--end-tick", str(end),
               "--fps", str(fps), "--output", output]
    from .release_profile import character_beta
    if values.get('character_look'):
        from .gui_look import resolve_look_settings
        values = resolve_look_settings(values, character_only=character_beta() or values.get('character_only') == '1')
        if values.get('source_lighting') == '1':
            if asset_mode != 'installed':
                raise ValueError('This character look needs Installed game on Setup. Choose Standard materials to use prepared assets without new Source lighting.')
            if lighting_consent is not True:
                raise ValueError('Review the partial lighting limits in Lighting lab and accept them for this export, or choose Standard materials. Character animation remains strict.')
    character_lighting=values.get('source_lighting','0')=='1' and (values.get('character_only','0')=='1' or character_beta())
    if values.get('source_lighting','0') == '1':
        if asset_mode != 'installed' or values.get('map_enabled','0') != '1':
            raise ValueError('Partial Source lighting requires installed assets and Prepare installed map region on Map. This reads lighting data; Characters only can omit map geometry from the scene.')
        from .source_region import regional_command
        lighting_probe=values.get('world_region_probe','').strip()
        lighting_dotnet=values.get('lighting_dotnet','').strip()
        if not character_lighting:
            regional_command(lighting_probe,lighting_dotnet)
        command += ['--source-lighting','--partial-lighting','--lighting-dotnet',lighting_dotnet]
        if lighting_probe:command += ['--world-region-probe',lighting_probe]
    if values.get('source_npr_lighting','0') == '1':
        if values.get('source_hero_lighting','0')!='1':
            raise ValueError('NPR indirect requires Hero Source lighting.')
        if not values.get('shot_hero','').strip():
            raise ValueError('Choose a hero under Follow camera. Inspect the recording first; NPR indirect needs a follow target.')
        command += ['--source-npr-lighting']
    character_shader=values.get('character_shader','').strip()
    if character_shader:
        if values.get('source_npr_lighting','0')!='1':
            raise ValueError('Character shader requires NPR indirect lighting.')
        from .blender_character_shader import validate_character_shader_library
        command += ['--character-shader',str(validate_character_shader_library(character_shader))]
    if values.get('character_only','0')=='1':
        command += ['--character-only']
    if values.get('external_map_enabled', '0') == '1':
        map_path = values.get('external_map', '').strip()
        if not map_path or not Path(map_path).is_file() or Path(map_path).suffix.lower() != '.blend':
            raise ValueError('Choose the downloaded RenderLockMap.blend on Map, or turn off Include downloaded map.')
        if Path(map_path).resolve() == Path(output).resolve():
            raise ValueError('Choose an output different from the downloaded map library.')
        command += ['--external-map', map_path]
    if values.get('source_hero_lighting','0') == '1' or character_lighting:
        if values.get('source_lighting','0') != '1':raise ValueError('Hero lighting requires Partial static diffuse lighting.')
        from .source_hero_lighting import validate_hero_decoder_paths
        atlas=values.get('probe_atlas_decoder','').strip();cube=values.get('cubemap_decoder','').strip()
        validate_hero_decoder_paths(atlas,cube,values.get('lighting_dotnet','').strip())
        command += ['--probe-atlas-decoder',atlas,'--cubemap-decoder',cube]
        if values.get('source_hero_lighting','0')=='1':command += ['--source-hero-lighting']
    if asset_mode == "installed":
        if values.get("map_enabled", "0") == "1":
            from .game_assets import safe_resource_path
            map_name = values.get("map_name", "").strip()
            if not map_name:
                raise ValueError("Enter the installed map name, such as dl_midtown, or turn off Convert map region.")
            map_name = safe_resource_path(map_name)
            try:
                margin = float(values.get("map_margin", "26"))
            except ValueError as exc:
                raise ValueError("Map margin must be a number in metres.") from exc
            if not math.isfinite(margin) or not 0 < margin <= 1000:
                raise ValueError("Map margin must be greater than 0 and at most 1000 metres.")
            command += ["--include-map", "--map-name", map_name, "--map-margin", str(margin)]
        if values.get("effects_enabled", "1") == "1" and values.get('simulate_effects', '0') == '1':
            probe = values.get('particle_probe', '').strip()
            dotnet = values.get('particle_dotnet', '').strip()
            from .particle_runtime import particle_command
            particle_command(probe, dotnet)
            command += ['--particle-probe', probe]
            try:
                history = int(values.get('particle_history_ticks', '0').strip() or '0')
            except ValueError as exc:
                raise ValueError('Particle history ticks must be a nonnegative whole number.') from exc
            if history < 0:
                raise ValueError('Particle history ticks must be a nonnegative whole number.')
            if history:
                command += ['--particle-history-ticks', str(history)]
            if Path(probe).suffix.lower() == '.dll':
                command += ['--dotnet', dotnet]
        if values.get("cloth_enabled", "1") == "1":
            command.append("--secondary-motion")
        if values.get("material_repairs", "0") == "1":
            command.append("--material-repairs")
    if values.get("effects_enabled", "1") != "1":
        command.append("--no-effects")
    if values.get('model_constraints', '0') == '1':
        command.append('--model-constraints')
    heroes = values.get("heroes", "").strip()
    if heroes:
        try:
            ids = [int(part.strip()) for part in heroes.split(",")]
        except ValueError as exc:
            raise ValueError("Hero IDs must be whole numbers separated by commas, such as 13,2.") from exc
        if any(hero_id <= 0 for hero_id in ids):
            raise ValueError("Hero IDs must be positive numbers.")
        command += ["--heroes", ",".join(str(hero_id) for hero_id in dict.fromkeys(ids))]
    if partial:
        command.append("--partial")
    if partial_cloth:
        command.append('--partial-cloth')
    shot = values.get('shot_hero', '').strip()
    if shot:
        try:
            hero = int(shot)
        except ValueError as exc:
            raise ValueError('Follow camera hero ID must be a positive whole number.') from exc
        if hero <= 0:
            raise ValueError('Follow camera hero ID must be a positive whole number.')
        if heroes and hero not in ids:
            raise ValueError('The follow camera hero must be selected for export.')
        command += ['--shot-hero', str(hero)]
    if overwrite:
        command.append("--overwrite")
    return command


class AGRApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"RenderLock {__version__} — Replay to Blender")
        self.root.geometry("920x820")
        self.root.minsize(700, 640)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.process_lock = threading.Lock()
        self.cancel_requested = threading.Event()
        self.termination_done = threading.Event()
        self.termination_done.set()
        self.busy = False
        self.close_when_done = False
        self._inspect_source = None
        self._roster_source = None
        self._roster_complete = False
        self._portrait_generation = 0
        self._portrait_loading_key = None
        self._portrait_loaded_key = None
        self._portrait_timer = None
        self.values = {key: tk.StringVar(value=value) for key, value in {
            "mode": "replay", "input": "", "assets": "", "blender": "",
            "output": "", "start": "", "end": "", "fps": "64", "heroes": "", "heroes_empty": "0",
            "asset_source": "installed", "game_install": "", "source2viewer": "", "asset_cache": "",
            "map_enabled": "0", "map_name": "dl_midtown", "map_margin": "26",
            "external_map_enabled": "0", "external_map": "",
            "source_lighting": "0", "world_region_probe": "", "lighting_dotnet": "",
            "source_hero_lighting": "0", "source_npr_lighting": "0", "probe_atlas_decoder": "", "cubemap_decoder": "",
            "character_shader": "", "character_only": "0",
            "character_look": "standard",
            "map_workflow_revision": MAP_WORKFLOW_REVISION,
            "effects_enabled": "1", "cloth_enabled": "1", "material_repairs": "0", "model_constraints": "0",
            "check_updates": "1",
            "simulate_effects": "0", "particle_probe": "", "particle_dotnet": "", "shot_hero": "", "particle_history_ticks": "0",
        }.items()}
        self.partial = tk.BooleanVar(value=False)
        self.partial_cloth = tk.BooleanVar(value=False)
        self.lighting_consent = tk.BooleanVar(value=False)
        self.overwrite = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self.mutable_widgets: list[ttk.Widget] = []
        self.discovery_note = ""
        self._load_settings()
        if not self.values["assets"].get().strip():
            self.values["assets"].set(discover_assets())
        if not self.values["blender"].get().strip():
            self.values["blender"].set(discover_blender())
        if not self.values["game_install"].get().strip():
            try:
                from .game_assets import discover_installations
                installations = discover_installations()
                if len(installations) == 1:
                    self.values["game_install"].set(str(installations[0]))
                elif len(installations) > 1:
                    self.discovery_note += "Several Deadlock installations were found. Choose the folder to use.\n"
            except (OSError, ValueError) as exc:
                self.discovery_note += f"Automatic game discovery was unavailable: {exc}. Choose the game folder manually.\n"
        if not self.values["source2viewer"].get().strip():
            self.values["source2viewer"].set(str(discover_source2viewer() or ""))
        if not self.values["asset_cache"].get().strip():
            self.values["asset_cache"].set(str(default_asset_cache()))
        probe, dotnet = discover_particle_runtime()
        for key, found in (('particle_probe', probe), ('particle_dotnet', dotnet)):
            if not self.values[key].get().strip():
                self.values[key].set(found)
        for key, found in discover_lighting_kit().items():
            if not self.values[key].get().strip():
                self.values[key].set(found)
        self._build_ui()
        from .update_ui import UpdateUI
        self.update_ui = UpdateUI(self, PROGRAM_ROOT)
        for key in ("input", "mode"):
            self.values[key].trace_add("write", self._input_changed)
        for key in ("game_install", "source2viewer", "asset_cache"):
            self.values[key].trace_add("write", self._portrait_settings_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)

    def _build_ui(self) -> None:
        from .gui_layout import build
        build(self)

    def _mode_changed(self) -> None:
        self.inspect_button.configure(text="Inspect replay" if self.values["mode"].get() == "replay"
                                      else "Inspect package")

    def _input_identity(self):
        """Bind an inspected roster to both the selected path and its file bytes' revision."""
        try:
            path = resolve_gui_path(self.values["input"].get())
            stat = path.stat()
            revision = (stat.st_size, stat.st_mtime_ns)
        except (OSError, RuntimeError, ValueError):
            path = self.values["input"].get().strip()
            revision = None
        return self.values["mode"].get(), os.path.normcase(str(path)), revision

    def _input_changed(self, *_args) -> None:
        self._roster_source = None
        self._roster_complete = False
        self.character_picker.clear(reset_selection=True)
        if hasattr(self, 'follow_picker'):
            self.follow_picker.clear(reset_selection=True)
        self.input_summary.set("Inspect to read this recording's information and characters.")

    def _asset_source_changed(self, event=None) -> None:
        installed = self.asset_source_label.get() == "Installed game"
        self.values["asset_source"].set("installed" if installed else "prepared")
        (self.installed_fields if installed else self.prepared_fields).grid()
        (self.prepared_fields if installed else self.installed_fields).grid_remove()
        from .gui_layout import refresh_asset_source_controls
        refresh_asset_source_controls(self)

    def _browse_input(self) -> None:
        is_replay = self.values["mode"].get() == "replay"
        path = filedialog.askopenfilename(parent=self.root, title="Choose replay" if is_replay else "Choose manifest",
                                         filetypes=[("Replay", "*.dem"), ("All files", "*")] if is_replay
                                         else [("JSON manifest", "*.json"), ("All files", "*")])
        if path:
            self.values["input"].set(path)
            if not self.values["output"].get():
                output = Path.home() / "Documents" / "RenderLock" / "Exports"
                self.values["output"].set(str(output / (Path(path).stem + "_agr.blend")))
            self._inspect()

    def _browse_assets(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Choose supported game asset folder")
        if path:
            self.values["assets"].set(path)

    def _browse_game(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Choose Deadlock folder (Steam → Manage → Browse local files)")
        if path:
            self.values["game_install"].set(path)

    def _browse_source2viewer(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Choose Source 2 Viewer CLI executable",
                                         filetypes=[("Source 2 Viewer CLI", "Source2Viewer-CLI.exe"), ("All files", "*")]
                                         if sys.platform == "win32" else [("All files", "*")])
        if path:
            self.values["source2viewer"].set(path)

    def _browse_cache(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Choose generated asset cache (outside the game folder)")
        if path:
            self.values["asset_cache"].set(path)

    def _browse_particle_probe(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title='Choose AGR particle simulator',
                                         filetypes=[('AGR particle simulator', '*.exe *.dll'), ('All files', '*')])
        if path:
            self.values['particle_probe'].set(path)

    def _browse_particle_dotnet(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title='Choose .NET executable',
                                         filetypes=[('All files', '*')])
        if path:
            self.values['particle_dotnet'].set(path)

    def _browse_world_region_probe(self) -> None:
        path = filedialog.askopenfilename(parent=self.root,title='Choose AGR regional exporter',
            filetypes=[('AGR regional exporter','WorldRegionProbe.dll'),('All files','*')])
        if path:
            self.values['world_region_probe'].set(path)

    def _browse_lighting_dotnet(self) -> None:
        path = filedialog.askopenfilename(parent=self.root,title='Choose .NET executable',
            filetypes=[('All files','*')])
        if path:
            self.values['lighting_dotnet'].set(path)

    def _browse_hero_decoder(self,key,filename) -> None:
        path=filedialog.askopenfilename(parent=self.root,title='Choose '+filename,
            filetypes=[('AGR lighting decoder',filename),('All files','*')])
        if path:self.values[key].set(path)

    def _browse_character_shader(self) -> None:
        path=filedialog.askopenfilename(parent=self.root,title='Choose Elagrimm BLENDER-Deadlock-Shader.blend',
            filetypes=[('Blender shader library','*.blend'),('All files','*')])
        if path:self.values['character_shader'].set(path)

    def _browse_external_map(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title='Choose downloaded RenderLockMap.blend',
            filetypes=[('Blender map library', '*.blend'), ('All files', '*')])
        if path:
            self.values['external_map'].set(path)

    def _open_map_link(self, optimizer=False) -> None:
        import webbrowser
        from .external_map import MAP_DOWNLOAD_URL, MAP_OPTIMIZER_URL
        url = MAP_OPTIMIZER_URL if optimizer else MAP_DOWNLOAD_URL
        try:
            if webbrowser.open(url):
                return
        except (webbrowser.Error, OSError):
            pass
        self._append_log('Open this map workflow link:\n' + url + '\n')

    def _open_character_shader_download(self) -> None:
        import webbrowser
        from .blender_character_shader import LIBRARY_DOWNLOAD_URL
        try:
            if webbrowser.open(LIBRARY_DOWNLOAD_URL):
                return
        except (webbrowser.Error, OSError):
            pass
        self._append_log('Open this link to get the supported Elagrimm shader:\n'
                         + LIBRARY_DOWNLOAD_URL + '\n')

    def _browse_blender(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Choose Blender executable",
                                         filetypes=[("Blender executable", "blender.exe"), ("All files", "*")]
                                         if sys.platform == "win32" else [("All files", "*")])
        if path:
            self.values["blender"].set(path)

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.root, title="Choose Blender output",
                                           defaultextension=".blend", confirmoverwrite=False,
                                           filetypes=[("Blender scene", "*.blend")])
        if path:
            self.values["output"].set(path)

    def _current_values(self) -> dict[str, str]:
        return {key: variable.get() for key, variable in self.values.items()}

    def _convert(self) -> None:
        if self.busy:
            return
        try:
            if self._roster_source is not None and self._roster_source != self._input_identity():
                # Keep the explicit filter and stale identity until inspection
                # succeeds; a second Export click must not turn a subset into All.
                self.character_picker.clear(reset_selection=False)
                if hasattr(self, 'follow_picker'):
                    self.follow_picker.clear(reset_selection=False)
                raise ValueError("The input file changed since inspection. Inspect it again before exporting.")
            shot = self.values["shot_hero"].get().strip()
            try:
                shot_id = int(shot)
            except ValueError:
                shot_id = None  # The command builder explains invalid camera input.
            if self._roster_complete and shot_id is not None and shot_id > 0 and shot_id not in self.character_picker.hero_ids:
                raise ValueError("The follow camera hero is not listed in this recording. Choose a listed hero or Overview.")
            command = build_convert_command(self._current_values(), partial=self.partial.get(),
                                            overwrite=self.overwrite.get(),
                                            partial_cloth=getattr(self,'partial_cloth',None) is not None and self.partial_cloth.get(),
                                            lighting_consent=getattr(self,'lighting_consent',None) is not None and self.lighting_consent.get())
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Check conversion settings", str(exc), parent=self.root)
            return
        self._save_settings()
        report_path = resolve_gui_path(self.values["output"].get()).with_suffix(".agr-report.json")
        self._start(command, "convert", report_path)

    def _inspect(self) -> None:
        if self.busy:
            return
        try:
            path = resolve_gui_path(self.values["input"].get())
            if not path.is_file():
                raise ValueError("Choose an existing replay or package first.")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Choose an input", str(exc), parent=self.root)
            return
        if self.values["mode"].get() == "segment":
            # Inspect in a cancellable child; large sample arrays must not block Tk.
            script = (
                "import json,sys\n"
                "from pathlib import Path\n"
                "from agr.scene_package import read_scene\n"
                "from agr.model_tracks import logical_entities\n"
                "data=read_scene(Path(sys.argv[1]))\n"
                "summary={key:data.get(key) for key in ('schema_version','start_tick','end_tick','tick_rate')}\n"
                "summary['roster']=[{'id':e['id'],'hero_id':e.get('hero_id'),'hero_name':e.get('hero_name'),"
                "'samples':len(e['samples'])} "
                "for e in logical_entities(data['entities']).values()]\n"
                "summary['roster_source']='saved_package_tracks'\n"
                "print(json.dumps(summary,indent=2))\n"
            )
            self._save_settings()
            self._start([sys.executable, "-c", script, str(path)], "inspect")
            return
        self._save_settings()
        self._start([sys.executable, "-m", "agr", "inspect", "--demo", str(path)], "inspect")

    def _status_tint(self, kind: str) -> None:
        dot = getattr(self, "status_dot", None)
        if dot is None:
            return
        color = {"busy": gui_theme.DOT_BUSY, "error": gui_theme.DOT_ERROR}.get(kind, gui_theme.DOT_READY)
        dot.configure(foreground=color)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget in self.mutable_widgets:
            widget.configure(state="disabled" if busy else "normal")
        self.asset_source_combo.configure(state="disabled" if busy else "readonly")
        self.character_picker.set_busy(busy)
        if hasattr(self, 'follow_picker'):
            self.follow_picker.set_busy(busy)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        from .gui_layout import refresh_asset_source_controls
        refresh_asset_source_controls(self)
        self._status_tint("busy" if busy else "ready")
        if busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def _start(self, command: list[str], operation: str, report_path: Path | None = None) -> None:
        if operation == "inspect":
            self._inspect_source = self._input_identity()
        else:
            self._conversion_command = list(command)
            self._conversion_identity = self._input_identity()
        self.cancel_requested.clear()
        self.termination_done.set()
        self._set_busy(True)
        self.status.set("Inspecting input…" if operation == "inspect" else "Converting…")
        self._append_log("\n" + ("Inspecting input" if operation == "inspect" else "Starting conversion") + "…\n")
        threading.Thread(target=self._run, args=(command, operation, report_path), daemon=True).start()

    def _run(self, command: list[str], operation: str, report_path: Path | None = None) -> None:
        output: list[str] = []
        conversion_log = ""
        code = -1
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            kwargs: dict[str, object] = {"cwd": str(PROGRAM_ROOT), "env": env, "stdout": subprocess.PIPE,
                                         "stderr": subprocess.STDOUT, "text": True,
                                         "encoding": "utf-8", "errors": "replace", "bufsize": 1}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            with self.process_lock:
                if self.cancel_requested.is_set():
                    return
                self.process = subprocess.Popen(command, **kwargs)
                process = self.process
            assert process.stdout is not None
            for line in process.stdout:
                self.events.put(("log", line))
                if operation == "inspect":
                    output.append(line)
                else:
                    conversion_log = (conversion_log + line)[-65536:]
            code = process.wait()
            if operation == 'convert' and code != 0:
                output.append(conversion_log)
            if operation == "convert" and code == 0 and report_path is not None:
                # Read the result off the Tk thread. A successful export can
                # still have a failed map stage or explicitly partial effects.
                try:
                    from .coverage_diagnostics import scene_completion_message
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    output.append(scene_completion_message(report))
                except (OSError, ValueError, TypeError, AttributeError):
                    self.events.put(("log", "Could not read the completion summary; see the coverage report.\n"))
        except Exception as exc:
            detail = f"Could not run the program: {exc}\n"
            self.events.put(("log", detail))
            if operation == 'convert':
                output.append(conversion_log + detail)
        finally:
            if self.cancel_requested.is_set():
                self.termination_done.wait(timeout=20)
            with self.process_lock:
                self.process = None
            self.events.put(("finished", (operation, code, "".join(output))))

    def _cancel(self) -> None:
        if not self.busy or self.cancel_requested.is_set():
            return
        self.termination_done.clear()
        self.cancel_requested.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelling…")
        self._append_log("Cancellation requested; stopping the conversion and its child processes.\n")
        threading.Thread(target=self._terminate_tree, daemon=True).start()

    def _terminate_tree(self) -> None:
        try:
            with self.process_lock:
                process = self.process
            if process is None:
                return
            if sys.platform == "win32":
                result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                        capture_output=True, text=True, timeout=15,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode != 0 and process.poll() is None:
                    self.events.put(("log", "Process-tree cancellation failed: " + result.stderr + "\n"))
                    process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                # A descendant may outlive the parent, so signal the original group too.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except ProcessLookupError:
            pass
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.events.put(("log", f"Could not stop every process automatically: {exc}\n"))
        finally:
            self.termination_done.set()

    def _apply_metadata(self, metadata: dict[str, object]) -> None:
        if hasattr(self, "character_picker"):
            self.character_picker.load(metadata)
            if hasattr(self, 'follow_picker'):
                self.follow_picker.load(metadata)
            self._roster_source = self._input_identity()
            self._roster_complete = self.character_picker.has_roster and not metadata.get("roster_warning")
            self._request_nurse_portrait()
            for page in getattr(self, "page_layouts", {}).values():
                page._schedule()
        first_tick = metadata.get("start_tick", metadata.get("first_tick", 0))
        last_tick = metadata.get("end_tick", metadata.get("last_tick", metadata.get("total_ticks")))
        if (isinstance(first_tick, (int, float)) and math.isfinite(first_tick)
                and isinstance(last_tick, (int, float)) and math.isfinite(last_tick)
                and 0 <= first_tick <= last_tick):
            first, last = int(first_tick), int(last_tick)
            try:
                start = int(self.values["start"].get())
            except ValueError:
                start = first
            if not first <= start <= last:
                start = first
            self.values["start"].set(str(start))
            try:
                end = int(self.values["end"].get())
            except ValueError:
                end = None
            if end is None or not start <= end <= last:
                # Replace a previous recording's invalid range with a preview.
                self.values["end"].set(str(min(last, start + 120)))
        if hasattr(self, "input_summary"):
            ticks = f"Ticks {first_tick}–{last_tick}" if last_tick is not None else "Input inspected"
            roster = metadata.get("roster")
            heroes = metadata.get("heroes")
            detail = (f" · {len(heroes)} characters" if isinstance(heroes, list) else
                      f" · {len(roster)} character tracks" if isinstance(roster, list) else "")
            self.input_summary.set(ticks + detail + " · Review the preview range before export.")

    def _portrait_settings_changed(self, *_args) -> None:
        """Discard a stale installed portrait when its game or converter changes."""
        from .gui_characters import set_installed_portrait
        self._portrait_generation += 1
        self._portrait_loaded_key = self._portrait_loading_key = None
        set_installed_portrait(87, None)
        self.character_picker.refresh_portraits()
        if self._portrait_timer is not None:
            self.root.after_cancel(self._portrait_timer)
        self._portrait_timer = self.root.after(500, self._request_nurse_portrait)

    def _request_nurse_portrait(self) -> None:
        """Load missing Nurse artwork asynchronously; the UI owns all Tk images."""
        timer = getattr(self, '_portrait_timer', None)
        if timer is not None:
            self.root.after_cancel(timer)
        self._portrait_timer = None
        if 87 not in self.character_picker.hero_ids:
            return
        key = (self.values['game_install'].get().strip(), self.values['source2viewer'].get().strip(),
               self.values['asset_cache'].get().strip() or str(default_asset_cache()))
        if not all(key) or key in (self._portrait_loaded_key, self._portrait_loading_key):
            return
        self._portrait_generation += 1
        generation = self._portrait_generation
        self._portrait_loading_key = key

        def worker():
            try:
                from .gui_portraits import load_nurse_portrait
                portrait, error = load_nurse_portrait(*key), None
            except Exception as exc:
                portrait, error = None, f'{type(exc).__name__}: {exc}'
            self.events.put(('nurse_portrait', (generation, key, portrait, error)))
        threading.Thread(target=worker, daemon=True).start()

    def _finish_nurse_portrait(self, payload) -> None:
        generation, key, portrait, error = payload
        if generation != self._portrait_generation:
            return
        self._portrait_loading_key = None
        if error:
            self._append_log('Nurse Harrow portrait unavailable; showing initials: ' + error + '\n')
            return
        from .gui_characters import set_installed_portrait
        set_installed_portrait(87, portrait)
        self._portrait_loaded_key = key
        self.character_picker.refresh_portraits()

    def _poll(self) -> None:
        try:
            # Limit each pass so a noisy parser cannot starve the Tk event loop.
            for _ in range(300):
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == 'nurse_portrait':
                    self._finish_nurse_portrait(payload)
                elif kind == "finished":
                    operation, code, output = payload
                    self._set_busy(False)
                    if self.cancel_requested.is_set():
                        self.status.set("Cancelled. Review the output folder before reusing the same filename.")
                    elif code != 0:
                        from .gui_layout import set_log_visible
                        set_log_visible(self, True)
                        self.status.set(f"{operation.capitalize()} failed (exit {code}); see the log for details.")
                        self._status_tint("error")
                        if operation == 'convert' and not self.close_when_done:
                            self._show_conversion_failure(output)
                    elif operation == "inspect":
                        try:
                            metadata = json.loads(output)
                            if not isinstance(metadata, dict):
                                raise ValueError("Inspection did not return metadata.")
                            current = self._input_identity()
                            if self._inspect_source != current:
                                self.character_picker.clear(reset_selection=False)
                                if hasattr(self, 'follow_picker'):
                                    self.follow_picker.clear(reset_selection=False)
                                if self._inspect_source[:2] == current[:2]:
                                    self._roster_source = self._inspect_source
                                self.status.set("Input changed; inspect the selected recording again.")
                            else:
                                self._apply_metadata(metadata)
                                self.status.set("Input inspected; choose characters and review the tick range before exporting.")
                        except ValueError:
                            self.status.set("Inspection finished. Metadata could not be parsed; see the log above.")
                    else:
                        self.status.set(output or "Conversion finished. See the log for the scene path and support report.")
                    if self.close_when_done:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_conversion_failure(self, output: str) -> None:
        from .export_recovery import recovery_choices, recovery_summary
        from .gui_export_recovery import failure_details, show_failure
        command = getattr(self, '_conversion_command', [])
        failure, work, bundle = failure_details(output)
        guidance = ['Export recovery: ' + recovery_summary(command, failure, output)]
        for suggestion in recovery_choices(command, failure, output):
            guidance.extend((suggestion.title, suggestion.description))
        if work is not None:
            guidance.append('Retained diagnostic folder: ' + str(work))
        if bundle is not None:
            guidance.append('Diagnostic ZIP: ' + str(bundle))
        self._append_log('\n'.join(guidance) + '\n')
        try:
            choice = show_failure(self, command, output)
        except Exception as exc:
            self._append_log(f'Could not open export recovery: {type(exc).__name__}: {exc}\n'
                             'Review the recovery guidance above and adjust settings before pressing Export to retry.\n')
            return
        if choice is not None:
            self.root.after(0, lambda: self._retry_conversion(choice))

    def _retry_conversion(self, choice) -> None:
        """Apply only the explicitly selected compromise to this exact job."""
        if self.busy:
            return
        try:
            from .cli import parser
            cli_parser = parser()

            def same_command(left, right):
                return (left[:3] == right[:3] and
                        vars(cli_parser.parse_args(left[3:])) == vars(cli_parser.parse_args(right[3:])))

            current = build_convert_command(self._current_values(), partial=self.partial.get(),
                overwrite=self.overwrite.get(), partial_cloth=self.partial_cloth.get(),
                lighting_consent=getattr(self,'lighting_consent',None) is not None and self.lighting_consent.get())
            if (not same_command(current, self._conversion_command) or
                    self._input_identity() != self._conversion_identity):
                raise ValueError('The input or settings changed. Review them and press Export to start a new job.')
            command = list(choice.command)
            accepted_lighting = getattr(self, 'lighting_consent', None) is not None and self.lighting_consent.get()
            for key, value in choice.values_overrides:
                if key in self.values:
                    self.values[key].set(value)
            if 'character_look' in self.values:
                # Recovery owns the exact flag changes; a preset must not add
                # its dependencies back after the user explicitly omitted them.
                previous = cli_parser.parse_args(current[3:])
                selected = cli_parser.parse_args(command[3:])
                lighting_keys = ('source_lighting', 'source_hero_lighting', 'source_npr_lighting',
                                 'include_map', 'character_shader')
                if any(getattr(previous, key) != getattr(selected, key) for key in lighting_keys):
                    for key, enabled in (('source_lighting', selected.source_lighting),
                                         ('source_hero_lighting', selected.source_hero_lighting),
                                         ('source_npr_lighting', selected.source_npr_lighting),
                                         ('map_enabled', selected.include_map)):
                        self.values[key].set('1' if enabled else '0')
                    if not any((selected.source_lighting, selected.source_hero_lighting, selected.source_npr_lighting)):
                        from .release_profile import character_beta
                        # A legacy recovery may deliberately retain map preparation.
                        # Standard prunes it for character-only jobs, so retain the
                        # exact selected recovery as Custom in that case.
                        look = 'custom' if selected.include_map and (selected.character_only or character_beta()) else 'standard'
                    elif selected.source_lighting and selected.source_hero_lighting and not selected.source_npr_lighting:
                        look = 'source'
                    elif selected.character_shader:
                        look = 'elagrimm_source'
                    else:
                        look = 'custom'
                    if selected.character_shader or look == 'custom':
                        self.values['character_shader'].set(str(selected.character_shader or ''))
                    self.values['character_look'].set(look)
                if hasattr(self, 'lighting_consent'):
                    self.lighting_consent.set(accepted_lighting and '--partial-lighting' in command)
            self.partial.set('--partial' in command)
            self.partial_cloth.set('--partial-cloth' in command)
            rebuilt = build_convert_command(self._current_values(), partial=self.partial.get(),
                overwrite=self.overwrite.get(), partial_cloth=self.partial_cloth.get(),
                lighting_consent=getattr(self,'lighting_consent',None) is not None and self.lighting_consent.get())
            if not same_command(rebuilt, command):
                raise ValueError('Review the suggested settings and press Export to start the next job.')
        except (OSError, RuntimeError, ValueError) as exc:
            self._append_log(str(exc) + '\n')
            self.status.set(str(exc))
            return
        self._append_log('Selected recovery: ' + choice.title + '\n' + choice.description + '\n')
        self._save_settings()
        report = resolve_gui_path(self.values['output'].get()).with_suffix('.agr-report.json')
        self._start(command, 'convert', report)

    def _append_log(self, text: str) -> None:
        # The Windows launcher captures stdout, including conversion diagnostics.
        print(text, end="" if text.endswith("\n") else "\n", flush=True)
        self.log.configure(state="normal")
        self.log.insert("end", text)
        # Bound display memory; the CLI's own report remains the source of record.
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 12000:
            self.log.delete("1.0", f"{lines - 10000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_output(self) -> None:
        value = self.values["output"].get().strip()
        if not value:
            messagebox.showinfo("Choose an output", "Choose an output filename first.", parent=self.root)
            return
        try:
            path = resolve_gui_path(value).parent
            if not path.is_dir():
                messagebox.showinfo("Folder unavailable", f"This output folder does not exist yet:\n{path}", parent=self.root)
                return
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self.root)

    def _load_settings(self) -> None:
        try:
            saved = json.loads(settings_path().read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                return
            saved = normalize_gui_paths(migrate_scene_settings(saved))
            from .tool_paths import reconcile_saved_tools
            saved, notices = reconcile_saved_tools(saved, PROGRAM_ROOT)
            if notices:
                self.discovery_note = getattr(self, "discovery_note", "") + "\n".join(notices) + "\n"
            for key, variable in self.values.items():
                if isinstance(saved.get(key), str):
                    variable.set(saved[key])
            if self.values["mode"].get() not in {"replay", "segment"}:
                self.values["mode"].set("replay")
            self.values["asset_source"].set(asset_source_mode(saved))
        except (OSError, RuntimeError, ValueError):
            pass

    def _save_settings(self) -> None:
        try:
            path = settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            saved = normalize_gui_paths(self._current_values())
            saved["map_workflow_revision"] = MAP_WORKFLOW_REVISION
            from .tool_paths import PROGRAM_ROOT_SETTING
            saved[PROGRAM_ROOT_SETTING] = str(PROGRAM_ROOT.resolve())
            temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
            temporary.replace(path)
        except (OSError, RuntimeError, ValueError) as exc:
            self._append_log(f"Could not remember settings: {exc}\n")

    def _close(self) -> None:
        if self.busy:
            if not messagebox.askyesno("Stop current job?", "Cancel the running job and close RenderLock?", parent=self.root):
                return
            self.close_when_done = True
            self._cancel()
        else:
            if hasattr(self, "update_ui"):
                self.update_ui.closed = True
            self._save_settings()
            self.root.destroy()


def main() -> int:
    from .console import configure_stdio
    configure_stdio()
    gui_theme.enable_dpi_awareness()  # before the first window, so text is sharp on scaled displays
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"The desktop window could not start: {exc}\nUse python -m agr --help for the command line.",
              file=sys.stderr)
        return 1
    app = AGRApp(root)
    def report_callback_exception(exc_type, exc, tb):
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        print(detail, file=sys.stderr, flush=True)
        app._append_log("AGR window error:\n" + detail)
    root.report_callback_exception = report_callback_exception
    print("AGR window opened", flush=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
