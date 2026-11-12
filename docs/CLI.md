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
| `--external-map "maps\RenderLockMap.blend"` | Include a qualified downloaded or locally prepared map; connect textures and align it; cutting manual |
| `--secondary-motion` or `--cloth` | Request supported approximate cloth |
| `--partial-cloth` | Accept unavailable/failed cloth only; character data remains strict |
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

## Prepare a full map once

```powershell
python -m agr prepare-map `
  --game-install "D:\SteamLibrary\steamapps\common\Deadlock" `
  --blender "D:\Apps\Blender 5.2\blender.exe" `
  --asset-cache "D:\RenderLockCache" --map-name dl_midtown
```

This needs Blender 5.2 LTS, substantial free RAM, at least 32 GB free working
space and internet access for the first pinned-tool download. It does not need a
replay. The log prints `RENDERLOCK_MAP_READY=` with the resulting library path.
Use that path with `convert --external-map`. New packages reuse a cached
viewport proxy by default; pass `--no-map-viewport-proxy` to show the full map
while editing. Older downloaded libraries retain their existing proxy behavior.
Keep the complete prepared package; move exported scenes with their adjacent
`RenderLockMapAssets` folder. See the [map guide](../MAP_WORKFLOW.md).


## Qualified particle observation files

`convert --particle-observations observations.json --particle-probe <simulator>
--model-constraints` imports already captured, qualified owner observations.
The same optional file is available under Advanced > Particle simulation.
It requires Installed game assets, particle simulation and model bone rules.
Leaving it empty preserves normal export behavior. A configured file with those
requirements disabled is an error; clear the field to export without it.

The UTF-8 JSON envelope is `{"format":"renderlock-particle-observations",
"schema_version":1,"captures":[{"instance_id":"index:generation",
"capture":{...}}]}`. Each capture is the normalized
`observed_crossbow_owner_birth_6861_v1` record validated by
`agr.particle_crossbow_owner.validate_capture`; it is not a bound package from
another scene. Up to64 records/1MiB are accepted. Unknown envelope/record fields,
duplicate keys/identities, oversized/deep/nonfinite JSON, reserved-output aliases
and captures absent from the selected history refuse. All rows must match the
fresh replay/model/birth/provider before Blender receives them. The report
records the imported byte digest and instance IDs separately from FX coverage.

This does not capture anything from a running game, prove that user-edited
records are authentic, or establish complete particle coverage. Only the current
qualified Windows Crossbow profile is supported by this importer; positive render
children, other heroes/builds and Linux need their own qualification. Missing
observations remain unsupported and strict/explicit-partial rules are unchanged.


### Preparing observations from an existing capture report

`python -m agr prepare-particle-observations --capture-report report.json
--segment scene.agrscene --instance 5682:1` emits observation JSON on stdout
only after every requested instance passes. Save stdout as UTF-8 for use with
`convert --particle-observations`. Repeat `--instance` for multiple records.
A saved `.agrscene` can supply the recorded event/model identities; geometry
assets are not needed merely to normalize the report. The final export still
prepares and independently validates its fresh rig and original event history.

Only the already qualified current Crossbow report shape is accepted. The
normalizer checks the report's declared successful closure/restoration fields,
replay identity, exact birth sample, full owner serial, unique property group,
actual CP0 raw-record slot and existing module/model/graph profile. Simultaneous
indistinguishable births refuse. No observation/default pose is synthesized.
The report digest binds its original bytes. These checks do not authenticate
edited reports or replace the producer's native-reader and cleanup audits.

This command is an offline producer adapter. It does not launch, attach to,
control or read a game, create a live observation, or add other particle families.
The qualified collector remains a separate integration requirement.
