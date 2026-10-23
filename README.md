# RenderLock

RenderLock turns a selected section of a Deadlock replay into an animated Blender scene. Use it to bring player performances into cinematics, animation edits and still renders.

RenderLock reads local replay and installed game files. It does not launch or attach to Deadlock. Blender scenes retain the original world coordinates so a separate map can be added later.

Installed exports use the base Citadel/Core archive layout and any explicitly selected map. Custom addon/mod search paths in `gameinfo.gi` are excluded from export, including renamed mod folders and directly listed addon VPKs. This prevents a local replacement model from introducing missing dependencies into a base character export. The preparation log reports skipped paths, and the asset report retains their details. RenderLock leaves installed mods and game configuration in place. This path policy does not certify files changed directly inside the base archives; missing or corrupt required base resources still fail validation.

## Character beta

- Character models, packed textures, recorded movement and skeletal animation.
- A selected tick range and a named checklist of characters to export.
- An overview camera or a follow camera for one hero.
- Supported particle effects, with missing effects listed in a coverage report.
- Automatic reconstruction for supported cloth proxies, plus optional bone constraints and material repairs.
- **Experimental character look (Lighting lab):** partial Source lighting, probe reflections and the Elagrimm character shader.

Installed map geometry conversion is unavailable in the beta. On **Map**, you can download the separate supported map package, extract it, choose `RenderLockMap.blend` and enable **Include downloaded map**. RenderLock appends and aligns it in the final scene independently of Source lighting. Texture relinking and cutting with the separate Map Optimizer remain manual; see [MAP_WORKFLOW.md](MAP_WORKFLOW.md). The character look is partial and artistic by design, cloth and effects are incomplete and vary by hero, and a successful character export does not mean every effect was reproduced. Always read the coverage report.

If a converted emissive texture is present but has no visible glow, **Advanced → Character options → Emission and glass repairs (experimental)** can restore supported static emission from the material's recorded tint and strength. This option also applies an artistic glass approximation. It remains optional and does not recover every shader's glow, animated mask or game appearance; Elagrimm is not required for converted emission.

## Experimental character look (Lighting lab)

Everything the look pipeline needs is included in the portable package, except the shader library itself:

