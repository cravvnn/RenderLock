# Troubleshooting and support

[All guides](README.md) · [User guide](USER_GUIDE.md)

Start with the exact error and selected feature. A stopped optional feature does
not mean that allowing missing character animation is the right workaround.

## Recovery choices

For recognized failures, **Export recovery** explains what is unavailable and
offers scoped choices. Examples include keeping supported cloth, omitting cloth
or effects, continuing without incompatible lighting, or omitting the downloaded
map. Read the losses described by the actual choice.

Closing the window never retries or accepts partial output. Guidance, choice
descriptions and diagnostic locations are logged before the window opens. If the
window cannot open, the main interface should remain responsive; use the logged
guidance to review settings and retry manually. Unknown errors may have no safe
retry suggestion. A retry can repeat preparation/scene creation while reusing
verified cache entries.

| Control | Scope |
| --- | --- |
| **Keep supported cloth if some cloth is unavailable** / `--partial-cloth` | Cloth omissions/failures only; models and animation stay strict |
| **Accept partial lighting limits for this export** / `--partial-lighting` | Artistic/reference lighting limits; models and animation stay strict |
| **Allow partial export / movement placeholders** / `--partial` | Broader missing-model/animation compromises; identity and validation checks still apply |
| **Overwrite existing scene and companion package** / `--overwrite` | Explicit permission to replace matching output files |

## Where diagnostics go

Normal Windows logs are under `%LOCALAPPDATA%\RenderLock\logs`. Failed exports
try to retain a bounded `renderlock-support-*.zip` there, beside the startup log.
The recovery window shows the path and provides **Open diagnostics** and **Copy
path**. The log also prints **Support ZIP:** and the exact path.

If the destination is unwritable, archive creation can fail and that failure is
logged. Forced termination can prevent finalization. Keep the retained `agr-*`
work directory printed in the export log; it can contain fuller evidence than
the small support ZIP.

Useful retained files can include `failure.json`, `segment.json`,
`installed_asset_report.json`, animation reports and `scene.blender.log`.
Availability depends on how early the job stopped. Large reports can be omitted
from the support ZIP to keep it bounded.

## The app will not start

Extract the entire Windows ZIP into a new writable folder. Keep **RenderLock.exe**,
`runtime`, `tools`, `bin` and the remaining files together. Try
**Launch_RenderLock.cmd**, then retain the startup log. Do not repair a verified
portable installation by copying arbitrary DLLs into it or editing its manifest.
An interrupted app update has its own [recovery workflow](../UPDATING.md).

## Game updates, models or skeleton errors

The replay's model IDs, bone/mask indices and animation tasks must match compatible
installed assets. A different model with a similar filename is not a safe
substitute. A recent replay from the current game can be a useful control, but
does not establish every feature's compatibility.

The base-asset policy excludes custom addon/mod mounts, including renamed addon
folders. Check the recorded winning archive and missing dependency in diagnostics.
If base files were modified directly, restore the stock files through the tool
that changed them or Steam's verification workflow. Do not rename backup models,
substitute another hero, disable corruption checks or clear every cache entry.

For read-only installed resource checks, use `python -m agr compat`; see the
[CLI guide](CLI.md). Exit zero/resource readiness does not certify selected
animation, materials or a complete export. An app update may be needed to qualify
changed operations or lighting inputs.

## Cloth stops the export

Use the [cloth guide](../CLOTH_WORKFLOW.md). Unsupported stiffening, collision or
other FE operations cannot be made faithful by removing their validation checks.
Choose a cloth-only recovery option or disable cloth if that compromise suits the
shot. Keep broad partial character export separate.

## Materials or lighting look wrong

- **No Elagrimm file beside the output:** expected. Appended materials/groups are
  inside the scene. Select a character mesh, not its replay-root Empty. Confirm
  the actual exported look and material coverage in the report.
- **Shader not applied:** check the qualified library, Elagrimm + Source look,
  included follow target, installed lighting inputs and explicit lighting consent.
  Source/NPR refusal before Blender cannot produce an appended shader.
- **Emission texture present but no glow:** supported static cases can use
  **Emission and glass repairs (experimental)**. This is not universal Source
  emission recovery, and it does not require Elagrimm.
- **Normal texture present but inverted/incorrect shading:** retain the finished
  `.blend`, matching report and converted input identities. No universal channel
  flip is established; do not assume every material needs the same repair.
- **Dark Source look:** inspect probe/material coverage and the selected position.
  Source lighting is partial. Preview lights and artistic adjustments are editable,
  but a global emission multiplier is not a demonstrated fidelity fix.

See [Lighting and materials](LIGHTING.md) for the look presets and limits.

## The map is missing, pink or too heavy

Use the [supported download](https://maps.allevilmc.com/download), extract the
entire ZIP and choose its unchanged `RenderLockMap.blend` on **Map**. The raw
contributor ZIP/glTF is not the supported automatic-append library.

Map textures remain external. Cut in **Solid** view before loading textures,
remove unused map materials/images, then use **File > External Data > Find Missing
Files** on the downloaded `Textures` folder. Set the external optimizer's **Map**
selector to **RenderLockMap**; hiding characters alone does not scope cutting.
For large merged cells, **Precise Cut** gives a closer boundary than **Entire
Objects**. Keep the downloaded library unchanged and edit the final scene.

The complete map is large and must load before you cut it. A short replay does
not eliminate that cost. Cut the area you need before heavy material-preview or
render work. Ambient props stay static, and snapshot geometry can differ from
the replay. The map is not a source of installed lighting data or cloth collision.

Whole-map relinking exceeded 19 GB of process RAM in a supplied Linux test before
it was stopped. Missing map images also produced EEVEE GPU texture errors and
pink characters in that witness. Hide `RenderLockMap` to inspect characters, cut
the map first and relink the retained images before EEVEE. If the camera sees a
wall, inspect its placement or cut that geometry; map alignment alone does not
guarantee a clear camera view. See the [map workflow](../MAP_WORKFLOW.md#in-blender).

## Missing effects or different motion

Some particle trees/renderers, attachments, provider histories and model-switch
cases remain unsupported. More recovered history helps only supported recorded
cases. An empty point-follow inventory is neutral; genuine attachment failures
still matter. A successful character export is not complete ability reproduction.

Recorded body poses also do not establish every client-only presentation detail,
including the backward-dash body turn. Automatic ragdolls are outside scope;
death animation can require user-authored work. Keep these limitations distinct
from an actual missing or mismatched recorded pose.

## Reporting an issue

[Open a GitHub issue](https://github.com/cravvnn/RenderLock/issues) or use the
[Deadlock Editing Discord](https://discord.gg/deadlockediting). Include:

- RenderLock version, operating system and Blender version.
- Installed `ClientVersion`/`SourceRevision` from `game/citadel/steam.inf`.
- Replay/match ID, selected characters, ticks, FPS and requested optional features.
- Exact error, support ZIP/startup log and the `.agr-report.json` if one was saved.
- For appearance issues, a clear comparison and the finished scene/converted-input
  identities where you have permission to share them.

Review diagnostics first: paths, replay names and other identifying details can
remain. Do not post credentials, private replay data or game assets publicly.
