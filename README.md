# RenderLock

RenderLock turns a selected section of a Deadlock replay into an animated Blender scene. Use it to bring player performances into cinematics, animation edits and still renders.

RenderLock reads local replay and installed game files. It does not launch or attach to Deadlock. Blender scenes retain the original world coordinates so a separate map can be added later.

## Character beta

- Character models, packed textures, recorded movement and skeletal animation.
- A selected tick range, all recorded heroes or a subset of hero IDs.
- An overview camera or a follow camera for one hero.
- Supported particle effects, with missing effects listed in a coverage report.
- Optional decompiled cloth proxies, bone constraints and material repairs.
- **Experimental character look (Lighting lab):** partial Source lighting, probe reflections and the Toilet Horse Shoe character shader.

Map export is unavailable in the beta; scenes stay character-only and maps are added separately in Blender. The character look is partial and artistic by design, cloth and effects are incomplete and vary by hero, and a successful character export does not mean every effect was reproduced. Always read the coverage report.

## Experimental character look (Lighting lab)

Everything the look pipeline needs is included in the portable package, except the shader library itself:

1. In **Advanced**, tick **Allow partial export / movement placeholders** (the look pipeline requires it).
2. In **Export**, choose the replay and set a **Follow hero ID** (the look needs a follow camera).
3. In **Map**, tick **Prepare installed map region** (the map name defaults to your replay's map), then in **Lighting lab** tick **Partial static diffuse lighting**, **Partial hero probe lighting / reflections** and **Partial NPR indirect**.
4. Point **Character shader .blend** at your local copy of the Toilet Horse Shoe library (version 2.5, SHA-256 `a5ab487c827efd981e726ef9c828216fe9baaf29ccf9b8ff7c8e474ff37a3996`). The library is not bundled.
5. Export. The result uses Eevee with an artistic Source-lighting bridge and Fresnel rim; hero lighting, probe reflections and NPR indirect are partial. The coverage report and the in-scene notes list exactly what was and was not applied.

## Windows portable

Ready-to-run Windows packages are published on the Releases page of this repository. Extract the complete portable ZIP, then double-click **RenderLock.exe**. Python, its runtime dependencies, the **Source 2 Viewer 20.1 command-line exporter**, the character-look helper tools and a .NET runtime are included; the launcher does not download packages. A `Launch_RenderLock.cmd` fallback remains for troubleshooting.

Install Blender separately. The candidate is checked with Blender 5.2.1 LTS. RenderLock does not include replays, game assets or third-party shader libraries.

1. In **Setup**, select Blender and your Deadlock folder. The bundled Source 2 Viewer CLI and asset cache are filled in automatically; choose an asset cache outside the game folder if you prefer another location.
2. In **Export**, choose a `.dem` and select **Inspect replay**.
3. Review the start and end ticks. Inspection suggests a short preview rather than the full match. Leave hero IDs empty for the roster, or enter comma-separated IDs.
4. Choose a new `.blend` path and export. Keep partial export off unless you explicitly accept missing animation/models or movement placeholders (or use the Lighting lab).
5. Read the completion message and coverage report, then open the scene in Blender.

The output includes a `.blend`, a companion `.agrscene` package and an `.agr-report.json` report. Keep the companions for validation or reuse. Packed textures make the `.blend` independent of the temporary conversion folder.

The launcher writes its startup log under `%LOCALAPPDATA%/RenderLock/logs`. Preferences are stored under `%APPDATA%/RenderLock`.

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

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the fail-closed contract, and what makes a change reviewable.
