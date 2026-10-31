# Full maps and a lighter viewport

Experimental Linux source: native Linux execution and rendering still need testing.

[All guides](docs/README.md) · [Troubleshooting](docs/TROUBLESHOOTING.md#the-map-is-missing-pink-or-too-heavy)

New Midtown maps use **Blender 5.2 LTS** and the improved material, lighting and
map preparation workflow. Choose a ready-made map from the existing host or build
the same kind of package from your installed Deadlock files. Both produce local,
editable map geometry in the final replay scene. The map stays separate from the
RenderLock application download.

## Choose your map

1. In **Setup**, select Blender 5.2 LTS and a generated asset cache outside the game folder. For local building on Windows, use a short path such as `D:\RenderLockCache`.
2. Open **Map**. Choose either:
   - **Download map**: download from the host, extract the entire ZIP, then select its `RenderLockMap.blend`. Keep its `map-package.json`, `textures`, `lightmaps` and `volumes` together. The host can still serve an older compatible map until its download is replaced.
   - **Build from game**: select your installed Deadlock folder in Setup, then press this button. The first build downloads pinned map tools and prepares the full map and viewport proxy. Allow at least 32 GB of free working space and substantial free RAM. Do not update the game while building. A replay is not required for this step.
3. Enable **Include map** and export your selected characters and range as usual. The simplified viewport defaults on when you first choose an upgraded map; afterward your choice is remembered. The CLI also defaults to the cached proxy for new prepared maps.

The local build currently supports **Midtown (`dl_midtown`)**, base game content,
Windows x64 and Linux x64 tool downloads. Native Linux building still needs its
own qualification. New maps are qualified on Blender 5.2.x; older maps retain
their existing compatibility path. Building reads installed assets without
launching the game or modifying its files. Prepared packages are reused between
exports; a changed game or builder selects a different cache entry. An interrupted
import can reuse earlier stages only after all their recorded file hashes pass.

## Exported scene and textures

RenderLock opens the prepared map and brings the smaller generated replay scene
into it. You get editable master collections **RenderLockMap**, **RenderLockChar**
and **RenderLockProxy**, with no linked map-library dependency. Character
animation, camera and packed character textures are checked during composition.
The map does not require enabling optional Source character lighting or Elagrimm;
those looks keep their own prerequisites and explicit lighting consent.

New exports copy the map's external images and volume data into
**RenderLockMapAssets beside the output scene**. Paths are relative to the project;
the same map's verified files are reused by other exports in that output folder.
Move that folder together with your `.blend`. Do not rename or separate its files.
Character textures remain packed. If you edit a managed map texture, keep the edit
separately or choose a fresh output folder for the next export: RenderLock will
report the changed file instead of overwriting your work.

Keep the original prepared package unchanged. Edit and cut the final exported
scene. Existing older libraries remain selectable; their external texture and
repair rules below still apply. Selecting an older library does not upgrade its
geometry or shaders.

## Simplified map in viewport

For new maps, the package already contains an approximate, untextured proxy.
**Simplified map in viewport** shows it while editing and hides the full map in
viewports. Renders use the full textured map, including off-camera shadows and
reflections; the proxy has rendering disabled. Uncheck the option before export
to work with the full map immediately.

This reduces the geometry drawn while editing. It does not remove the full map
from the file or eliminate its RAM requirements. Small features and surfaces can
differ; inspect the full map before final framing. There is no automatic deletion
of objects outside the camera.

In Blender, expose the Outliner's monitor restriction toggles. Show the monitor
beside **RenderLockMap** and hide the one beside **RenderLockProxy** to inspect or
edit the full map. Keep the proxy's camera icon disabled. A proxy is a snapshot:
after cutting or editing the map, remove the outdated proxy and use the full map.
The separate [Deadlock Map Optimizer](https://github.com/Miruneko0/Deadlock-Map-Optimizer)
can help with manual cuts; explicitly select **RenderLockMap**, and use **Precise
Cut** for a close boundary. RenderLock does not install or run that add-on.

## Alignment and limits

Standard replay exports use **0.0254 metres per Source unit**. The raw map's
Blender glTF basis receives the existing +90 degree Z correction once, followed
by scene origin and world yaw. Map lighting directions, reflections, sky and fog
are transported into that basis as well. New prepared lighting currently requires
this standard metre scale; changing origin/yaw is supported. The character rig's
separate model correction is not applied to map geometry.

These are static snapshots. Map revisions and ambient prop motion may differ
from the replay. Materials, baked lighting, probes, reflections and fog are
reconstructed approximations, not full game-render parity. The proxy does not
establish a frame-rate guarantee. Heavy renders still need considerable memory.

The builder is the separately executed, pinned
[Sirsyorrz/deadlockrigs map pipeline](https://github.com/Sirsyorrz/deadlockrigs/tree/0d1958ab553630df997a93fda06d3ab9ed6994c3),
with its original source and GPL notices retained. See [credits](CREDITS.md) and
[third-party notices](THIRD_PARTY.md).

## Older map libraries


[All guides](docs/README.md) · [Troubleshooting](docs/TROUBLESHOOTING.md#the-map-is-missing-pink-or-too-heavy)

The map is a separate download, outside the RenderLock application ZIP. RenderLock appends and aligns it in the final replay scene. Texture relinking and map cutting remain manual.

Automatic inclusion is available from **0.3.0b16**. The current download is about
2.60 GB; extract the whole ZIP, not just its `.blend`. Use the
[published Windows build](https://github.com/cravvnn/RenderLock/releases/latest)
or matching current source. Older versions without this workflow require manual
Blender appending; they do not gain support by changing a saved library path.

## In RenderLock

1. Open **Map** and select **Download map**. Extract the ZIP once and retain its `Textures` folder.
2. Choose the extracted `RenderLockMap.blend`, then enable **Include downloaded map**. Use Blender 5.2 or newer; Blender 5.2.1 LTS is the tested installation.
3. Export the selected characters and range as usual. The map does not require enabling Source lighting, NPR or Elagrimm. Those optional looks still need their own installed lighting inputs and explicit lighting consent.
4. The final `.blend` has master collections `RenderLockChar` for generated characters, cameras, lights and effects, and `RenderLockMap` for the appended map. Character poses, camera motion and existing visibility settings are retained.

To make the viewport lighter, optionally enable **Simplified map in viewport** on Map before exporting. RenderLock creates an untextured, approximate `RenderLockProxy` collection, disables the full map in viewports and keeps the proxy out of renders. The original `RenderLockMap` is still used for the render, including its shadows and reflections. This option currently supports the current downloaded Midtown map. It adds preparation time and makes the scene file larger: the full map is still loaded, so this is not a fix for running out of memory while opening the file. Small details and surfaces may differ in the proxy; check framing against the full map before a final render.

The map picker remembers its location; disabling inclusion retains that location. Keep the downloaded library unchanged. Edit and cut the final exported scene instead. Requested map identity, hierarchy and placement are checked before the scene is published. A recognized map failure can offer **Continue without downloaded map**; it does not enable partial character animation or retry automatically.

## In Blender

1. Keep the viewport in **Solid** mode. Temporarily hide `RenderLockChar` while working on the map; avoid Material Preview while images are missing.
2. Install and use the separate [Deadlock Map Optimizer](https://github.com/Miruneko0/Deadlock-Map-Optimizer). Explicitly set its **Map** selector to **RenderLockMap**; hiding characters alone does not scope the tool. Cut to the needed area **before relinking textures**. For merged cell meshes, choose **Precise Cut** for a close boundary; **Entire Objects** can retain a much larger area. RenderLock does not install or run the optimizer.
3. Remove unused map materials/images after the cut (Blender's **File > Clean Up > Unused Data-Blocks**), then use **File > External Data > Find Missing Files** on the downloaded `Textures` folder. Save a backup first; cleanup can remove other intentionally unused data. Loading all map images before cutting reached about **19.8 GB process RAM** in the supplied Linux witness and was stopped before completion; this is an observation, not a memory upper bound.
4. Show `RenderLockChar` again and inspect the camera. A follow camera framed before map append can be behind a wall. The Linux test branch reports bounded post-append visibility samples without moving the camera, including the first obstructed frame and the nearest blocking object and distance, and it also samples any non-generated active camera. Move it or adjust the cut yourself. Sampling is not a guarantee for every frame, and a short export samples only its own frames.
5. Relink the retained map textures before switching to **EEVEE**. The supplied Linux witness produced GPU texture errors and pink map props while missing map images were visible; the packed character stayed fully textured, and hiding `RenderLockMap` or replacing its materials removed the pink props. This does not establish corrupt character textures or a universal renderer defect. Cycles does not recover missing maps. Save the finished scene; pack relinked textures if needed, or keep their folder with it.

Appending can spend several minutes making the full map local. The Linux test branch announces this step and reports its duration separately; it does not claim an append speedup. Keep disk space for both the downloaded library and the larger combined scene, plus temporary files and backups. A short replay does not reduce full-map loading cost.

The export raises EEVEE's shadow pool to 2048 MiB when a map is present, because the default 512 MiB overflows across thousands of map meshes and Blender drops shadows without reporting it. It also hides the review grid and adds a `RenderLockChar` view layer that excludes `RenderLockMap` for characters-only work. That exclusion is a viewport/render convenience, not a memory fix: excluded map data stays resident. Only keeping the map in a separate file lowers character-scene memory.

If a scene has `RenderLockProxy`, switch back to the full map while editing or cutting it: show the monitor restriction toggle in the Outliner's Filter menu, then click the monitor beside `RenderLockMap` to show the full map and the monitor beside `RenderLockProxy` to hide the proxy. The proxy is a snapshot and does not automatically follow cuts or other edits to the map. After cutting, remove the outdated proxy and work with the full map. A new export creates a proxy for the original downloaded map, before any manual cuts. The proxy's camera icon remains disabled so it does not render.

## Alignment and scope

RenderLock's world placement is `Rz(world_yaw) * (unit_scale * (SourceXYZ - origin))`. Standard replay exports use **0.0254 metres per Source unit**, zero origin and zero world yaw.

The supplied map retains its raw Blender glTF import basis: `(SourceY, -SourceX, SourceZ) * 0.0254`. RenderLock applies the existing **+90 degree Z correction**, then the scene's origin, scale and world yaw, exactly once. The map is not centered on a selected player. The character rig's separate 180-degree model correction is not applied to map geometry.

The library's basis was checked against three installed-world geometry anchors. This establishes the coordinate convention, not full visual parity or compatibility with every map revision. The provided snapshot has no authored game build identifier; walls, props or terrain can differ from an older replay or a later update. Inspect the exported shot.

The current download is the contributor's **24 October Midtown rebatched** snapshot. It has fewer objects than its own raw export, but the previous hosted map was already rebatched too, so this does not establish a speed improvement over that previous download. The package omits raw export/intermediate files and embedded texture copies verified byte-for-byte against its external PNGs. Its rebatching makes ambient fans, signs, bounce pads and similar props static; their authored animation is not replayed. Those limits are separate from recorded character animation.

The stable download entry point is [maps.allevilmc.com/download](https://maps.allevilmc.com/download). It points to the current versioned ZIP. Previously qualified, unchanged libraries already saved locally remain accepted by the refreshed build, with their own strict geometry inventory; that does not mean old map archives remain hosted.

Appending makes the scene larger and requires Blender to load the full map. Cutting it afterward is manual. Map geometry does not add recorded destruction, ragdolls, map collision for cloth, full Source materials or lighting data. Missing character animation is still a separate strict failure unless broad partial export was explicitly selected.

## CLI

Add `--external-map "path/to/RenderLockMap.blend"` to the normal `agr convert` command. Installed map geometry is omitted when using this option, while requested installed character-lighting data can still be prepared. The external map is reported separately in the coverage report. The reusable `.agrscene` does not embed this large library; choose it again when rebuilding the scene.

Add `--map-viewport-proxy` alongside `--external-map` to request the simplified viewport collection. The coverage report records the proxy separately from the full map.

## Other map workflows

The development profile retains installed regional map conversion. The character beta omits that geometry conversion and can instead append the supported external library above. Installed lighting-region controls remain separate because the external `.blend` supplies no Source probe or NPR data.

Custom or edited map libraries can still be appended manually in Blender. Preserve their correct world placement and hierarchy. They are not admitted as the supported download by the automatic workflow.
