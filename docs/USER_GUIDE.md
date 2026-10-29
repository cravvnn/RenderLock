# Using RenderLock

[All guides](README.md) · [Troubleshooting](TROUBLESHOOTING.md)

RenderLock converts local replay and installed game files into a Blender scene.
It does not launch or attach to Deadlock. The Windows portable ZIP includes the
Python runtime, converter and helper tools; install Blender separately.

## Install and check Setup

1. Download the Windows x64 ZIP from [Releases](https://github.com/cravvnn/RenderLock/releases/latest)
   and extract the entire folder into a writable location. Do not launch from
   inside the ZIP or move only the executable.
2. Open **RenderLock.exe**. **Launch_RenderLock.cmd** is the fallback launcher.
3. In **Setup**, choose Blender and your Deadlock installation. Steam's **Manage >
   Browse local files** opens the game folder. Keep **Installed game** selected for
   normal exports. **Prepared assets** is for an already compatible custom pack.
4. Keep **Generated asset cache** outside the game folder. Portable tool locations
   are discovered automatically; choose external overrides only when needed.

Blender 5.2.1 LTS is the tested installation. RenderLock probes required Blender
capabilities rather than requiring that exact patch version. The downloaded-map
library needs Blender 5.2 or newer. A successful capability probe does not qualify
every cloth solver, material or render engine behavior.

Installed exports use base Citadel/Core assets and an explicitly selected map.
Custom addon/mod mounts are excluded and logged, while installed mods and game
configuration are left unchanged. Files modified directly inside base archives
are not certified by this path policy; missing/corrupt required assets still fail.

## Disable UI animations

In **Setup > Accessibility**, check **Disable UI animations** to keep the
background, buttons, portraits and updater paper still. Moving progress lines
become static busy indicators; status messages continue to update. The choice
applies immediately, including during a job, and is remembered next time.

Uncheck it to allow motion again. Windows **Animation effects** being off or
`D2B_UI_MOTION=0` still keeps motion disabled. This preference controls the
RenderLock interface; it does not change exported character or particle animation.

## First preview

1. In **Export**, browse to the `.dem`. If typing a path, select **Inspect replay**.
2. Choose characters with the named checklist, **All** or **None**. Each choice
   includes that character's matching player/model tracks in the selected range.
   **Advanced > Hero IDs** is the fallback when the roster cannot be read.
3. Start with a short tick range and **Standard materials** in **Lighting lab**.
   Turn off **Include supported particle effects** and leave cloth/map off for
   this first animation check. A few seconds is useful for a preview, although asset preparation can still
   take time. Ticks are replay ticks, not seconds; ranges are inclusive.
4. Choose **Overview (no follow)** or a character in **Follow camera**. The follow
   target must also be included in the export selection.
5. Choose a new `.blend` output, keep broad partial export off, and export.
6. Read the completion message and report before opening the scene. Warnings can
   mean missing effects or approximate optional features, even after a saved export.

Export FPS controls the scene's output clock; it does not create missing source
animation. A different range, FPS or body animation requires rebuilding dependent
cloth and camera/effect work. An older replay can be incompatible with current
installed models even when the hero is selectable.

## Add optional features

| Page | Use it for |
| --- | --- |
| **Export** | Replay, characters, range, follow camera and effect inclusion |
| **Setup** | Blender, installed/prepared asset source, converter and cache |
| **Advanced** | Cloth, model bone rules, material repairs, particle simulation and output policy |
| **Map** | Download/include the separate map and open the external Map Optimizer link |
| **Lighting lab** | Standard, Source, Elagrimm + Source or Custom; lighting-only consent |
| **Updates** | Published Windows app updates and release notes |

Add optional features one at a time after the preview works. Use the
[lighting guide](LIGHTING.md), [map guide](../MAP_WORKFLOW.md) and
[cloth guide](../CLOTH_WORKFLOW.md) for their prerequisites and limits.

**Model bone rules (experimental)** applies supported authored aim/follow/twist
rules on top of recorded poses. It is separate from cloth and physics. Unsupported
rules, including relevant game-disabled rules, remain visible in the report.

**Include supported particle effects** controls whether effects are requested.
**Advanced > Particle simulation** selects recorded-event simulation and its
helper. A complete visible ability can contain unsupported children, renderers,
attachments or history. A recognized renderer or an available helper does not
establish complete ability support. **Earlier history (ticks)** may help only
where the required recorded history and provider semantics are supported; it is
not a general workaround for an unsupported effect. See the report.

## Output files and saved packages

| Output | Purpose |
| --- | --- |
| `shot.blend` | The editable Blender scene |
| `shot.agrscene/` | Reusable recorded scene data, including `manifest.json` and evaluated pose/skeleton files |
| `shot.agr-report.json` | Coverage, warnings, selected options, compatibility evidence and timings |

Character textures are normally packed into the `.blend`. The separately appended
map uses external textures until you relink/pack them. Elagrimm groups/materials
are embedded in the final scene; a separate shader file beside the output is not
expected. Select a character mesh to inspect materials, not the replay-root Empty.
The in-scene `AGR_README` also describes applied features and limitations.

Keep the `.agrscene` directory and report with the scene. A saved package is not a
complete game-asset backup: it does not embed character models, the downloaded map
library or all derived lighting/effect inputs. Rebuilding can still require
compatible installed assets and the selected external map/shader library. Select
**Saved package** as input and its `manifest.json` to reuse recorded scene data.

Compatible character transformations have separate models/rigs under one player
root. A reset tick can lack a recorded pose; strict export stops at that tick.
Explicit broad partial export can omit/hide an unavailable sample and report it.
Model-switch attachment/effect providers can remain unsupported independently.

## Cache and saved settings

Reuse the same **Generated asset cache** for later ranges. Matching, verified
models/textures and prepared inputs can be reused. A changed asset, converter or
helper can require a new entry; material repairs are cached separately. Do not
edit cache files in place or delete the entire cache as a routine workaround.

A warm cache can reduce preparation time, but full-map loading, pose baking and
other Blender work remain. Timings depend on the selected heroes, features and
range. Keep early previews small before a long export.

Preferences are under `%APPDATA%\RenderLock`; normal logs are under
`%LOCALAPPDATA%\RenderLock\logs`. The default generated-asset cache is under
`%LOCALAPPDATA%\RenderLock\assets`. Exports stay where you chose to save them.

## Interface fonts

Kara's original artwork and display styling are used across the app, recovery and
updater. On Windows, RenderLock can read the three supported Oracle/Pulp font files
from the selected Deadlock installation, verify/cache them privately, and load
them for its own processes. No font files are shipped or installed system-wide.

Missing or changed fonts use Segoe UI; ordinary interface text also intentionally
uses Segoe UI. Restart RenderLock after selecting the game folder for the first
time to refresh display fonts. Standalone update recovery can reuse the verified
cache in `%LOCALAPPDATA%\RenderLock\ui-fonts-v1`.

## A stopped export

Recognized failures can offer a scoped retry. Cloth-only, lighting-only and broad
partial-character choices are different decisions. Closing the window declines;
no automatic retry or consent is granted. Guidance and diagnostic paths are also
written to the log before the recovery window opens.

See [troubleshooting](TROUBLESHOOTING.md) before changing unrelated settings.
