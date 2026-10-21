# RenderLock

RenderLock turns a selected section of a Deadlock replay into an animated Blender scene. Use it to bring player performances into cinematics, animation edits and still renders.

RenderLock reads local replay and installed game files. It does not launch or attach to Deadlock. Blender scenes retain the original world coordinates so a separate map can be added later.

## Character beta

- Character models, packed textures, recorded movement and skeletal animation.
- A selected tick range and a named checklist of characters to export.
- An overview camera or a follow camera for one hero.
- Supported particle effects, with missing effects listed in a coverage report.
- Automatic reconstruction for supported cloth proxies, plus optional bone constraints and material repairs.
- **Experimental character look (Lighting lab):** partial Source lighting, probe reflections and the Elagrimm character shader.

Map export is unavailable in the beta; scenes stay character-only and maps are added separately in Blender. The character look is partial and artistic by design, cloth and effects are incomplete and vary by hero, and a successful character export does not mean every effect was reproduced. Always read the coverage report.

## Experimental character look (Lighting lab)

Everything the look pipeline needs is included in the portable package, except the shader library itself:

1. The **Partial** lighting controls accept artistic lighting limits while keeping character models and skeletal animation strict. Leave **Advanced → Allow partial export / movement placeholders** off unless you also accept missing character data.
2. In **Export**, choose the replay, inspect it, then choose a hero by name under **Follow camera** (the look needs a follow camera). The list comes from that recording; **Overview (no follow)** clears the target. Include the chosen hero in **Characters**.
3. In **Map**, tick **Prepare installed map region** and choose the installed map name (the default is `dl_midtown`; check it yourself). In the character beta this reads lighting data without exporting map geometry. In **Lighting lab**, **Partial Source character diffuse lighting** applies indirect diffuse lighting to character materials. Add **Partial hero probe lighting / reflections** and **Partial NPR indirect** for the full look pipeline. Development map scenes retain the **Partial static diffuse lighting** route; **Characters only** uses character diffuse lighting.
4. Download **BLENDER-Deadlock-Shader.blend** from [Elagrimm's shader](https://github.com/Elagrimm/BLENDER-Deadlock-Shader/blob/e18af8b1c9c1d34b4af72002fb1f4136aa159515/BLENDER-Deadlock-Shader.blend) and select it in **Elagrimm shader .blend**. Supported revision: `e18af8b1c9c1d34b4af72002fb1f4136aa159515`, SHA-256 `018bb08347dcd9cf48da317f5b4c2bcee9e0dcc4fecd4f32c3be3a3225ec1891`. The GPL-3.0 library stays a separate download. Earlier Toilet Horse Shoe selections must be replaced; that library is no longer supported.
5. Export. The result uses Eevee with an artistic Source-lighting bridge and Fresnel rim; hero lighting, probe reflections and NPR indirect are partial. The coverage report and the in-scene notes list exactly what was and was not applied.

The Elagrimm look keeps the editable `AGR | Key` and `AGR | Fill` preview lights enabled. Follow-camera exports place and animate these lights relative to the selected character and viewing side, so the character stays readable as it moves. They provide artistic Blender direct lighting alongside the partial Source indirect inputs. Adjust their power or the material's **Additional Light Influence** for your shot; these preview lights do not reproduce the game's direct lights or shadows. Rebuild the light animation after editing the camera or replay root.

Probe selection follows each selected character's movement region, and distant characters can use different probes. Data is decoded in bounded batches of at most 32 probes and 256 MiB of atlas output; reflection decodes contain at most four cubemaps per batch. Each evaluated character surface must still have verified coverage. Larger selections require more cache space and processing time. An external map `.blend` does not replace the installed lighting data. Missing or unqualified lighting fails explicitly.

CLI users can select `--partial-lighting` to accept artistic lighting limits while retaining strict character data. The existing `--partial` option also permits missing models or animation and remains a separate, broader choice.

## Windows portable

Ready-to-run Windows packages are published on the Releases page of this repository. Extract the complete portable ZIP, then double-click **RenderLock.exe**. Python, its runtime dependencies, the **Source 2 Viewer 20.1 command-line exporter**, the character-look helper tools and a .NET runtime are included; the launcher does not download packages. A `Launch_RenderLock.cmd` fallback remains for troubleshooting.

Install Blender separately. The candidate is checked with Blender 5.2.1 LTS. RenderLock does not include replays, game assets or third-party shader libraries.

1. In **Setup**, select Blender and your Deadlock folder. The bundled Source 2 Viewer CLI and asset cache are filled in automatically; choose an asset cache outside the game folder if you prefer another location.
2. In **Export**, browse to a `.dem`. RenderLock inspects it and fills the **Characters** checklist. If you type a path, select **Inspect replay**.
3. Tick the characters to export, using **All** or **None** to start your selection. Review the start and end ticks; inspection suggests a short preview rather than the full match. Each choice includes all matching character tracks. The recording's roster does not guarantee availability or animation compatibility in every clip. Manual hero IDs remain in **Advanced** if the roster cannot be read.
4. Choose a new `.blend` path and export. Keep partial export off unless you explicitly accept missing animation/models or movement placeholders (or use the Lighting lab).
5. Read the completion message and coverage report, then open the scene in Blender.

The output includes a `.blend`, a companion `.agrscene` package and an `.agr-report.json` report. Keep the companions for validation or reuse. Packed textures make the `.blend` independent of the temporary conversion folder.

The launcher writes its startup log under `%LOCALAPPDATA%/RenderLock/logs`. Preferences are stored under `%APPDATA%/RenderLock`.

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

