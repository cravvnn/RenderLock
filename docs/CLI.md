# Command-line guide

[All guides](README.md) · [Building from source](BUILDING.md)

The examples use `python -m agr` from an environment with RenderLock's dependencies.
In the extracted Windows portable folder, substitute `runtime\python.exe` for
`python`. Source users can use `.venv\Scripts\python.exe`. Paths below are examples;
replace them with your own files. Run commands from the project/portable folder.

## Inspect and check

```powershell
python -m agr --help
python -m agr doctor --blender "D:\Apps\Blender\blender.exe"
python -m agr inspect --demo "replays\match.dem"
python -m agr compat --game-install "D:\SteamLibrary\steamapps\common\Deadlock" --map-name dl_midtown
```

`doctor` probes the selected Blender; `compat` reads installed resource
identities and layouts. Neither establishes that every selected character,
animation or material will export. The installed map name must be explicit when
needed; a replay label such as `start` is not the installed archive name.

## Standard character preview

```powershell
python -m agr convert `
  --demo "replays\match.dem" `
  --game-install "D:\SteamLibrary\steamapps\common\Deadlock" `
  --source2viewer "tools\source2viewer\Source2Viewer-CLI.exe" `
  --blender "D:\Apps\Blender\blender.exe" `
  --heroes 13 --shot-hero 13 `
  --start-tick 30000 --end-tick 30320 --fps 64 `
  --output "exports\shot.blend"
```

Hero 13 is an example, not a guarantee it appears in your recording. Inspect first.
Omit `--heroes` to select all available character tracks; omit `--shot-hero` for an
overview camera. Replay conversion requires inclusive start/end ticks. A character
beta package omits installed map geometry; a development checkout has a different
feature profile unless explicitly staged as the character beta.

For a saved scene, replace `--demo` with `--segment "exports\shot.agrscene\manifest.json"`.
Keep compatible installed assets or a suitable `--assets` pack available. Saved
packages do not embed every model, lighting input or external map.

## Optional features and consent

| Option | Meaning |
| --- | --- |
| `--external-map "maps\RenderLockMap.blend"` | Append the qualified download; textures/cutting manual, independent of Source lighting |
| `--secondary-motion` or `--cloth` | Request supported approximate cloth |
| `--partial-cloth` | Accept unavailable/failed cloth only; character data remains strict |
| `--partial-effects` with `--particle-probe` | Accept documented incomplete simulation; if it renders no objects, keep supported recorded layers instead. Character animation stays strict. |
| `--partial-lighting` | Accept partial reference-lighting limits; character data remains strict |
| `--partial` | Broader missing-model/animation compromises; validation remains active |
| `--motion-only` | Explicit movement-only export without skeletal evaluation |
| `--material-repairs` | Supported static emission/unlit and artistic glass repairs |
| `--model-constraints` | Supported authored model bone rules |
| `--no-effects` | Omit effects for this export |
| `--overwrite` | Permit replacing matching existing outputs |
| `--keep-work` | Retain intermediate work after success |

Do not use `--partial` merely to accept lighting or cloth limits. See
[lighting](LIGHTING.md), [cloth](../CLOTH_WORKFLOW.md) and
[map](../MAP_WORKFLOW.md) for the actual feature prerequisites.

## Lighting and particle tools

The GUI presets assemble the dependent look settings. CLI users must provide
the matching flags/tools explicitly. Source hero lighting uses `--include-map`,
`--map-name`, `--source-lighting`, `--source-hero-lighting`, `--partial-lighting`,
`--world-region-probe`, `--lighting-dotnet`, `--probe-atlas-decoder` and
`--cubemap-decoder`; use `--character-only` to omit installed geometry in a
development checkout. Elagrimm additionally needs `--source-npr-lighting`,
`--character-shader` and a selected `--shot-hero`. Review current `convert --help`
and the [lighting guide](LIGHTING.md); an external map does not supply these inputs.

Recorded simulation uses `--particle-probe` with the qualified helper. A managed
`.dll` also needs `--dotnet`; the qualified Windows `.exe` can run directly.
`--particle-history-ticks` requests earlier recorded history, not support for
otherwise unsupported operations. The report lists actual completed layers.

For normal use the GUI is the easier way to select the look, bundled helpers,
consent and recovery. The exact selected command and outcomes remain in diagnostics.