1. In **Lighting lab**, choose **Standard materials** for converted materials, **Source lighting** for partial character diffuse lighting and probe reflections, or **Elagrimm + Source** for the artist shader with the full existing Source/NPR bridge. The Source presets prepare their linked lighting data together. **Custom** retains manual controls for existing configurations.
2. In **Export**, choose the replay and inspect it. For **Elagrimm + Source**, choose a hero by name under **Follow camera** and include that hero in **Characters**. The list comes from that recording; **Overview (no follow)** clears the target.
3. Check the installed map name in **Map** (the default is `dl_midtown`; check it yourself). Source presets do not need a separate **Prepare installed map region** checkbox. The character beta reads the installed map's lighting data without exporting map geometry.
4. For **Elagrimm + Source**, download **BLENDER-Deadlock-Shader.blend** from [Elagrimm's shader](https://github.com/Elagrimm/BLENDER-Deadlock-Shader/blob/e18af8b1c9c1d34b4af72002fb1f4136aa159515/BLENDER-Deadlock-Shader.blend) and select it in **Elagrimm shader .blend**. Supported revision: `e18af8b1c9c1d34b4af72002fb1f4136aa159515`, SHA-256 `018bb08347dcd9cf48da317f5b4c2bcee9e0dcc4fecd4f32c3be3a3225ec1891`. The GPL-3.0 library stays a separate download. Earlier Toilet Horse Shoe selections must be replaced; that library is no longer supported.
5. For a Source look, select **Accept partial lighting limits for this export**, then export. Relevant input or settings changes reset this acceptance. Leave **Advanced → Allow partial export / movement placeholders** off unless you also accept missing character data. The Elagrimm result uses Eevee with an artistic Source-lighting bridge and Fresnel rim; hero lighting, probe reflections and NPR indirect are partial. The coverage report and the in-scene notes list exactly what was and was not applied. Appended Elagrimm materials and node groups are embedded inside the exported scene; no separate shader file is copied beside it. Select a character mesh to inspect its materials; the selected replay-root Empty has no material slots.

The shader picker appears for **Elagrimm + Source** and **Custom**. Saved library and tool locations are retained when switching looks. **Advanced tool overrides** contains the bundled decoder and .NET program paths: the probe atlas decoder reads baked indirect-light data, and the cubemap decoder reads reflection environment images. Portable installations discover these tools automatically; detecting the files does not establish compatibility with the current game.

Game updates can invalidate Source/NPR material evaluation. The evaluator from game build 6813 is qualified for the existing bounded constant-color subset; unknown evaluator identities and unsupported expressions remain strict. RenderLock checks the selected material set before preparing animation or lighting and records the actual evaluator identity when constant overrides are used. A resource-ready compatibility report still requires selected-material and animation validation during export. For an unqualified evaluator, the recovery window can offer an explicit retry without NPR and its dependent Elagrimm shader while retaining Source diffuse/probe lighting and strict animation. **Standard materials** avoids the optional Source-lighting path. A standalone Elagrimm look without Source inputs is not available.

The Elagrimm look keeps the editable `AGR | Key` and `AGR | Fill` preview lights enabled. Follow-camera exports place and animate these lights relative to the selected character and viewing side, so the character stays readable as it moves. They provide artistic Blender direct lighting alongside the partial Source indirect inputs. Adjust their power or the material's **Additional Light Influence** for your shot; these preview lights do not reproduce the game's direct lights or shadows. Rebuild the light animation after editing the camera or replay root.

Probe selection follows each selected character's movement region, and distant characters can use different probes. Data is decoded in bounded batches of at most 32 probes and 256 MiB of atlas output; reflection decodes contain at most four cubemaps per batch. Each evaluated character surface must still have verified coverage. Larger selections require more cache space and processing time. An external map `.blend` does not replace the installed lighting data. Missing or unqualified lighting fails explicitly.

CLI users can select `--partial-lighting` to accept artistic lighting limits while retaining strict character data. The existing `--partial` option also permits missing models or animation and remains a separate, broader choice.

## Windows portable

Ready-to-run Windows packages are published on the Releases page of this repository. Extract the complete portable ZIP, then double-click **RenderLock.exe**. Python, its runtime dependencies, the **Source 2 Viewer 20.1 command-line exporter**, the character-look helper tools and a .NET runtime are included; the launcher does not download packages. A `Launch_RenderLock.cmd` fallback remains for troubleshooting.

Install Blender separately. The candidate is checked with Blender 5.2.1 LTS. RenderLock does not include replays, game assets or third-party shader libraries.

1. In **Setup**, select Blender and your Deadlock folder. The bundled Source 2 Viewer CLI and asset cache are filled in automatically; choose an asset cache outside the game folder if you prefer another location.
2. In **Export**, browse to a `.dem`. RenderLock inspects it and fills the **Characters** checklist. If you type a path, select **Inspect replay**.
3. Tick the characters to export, using **All** or **None** to start your selection. Review the start and end ticks; inspection suggests a short preview rather than the full match. Each choice includes all matching character tracks. The recording's roster does not guarantee availability or animation compatibility in every clip. Manual hero IDs remain in **Advanced** if the roster cannot be read.
4. Choose a new `.blend` path and export. Keep partial export off unless you explicitly accept missing animation/models or movement placeholders. Cloth-only and lighting consent have separate controls.
5. Read the completion message and coverage report, then open the scene in Blender.

The output includes a `.blend`, a companion `.agrscene` package and an `.agr-report.json` report. Keep the companions for validation or reuse. Packed textures make the `.blend` independent of the temporary conversion folder.

The launcher writes its startup log under `%LOCALAPPDATA%/RenderLock/logs`. Preferences are stored under `%APPDATA%/RenderLock`.

RenderLock uses Kara's original artwork and display typography. On Windows it reads the three supported VALVE Oracle/Pulp font files from your selected Deadlock installation and loads verified copies privately for RenderLock. The cache lives under `%LOCALAPPDATA%/RenderLock/ui-fonts-v1`, allowing standalone update recovery to use the same fonts. No fonts are installed into Windows or included in the download. Missing or changed game fonts use Segoe UI; ordinary interface text also uses Segoe UI by design. Restart RenderLock after selecting a game folder for the first time to refresh the display fonts.

### Recovering a stopped export

For recognized failures, the recovery window explains what cannot be included and offers specific retry choices. You can keep supported approximate cloth, omit cloth or effects, or turn off incompatible Source lighting. Each choice describes what will be missing. The same guidance and diagnostic locations appear in the log before the window opens. If the window cannot open, the interface stays responsive and the log explains how to review settings and retry manually. Closing recovery never starts a retry. A retry may repeat preparation and scene creation while reusing verified cached assets.

**Advanced → Output policy → Keep supported cloth if some cloth is unavailable** accepts omitted or failed cloth and keeps character models and animation strict. CLI users can select `--partial-cloth`. Missing character animation or models requires the separate, explicit **Allow partial export / movement placeholders** choice (`--partial`); rig identities, replay identities and data validation remain checked.

Failed exports try to save a small `renderlock-support-*.zip` beside the startup log. The recovery window shows its exact path and provides **Open diagnostics** and **Copy path**. The ZIP contains the error, options, available logs and bounded reports. Raw work files remain in the retained export folder for deeper investigation. If the ZIP cannot be written, the log explains that failure; forced process termination can prevent diagnostic finalization. Paths and replay filenames may remain in diagnostics, so review them before sharing.

### Reusing models and textures

Keep **Setup → Generated asset cache** pointed at the same folder between exports. RenderLock converts each model and its textures once for a matching set of source files and converter tools, then reuses the verified cache for other replay ranges. The log identifies cached models and any conversions that need rebuilding. Material repairs are cached separately from the original conversion.

The first export after an affected game asset or conversion tool changes may need to rebuild that cache entry. Do not edit cached files in place. The exported `.blend` still packs its textures, so it can be moved independently of the cache.

### Model bone rules

**Model bone rules (experimental)** applies supported rules authored in the character model, such as making one bone aim at, follow or twist with another. These supplement the recorded skeletal animation. They are separate from cloth simulation, and unsupported rules remain explicitly reported. The report lists applied rules and any rules the game disables because their driven bone is missing.

### Skeleton compatibility errors

A replay's bone and animation-mask indices must match the installed animation skeleton. RenderLock stops when they differ, because applying those indices to another rig can animate the wrong bones. A different replay/game asset version is one possible cause; the diagnostic report identifies whether the failure concerns bones, masks or invalid names.

Installed exports also check the game build, probe layout and shader identities before converting models. The log names the incompatible resource if a game update needs requalification, and reports per-stage timings at the end. The standalone check is `agr compat --game-install <Deadlock folder> --map-name dl_midtown`; it only reads installed files.

If this happens, keep the diagnostic folder printed in the log. It contains `failure.json`, `segment.json`, `animation_tables.json`, `installed_asset_report.json` and the decoded skeleton below `installed-assets/clip_cache`; rig mismatches also include `installed-assets/animation_asset_report.json`. A failed Blender scene build also retains `scene.blender.log` with the exact error, and `failure.json` repeats its last lines and stage timings. Include the RenderLock version, replay match ID, selected ticks and installed game build when reporting the issue. These diagnostic files can contain replay details, so share them privately when needed. A replay recorded with your currently installed game build is a useful compatibility check.

A character can also change models and animation graphs during a transformation. Installed-game exports retain separate models, rigs and recorded poses under one player root and follow camera. The character stays selectable. A reset tick may have no recorded pose: strict export explains the missing tick, while explicitly selected partial export hides that sample and reports it without substituting a pose. For example, the verified Silver switch in replay 120527093 has no pose at tick 80656; both forms and the other 800 selected poses are retained in partial export. Particle model/attachment providers spanning a model switch remain unsupported and are reported separately. Custom asset packs still require a compatible model/rig for each selected track; changing graphs within a single unchanged model remains unsupported.

## Source checkout

Requires 64-bit Python 3.12 or newer, Blender and the Source 2 Viewer CLI (a bundled copy lives in the portable package's `tools/source2viewer` folder). Create a virtual environment, install `requirements.txt`, then run:

```powershell
python -m agr gui
python -m agr --help
```

On Windows, `Launch_RenderLock.bat` checks the local Python, installs the pinned dependencies and opens the desktop app; `Launch_RenderLock_Development.bat` uses an existing `.venv`. On Linux or macOS run `./launch_renderlock.sh`.

The native replay helper belongs at `bin/agr-extract.exe`. Build it with Rust using `cargo build --release --locked --manifest-path native/Cargo.toml`, then copy the executable from `native/target/release`. The source ZIP contains the same character-beta feature profile as the portable candidate, including the Lighting lab look tools.

### Building the portable package

`tools/package_character_beta.py` stages the Windows portable and source archives:

```powershell
python tools/package_character_beta.py --source . --output <new-folder> `
  --native-helper bin/agr-extract.exe `
  --source2viewer-dir <Source 2 Viewer 20.1 folder> `
  --lighting-kit <built helper kit> `
  --particles-dir <built ParticleProbe folder> `
  --dotnet-runtime <dotnet shared runtime folder> `
  --launcher-exe tools/launcher/target/release/RenderLock.exe
```

- The Source 2 Viewer CLI comes from the ValveResourceFormat 20.1 release (github.com/ValveResourceFormat/ValveResourceFormat/releases); the folder must contain `Source2Viewer-CLI.exe`.
- The lighting kit and particle helper are built from the `research/` projects (WorldRegionProbe, ProbeAtlasDecode, CubemapDecode, ParticleProbe) against the ValveResourceFormat library.
- The launcher is built with `cargo build --release --manifest-path tools/launcher/Cargo.toml`; `python tools/launcher/set_icon.py` then embeds the app icon (`artwork/RenderLock.ico`, regenerated from the artwork with `blender -b --factory-startup --python tools/make_app_icon.py`).
- The .NET runtime is any win-x64 `Microsoft.NETCore.App` shared runtime folder.

Run the source checks with `python -m pytest -q` after installing pytest. Third-party notices are in `THIRD_PARTY.md`, `agr/vendor` and `native/licenses`; the portable runtime also retains each dependency's license files.

## Compatibility

Game updates can change models, animation tasks and replay data. Unsupported operations fail explicitly. Partial export must be selected deliberately, and the coverage report distinguishes decoded animation, omitted effects and experimental features. Saved-scene consistency checks do not establish identical in-game lighting or visual parity.

## Credits

RenderLock builds on community tools, shaders and research. See [CREDITS.md](CREDITS.md) for the people and projects behind it, including Source 2 Viewer, the shader authors and the original cloth work. License notices are in [THIRD_PARTY.md](THIRD_PARTY.md).

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the fail-closed contract, and what makes a change reviewable.
## In-app updates

Windows portable builds from 0.3.0b4 include an **Updates** page. Review release
notes, download an update, then click **Install and restart** when your work is
finished. Startup checks fetch metadata only and can be disabled. Older versions
need one manual upgrade to receive this feature. See [Updating RenderLock](UPDATING.md).
