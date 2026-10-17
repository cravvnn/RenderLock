# Reuse a separate map in Blender

AGR exports the replay's heroes, animation, supported effects and supported experimental cloth. Map conversion is off by default. Keep a reusable map `.blend` separately, then link or append its map collection into each replay scene. This avoids repeating map conversion and makes replay exports smaller. Blender still needs to load and render the map when you add it.

## Prepare the map once

1. Use the same map revision as the replay. Keep the map's patch or build identifier in the filename and its metadata; a current installation can differ from an older replay.
2. Put the map geometry and required lights or props in a clearly named collection, such as `DL_midtown_map`. Keep the collection's instance offset at zero. Pack textures with **File → External Data → Pack Resources**, or keep the texture folder alongside the map file.
3. Match AGR's world coordinates before saving. Standard AGR replay exports use Source X/Y/Z multiplied by **0.0254 metres per Source unit**, with world origin `(0, 0, 0)` and no world yaw. A manually imported Source 2 Viewer glTF needs AGR's map coordinate correction; a raw glTF import alone can be rotated relative to the heroes. An already aligned map from an AGR scene includes the `AGR | map coordinate basis` object—retain it and its parent relationships.
4. Verify several recognizable hero positions against the map, including different heights, then save the map `.blend`. Keep the map at its original world placement. Do not recenter either collection independently.

The hero model's 180-degree orientation correction belongs to the model rig; it is not a rotation to apply to the map. Custom saved manifests can set a different origin, unit scale or world yaw. Their map must use the same coordinate settings.

You can continue preparing the map manually in Blender. AGR's **Convert map region (optional, slow)** option remains available for a one-time regional export, but it still depends on the installed map converter and does not create a full-map asset automatically. If you reuse a regional map, make sure later replay paths stay within the geometry it contains.

## Use the map in a replay export

1. In AGR, leave **Convert map region** unchecked. Export the desired tick range with supported particles and experimental cloth enabled as needed.
2. Open the resulting replay `.blend` in Blender.
3. Choose **File → Link**, select your map `.blend`, open **Collection**, and select the map collection. Keep any collection instance at location `(0, 0, 0)`, rotation `(0, 0, 0)` and scale `(1, 1, 1)`; retain the map collection's existing internal alignment.
4. Save the replay scene. Keep the linked map file accessible at a stable path, or use relative paths when distributing a shared folder containing both files.

**Link** keeps the map in the separate file, so fixes to that map can be reused by multiple replay scenes. **Append** copies the collection into the replay file and is useful when you want to edit it independently, at the cost of a larger scene. Choose **Collection**, rather than individual objects, to preserve the map's complete hierarchy. Avoid adding map cameras when the replay's overview camera is sufficient.

If you move a linked map, repair the library path in Blender before saving. For an appended map, pack or include its textures before moving the scene to another computer.

## Cloth, deaths and collision

Experimental cloth currently covers Seven’s waist surface using installed character inputs and approximate native Blender cloth with animated body collision. Each export's coverage report identifies simulated and unsupported parts. This does not require the external map, and it does not add map collision. Some garments can therefore intersect floors or scenery, especially during a death sequence.

AGR does not generate ragdolls or reconstruct deaths with physics. You can edit or add death animations in Blender. Cloth is baked to the exported character animation: changing that animation afterward does not automatically update the existing cloth bake. After editing Seven’s animation, root motion, frame range or FPS, save a separate copy, choose the embedded `AGR_Rebake_Cloth.py` in Blender’s Text Editor and click Run Script. If AGR has moved, select its program folder when prompted. A failed rebake disables the simulated waist output and retains ordinary skinned motion; it does not automatically restore the previous cache. See `CLOTH_WORKFLOW.md`.

## Experimental Source static diffuse lighting

The development copy has an explicit **Partial static diffuse lighting** option under
**Experimental Source lighting**. Enable **Convert map region**, select the installed
map, and provide the qualified `WorldRegionProbe.dll` and .NET executable. This helper
is currently a development build; it is not included in the preserved dev8 release.

For CLI conversion, add these options to the normal installed-game command:

```text
--include-map --map-name dl_midtown --map-margin 8
--source-lighting --world-region-probe C:\path\WorldRegionProbe.dll
--lighting-dotnet C:\path\dotnet.exe
```

The first conversion decodes the four lightmaps and exports only intersecting static
world-node draws. Subsequent conversions reuse content-verified caches. Keep the asset
cache outside the game installation and preserve it when reusing these prepared assets.
Lighting selection does not enable `--partial` for core character animation.

Supported surfaces receive recovered baked SH diffuse lighting with the qualified
BRDF lookup for indirect energy and multiple scattering. Entity-lump geometry,
direct light/shadows, map reflections, fog and full Source material
response remain incomplete. Read the map lighting coverage and warning in the export
report; a successful hero export does not mean all map lighting is supported.
The saved `.blend` packs its textures. The portable `.agrscene` continues to omit derived
map assets; select map preparation again when rebuilding it, or reuse the map `.blend`.

To include experimental hero probes and reflections, also enable **Partial hero probe
lighting and reflections**, or add:

