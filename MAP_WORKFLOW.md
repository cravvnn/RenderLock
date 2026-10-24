# Add the downloaded map

The map is a separate download, outside the RenderLock application ZIP. RenderLock appends and aligns it in the final replay scene. Texture relinking and map cutting remain manual.

## In RenderLock

1. Open **Map** and select **Download map**. Extract the ZIP once and retain its `Textures` folder.
2. Choose the extracted `RenderLockMap.blend`, then enable **Include downloaded map**. Use Blender 5.2 or newer; Blender 5.2.1 LTS is the tested installation.
3. Export the selected characters and range as usual. The map does not require enabling Source lighting, NPR or Elagrimm. Those optional looks still need their own installed lighting inputs and explicit lighting consent.
4. The final `.blend` has two master collections: `RenderLockChar` for generated characters, cameras, lights and effects, and `RenderLockMap` for the appended map. Character poses, camera motion and existing visibility settings are retained.

The map picker remembers its location; disabling inclusion retains that location. Keep the downloaded library unchanged. Edit and cut the final exported scene instead. Requested map identity, hierarchy and placement are checked before the scene is published. A recognized map failure can offer **Continue without downloaded map**; it does not enable partial character animation or retry automatically.

## In Blender

1. Use **File > External Data > Find Missing Files** and select the downloaded `Textures` folder. Map textures remain external and are not automatically relinked or packed by RenderLock.
2. Temporarily hide `RenderLockChar` while working on the map.
3. Install and use the separate [Deadlock Map Optimizer](https://github.com/Miruneko0/Deadlock-Map-Optimizer). Explicitly set its **Map** selector to **RenderLockMap**; hiding the characters alone does not scope the tool. Use its zone-box or camera workflow to keep the needed area. For the merged cell meshes, choose **Precise Cut** when you need a close boundary; **Entire Objects** can keep or discard a much larger area. RenderLock does not install or run this tool.
4. Show `RenderLockChar` again and save. Pack the relinked map textures yourself if you need a self-contained file, or distribute the texture folder with it.

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
