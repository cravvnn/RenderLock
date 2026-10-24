# Add the downloaded map

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
4. The final `.blend` has two master collections: `RenderLockChar` for generated characters, cameras, lights and effects, and `RenderLockMap` for the appended map. Character poses, camera motion and existing visibility settings are retained.

The map picker remembers its location; disabling inclusion retains that location. Keep the downloaded library unchanged. Edit and cut the final exported scene instead. Requested map identity, hierarchy and placement are checked before the scene is published. A recognized map failure can offer **Continue without downloaded map**; it does not enable partial character animation or retry automatically.

## In Blender

1. Keep the viewport in **Solid** mode. Temporarily hide `RenderLockChar` while working on the map; avoid Material Preview while images are missing.
2. Install and use the separate [Deadlock Map Optimizer](https://github.com/Miruneko0/Deadlock-Map-Optimizer). Explicitly set its **Map** selector to **RenderLockMap**; hiding characters alone does not scope the tool. Cut to the needed area **before relinking textures**. For merged cell meshes, choose **Precise Cut** for a close boundary; **Entire Objects** can retain a much larger area. RenderLock does not install or run the optimizer.
3. Remove unused map materials/images after the cut (Blender's **File > Clean Up > Unused Data-Blocks**), then use **File > External Data > Find Missing Files** on the downloaded `Textures` folder. Save a backup first; cleanup can remove other intentionally unused data. Loading all map images before cutting reached about **19.8 GB process RAM** in the supplied Linux witness and was stopped before completion; this is an observation, not a memory upper bound.
4. Show `RenderLockChar` again and inspect the camera. A follow camera framed before map append can be behind a wall. The Linux test branch reports bounded post-append visibility samples without moving the camera; move it or adjust the cut yourself. Sampling is not a guarantee for every frame.
5. Relink the retained map textures before switching to **EEVEE**. The supplied Linux witness produced GPU texture errors and pink characters while missing map images were visible; hiding `RenderLockMap` or replacing its materials restored the character appearance. This does not establish corrupt character textures or a universal renderer defect. Cycles does not recover missing maps. Save the finished scene; pack relinked textures if needed, or keep their folder with it.

Appending can spend several minutes making the full map local. The Linux test branch announces this step and reports its duration separately; it does not claim an append speedup. Keep disk space for both the downloaded library and the larger combined scene, plus temporary files and backups. A short replay does not reduce full-map loading cost.

## Alignment and scope

RenderLock's world placement is `Rz(world_yaw) * (unit_scale * (SourceXYZ - origin))`. Standard replay exports use **0.0254 metres per Source unit**, zero origin and zero world yaw.

The supplied map retains its raw Blender glTF import basis: `(SourceY, -SourceX, SourceZ) * 0.0254`. RenderLock applies the existing **+90 degree Z correction**, then the scene's origin, scale and world yaw, exactly once. The map is not centered on a selected player. The character rig's separate 180-degree model correction is not applied to map geometry.

The library's basis was checked against three installed-world geometry anchors. This establishes the coordinate convention, not full visual parity or compatibility with every map revision. The provided snapshot has no authored game build identifier; walls, props or terrain can differ from an older replay or a later update. Inspect the exported shot.

The current download is the contributor's **24 October Midtown rebatched** snapshot. It has fewer objects than its own raw export, but the previous hosted map was already rebatched too, so this does not establish a speed improvement over that previous download. The package omits raw export/intermediate files and embedded texture copies verified byte-for-byte against its external PNGs. Its rebatching makes ambient fans, signs, bounce pads and similar props static; their authored animation is not replayed. Those limits are separate from recorded character animation.

The stable download entry point is [maps.allevilmc.com/download](https://maps.allevilmc.com/download). It points to the current versioned ZIP. Previously qualified, unchanged libraries already saved locally remain accepted by the refreshed build, with their own strict geometry inventory; that does not mean old map archives remain hosted.

Appending makes the scene larger and requires Blender to load the full map. Cutting it afterward is manual. Map geometry does not add recorded destruction, ragdolls, map collision for cloth, full Source materials or lighting data. Missing character animation is still a separate strict failure unless broad partial export was explicitly selected.

## CLI

Add `--external-map "path/to/RenderLockMap.blend"` to the normal `agr convert` command. Installed map geometry is omitted when using this option, while requested installed character-lighting data can still be prepared. The external map is reported separately in the coverage report. The reusable `.agrscene` does not embed this large library; choose it again when rebuilding the scene.

## Other map workflows

The development profile retains installed regional map conversion. The character beta omits that geometry conversion and can instead append the supported external library above. Installed lighting-region controls remain separate because the external `.blend` supplies no Source probe or NPR data.

Custom or edited map libraries can still be appended manually in Blender. Preserve their correct world placement and hierarchy. They are not admitted as the supported download by the automatic workflow.