```text
--source-hero-lighting
--probe-atlas-decoder C:\path\ProbeAtlasDecode.dll
--cubemap-decoder C:\path\CubemapDecode.dll
```

This requires Source map lighting above. The decoder DLLs and their qualified
companions are development tools, not part of preserved dev8. Hero lighting uses
explicit reference intensity, tint, normalization, and filtering assumptions;
it is not full game parity. AGR checks evaluated hero surfaces at every integer
export frame against the selected probe region and rejects uncovered surfaces.
Fractional-frame coverage and runtime light changes remain unverified. Asset or
coverage validation failures stop the requested hero-lighting export.

Hero NPR lighting, rim/outline/transmissive response and material ambient occlusion
are not yet reproduced. The audited Priest materials enable NPR lighting, so the
current generic indirect response is a partial reconstruction for that hero too.
Converted emission is retained, but this does not establish full Source material
or self-illumination equivalence.

The `.blend` packs lighting images. The export report retains the detailed lighting
package and assumptions; portable `.agrscene` packages omit derived hero-lighting
bindings so a later conversion rebuilds them from the selected installed assets.

For a stable local development setup, copy qualified helpers out of their build
directories with the project Python environment:

```text
.venv\Scripts\python.exe -m tools.prepare_lighting_helpers
  --world C:\built\world\WorldRegionProbe.dll
  --atlas C:\built\atlas\ProbeAtlasDecode.dll
  --cube C:\built\cube\CubemapDecode.dll
  --dotnet C:\dotnet\dotnet.exe
  --output C:\AGR-tools\SourceLighting-dev1
```

Enter the command on one line. The destination must be new. The tool verifies and
copies runtime companions into separate `world`, `atlas`, and `cube` folders,
preserving the identities used by conversion caches. `helper-kit.json` records
hashes and the exact CLI lighting arguments. Select those copied DLLs in the GUI.
Keep the explicit .NET 10 runtime path available; the kit does not bundle .NET.
This is a local development setup, not an update to preserved dev8 or a published
distribution. It contains no replay or game assets.

## Saved settings

On the first launch of this separate-map workflow, AGR turns off the previous automatic map setting once. Saved game, cache and map paths remain intact. If you explicitly enable map conversion afterward, AGR remembers that choice on later launches.

Blender reference: [Link & Append](https://docs.blender.org/manual/en/latest/files/linked_libraries/link_append.html).


## Experimental partial NPR sequence rendering

`render-npr` renders explicit frames from an already qualified generic hero-lighting or builder-prepared NPR-indirect
`.blend` and a prepared segment containing `hero_lighting`,
`npr_materials`, and explicit `npr_runtime` reference assumptions. This command
does not prepare those assets or export a replay. Normal conversion can prepare NPR indirect with the option below. Saved NPR inputs
are checked against entity/model selection, material interfaces and the current
camera bake before rim rendering. Arbitrary edited shader internals remain unqualified.

```powershell
.venv/Scripts/python.exe -m agr render-npr --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --scene "C:/path/GenericLighting.blend" --segment "C:/path/prepared-segment.json" --output "C:/path/new-render-directory" --frames 1 61 121 --partial
```

Each frame starts a fresh Blender worker, captures current depth, applies partial
NPR indirect/rim lighting and checks linear audit passes. The sequence stops on
worker failure, failed/mismatched receipts or changed output hashes. Inspect
`sequence-report.json`, per-frame `frame-report.json`, audit summaries and logs.
A successful frame retains its PNG and receipts; only eight scratch files made
by that invocation are removed after validation. Failed work remains for diagnosis.
Output must be a new directory on C:, with space for one audited frame plus
accumulating images/logs. No scene or release is overwritten.

Camera-forward primary, white instance color, exposure/DFAO/screen defaults,
direct-rim zero and equal-ramp reference behavior remain explicit assumptions.
Passing the audit does not prove full Source lighting or engine visual parity.


Normal conversion can opt into saved **partial NPR indirect** with
`--source-npr-lighting --source-hero-lighting --source-lighting --include-map --partial --shot-hero 65`
plus the existing map/decoder arguments. The GUI exposes the same optional setting.
This explicitly selects camera-forward primary and white-instance reference
assumptions. Material packages are prepared from the selected models; Blender
checks surface coverage and saves integer-frame primary animation. Export reports
`npr_lighting` as partial with rim not applied. This is not full Source rendering.

The sequence renderer accepts generic lighting and saved NPR-indirect builder scenes.
A changed camera or stale primary socket animation requires rebuilding before rendering.
Fresh installed-asset conversion and GUI layout still need end-to-end verification for this
experimental option. Existing releases do not include these development changes.

NPR conversion requires a fitted perspective hero camera (`--shot-hero`, or Follow camera hero ID in the GUI). The default orthographic overview is unqualified. A fresh full scene rebuild from the verified Priest assets has been saved/reopened with NPR indirect and all121primary samples intact; the rebuilt saved scene also passed audited frame61 rendering. Fresh installed-asset re-preparation remains a separate check.
