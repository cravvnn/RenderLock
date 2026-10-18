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
from tkinter import filedialog, messagebox, ttk
from . import __version__, gui_theme
from .cli import default_asset_cache, discover_source2viewer


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
    return migrated


def build_convert_command(values: dict[str, str], *, partial: bool = False,
                          overwrite: bool = False) -> list[str]:
    """Validate editable fields and build argv without shell interpretation."""
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
    if Path(output).exists() and not overwrite:
        raise ValueError("That output already exists. Choose a new name or explicitly enable overwrite.")
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
    if values.get('source_lighting','0') == '1':
        if asset_mode != 'installed' or values.get('map_enabled','0') != '1':
            raise ValueError('Partial Source lighting requires installed assets and Convert map region.')
        from .source_region import regional_command
        lighting_probe=values.get('world_region_probe','').strip()
        lighting_dotnet=values.get('lighting_dotnet','').strip()
        regional_command(lighting_probe,lighting_dotnet)
        command += ['--source-lighting','--world-region-probe',lighting_probe,'--lighting-dotnet',lighting_dotnet]
    if values.get('source_npr_lighting','0') == '1':
        if not partial or values.get('source_hero_lighting','0')!='1':
            raise ValueError('NPR indirect requires partial conversion and Hero Source lighting.')
        if not values.get('shot_hero','').strip():
            raise ValueError('NPR indirect requires a Follow camera hero ID for perspective framing.')
        command += ['--source-npr-lighting']
    character_shader=values.get('character_shader','').strip()
    if character_shader:
        if not partial or values.get('source_npr_lighting','0')!='1':
            raise ValueError('Character shader requires partial conversion and NPR indirect lighting.')
        from .blender_character_shader import validate_character_shader_library
        command += ['--character-shader',str(validate_character_shader_library(character_shader))]
    if values.get('character_only','0')=='1':
        command += ['--character-only']
    if values.get('source_hero_lighting','0') == '1':
        if values.get('source_lighting','0') != '1':raise ValueError('Hero lighting requires Partial static diffuse lighting.')
        from .source_hero_lighting import validate_hero_decoder_paths
        atlas=values.get('probe_atlas_decoder','').strip();cube=values.get('cubemap_decoder','').strip()
        validate_hero_decoder_paths(atlas,cube,values.get('lighting_dotnet','').strip())
        command += ['--source-hero-lighting','--probe-atlas-decoder',atlas,'--cubemap-decoder',cube]
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
        if values.get("effects_enabled", "1") != "1":
            command.append("--no-effects")
        elif values.get('simulate_effects', '0') == '1':
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
    shot = values.get('shot_hero', '').strip()
    if shot:
        try:
            hero = int(shot)
        except ValueError as exc:
            raise ValueError('Follow camera hero ID must be a positive whole number.') from exc
        if hero <= 0:
            raise ValueError('Follow camera hero ID must be a positive whole number.')
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
        self.values = {key: tk.StringVar(value=value) for key, value in {
            "mode": "replay", "input": "", "assets": "", "blender": "",
            "output": "", "start": "", "end": "", "fps": "64", "heroes": "",
            "asset_source": "installed", "game_install": "", "source2viewer": "", "asset_cache": "",
            "map_enabled": "0", "map_name": "dl_midtown", "map_margin": "26",
            "source_lighting": "0", "world_region_probe": "", "lighting_dotnet": "",
            "source_hero_lighting": "0", "source_npr_lighting": "0", "probe_atlas_decoder": "", "cubemap_decoder": "",
            "character_shader": "", "character_only": "0",
            "map_workflow_revision": MAP_WORKFLOW_REVISION,
            "effects_enabled": "1", "cloth_enabled": "1", "material_repairs": "0", "model_constraints": "0",
            "simulate_effects": "0", "particle_probe": "", "particle_dotnet": "", "shot_hero": "", "particle_history_ticks": "0",
        }.items()}
        self.partial = tk.BooleanVar(value=False)
        self.overwrite = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self.mutable_widgets: list[ttk.Widget] = []
        self._load_settings()
        if not self.values["assets"].get().strip():
            self.values["assets"].set(discover_assets())
        if not self.values["blender"].get().strip():
            self.values["blender"].set(discover_blender())
        self.discovery_note = ""
        if not self.values["game_install"].get().strip():
            try:
                from .game_assets import discover_installations
                installations = discover_installations()
                if len(installations) == 1:
                    self.values["game_install"].set(str(installations[0]))
                elif len(installations) > 1:
                    self.discovery_note = "Several Deadlock installations were found. Choose the folder to use.\n"
            except (OSError, ValueError) as exc:
                self.discovery_note = f"Automatic game discovery was unavailable: {exc}. Choose the game folder manually.\n"
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
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)

    def _build_ui(self) -> None:
        from .gui_layout import build
        build(self)

    def _mode_changed(self) -> None:
        self.inspect_button.configure(text="Inspect replay" if self.values["mode"].get() == "replay"
                                      else "Inspect package")

    def _asset_source_changed(self, event=None) -> None:
        installed = self.asset_source_label.get() == "Installed game"
        self.values["asset_source"].set("installed" if installed else "prepared")
        (self.installed_fields if installed else self.prepared_fields).grid()
        (self.prepared_fields if installed else self.installed_fields).grid_remove()

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
        path=filedialog.askopenfilename(parent=self.root,title='Choose character shader library',
            filetypes=[('Blender shader library','*.blend'),('All files','*')])
        if path:self.values['character_shader'].set(path)

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
            command = build_convert_command(self._current_values(), partial=self.partial.get(),
                                            overwrite=self.overwrite.get())
        except ValueError as exc:
            messagebox.showerror("Check conversion settings", str(exc), parent=self.root)
            return
        self._save_settings()
        report_path = Path(self.values["output"].get().strip()).expanduser().with_suffix(".agr-report.json")
        if not report_path.is_absolute():
            report_path = PROGRAM_ROOT / report_path
        self._start(command, "convert", report_path)

    def _inspect(self) -> None:
        if self.busy:
            return
        path = Path(self.values["input"].get().strip())
        if not path.is_file():
            messagebox.showerror("Choose an input", "Choose an existing replay or package first.", parent=self.root)
            return
        if self.values["mode"].get() == "segment":
            # Inspect in a cancellable child; large sample arrays must not block Tk.
            script = (
                "import json,sys\n"
                "from pathlib import Path\n"
                "from agr.scene_package import read_scene\n"
                "data=read_scene(Path(sys.argv[1]))\n"
                "summary={key:data.get(key) for key in ('schema_version','start_tick','end_tick','tick_rate')}\n"
                "summary['roster']=[{'id':e['id'],'hero_id':e.get('hero_id'),'samples':len(e['samples'])} "
                "for e in data['entities']]\n"
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
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self._status_tint("busy" if busy else "ready")
        if busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def _start(self, command: list[str], operation: str, report_path: Path | None = None) -> None:
        self.cancel_requested.clear()
        self.termination_done.set()
        self._set_busy(True)
        self.status.set("Inspecting input…" if operation == "inspect" else "Converting…")
        self._append_log("\n" + ("Inspecting input" if operation == "inspect" else "Starting conversion") + "…\n")
        threading.Thread(target=self._run, args=(command, operation, report_path), daemon=True).start()

    def _run(self, command: list[str], operation: str, report_path: Path | None = None) -> None:
        output: list[str] = []
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
            code = process.wait()
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
            self.events.put(("log", f"Could not run the program: {exc}\n"))
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
        first_tick = metadata.get("start_tick", metadata.get("first_tick", 0))
        last_tick = metadata.get("end_tick", metadata.get("last_tick", metadata.get("total_ticks")))
        if not self.values["start"].get().strip() and isinstance(first_tick, (int, float)):
            self.values["start"].set(str(int(first_tick)))
        if not self.values["end"].get().strip() and isinstance(last_tick, (int, float)):
            # Inspect suggests a preview, never a whole-match bake.
            try:
                start = int(self.values["start"].get())
            except ValueError:
                start = int(first_tick or 0)
            self.values["end"].set(str(min(int(last_tick), start + 120)))
        if hasattr(self, "input_summary"):
            ticks = f"Ticks {first_tick}–{last_tick}" if last_tick is not None else "Input inspected"
            roster = metadata.get("roster")
            detail = f" · {len(roster)} character tracks" if isinstance(roster, list) else ""
            self.input_summary.set(ticks + detail + " · Review the preview range before export.")

    def _poll(self) -> None:
        try:
            # Limit each pass so a noisy parser cannot starve the Tk event loop.
            for _ in range(300):
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
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
                    elif operation == "inspect":
                        try:
                            metadata = json.loads(output)
                            if isinstance(metadata, dict):
                                self._apply_metadata(metadata)
                            self.status.set("Input inspected; review the tick range before converting.")
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
        path = Path(value).expanduser().resolve().parent
        if not path.is_dir():
            messagebox.showinfo("Folder unavailable", f"This output folder does not exist yet:\n{path}", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self.root)

    def _load_settings(self) -> None:
        try:
            saved = json.loads(settings_path().read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                return
            saved = migrate_scene_settings(saved)
            for key, variable in self.values.items():
                if isinstance(saved.get(key), str):
                    variable.set(saved[key])
            if self.values["mode"].get() not in {"replay", "segment"}:
                self.values["mode"].set("replay")
            self.values["asset_source"].set(asset_source_mode(saved))
        except (OSError, ValueError):
            pass

    def _save_settings(self) -> None:
        try:
            path = settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            saved = self._current_values()
            saved["map_workflow_revision"] = MAP_WORKFLOW_REVISION
            temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            self._append_log(f"Could not remember settings: {exc}\n")

    def _close(self) -> None:
        if self.busy:
            if not messagebox.askyesno("Stop current job?", "Cancel the running job and close AGR?", parent=self.root):
                return
            self.close_when_done = True
            self._cancel()
        else:
            self._save_settings()
            self.root.destroy()


def main() -> int:
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
