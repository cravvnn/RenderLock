# Add the downloaded map

[All guides](docs/README.md) · [Troubleshooting](docs/TROUBLESHOOTING.md#the-map-is-missing-pink-or-too-heavy)

The map is a separate download, outside the RenderLock application ZIP. RenderLock appends and aligns it in the final replay scene. The refreshed **0.3.0b20** build also connects the current download's textures automatically. Map cutting remains manual.

Automatic inclusion is available from **0.3.0b16**. The current download is about
2.60 GB; extract the whole ZIP, not just its `.blend`. Use the
[published Windows build](https://github.com/cravvnn/RenderLock/releases/latest)
or matching current source. Older versions without this workflow require manual
Blender appending; they do not gain support by changing a saved library path.

## In RenderLock

1. Open **Map** and select **Download map**. Extract the entire ZIP once. Keep `RenderLockMap.blend`, `map-package.json` and the `Textures` folder together.
2. Choose the extracted `RenderLockMap.blend`, then enable **Include downloaded map**. Use Blender 5.2 or newer; Blender 5.2.1 LTS is the tested installation.
3. Export the selected characters and range as usual. The map does not require enabling Source lighting, NPR or Elagrimm. Those optional looks still need their own installed lighting inputs and explicit lighting consent.
4. The final `.blend` has master collections `RenderLockChar` for generated characters, cameras, lights and effects, and `RenderLockMap` for the appended map. Character poses, camera motion and existing visibility settings are retained.

To make the viewport lighter, optionally enable **Simplified map in viewport** on Map before exporting. RenderLock creates an untextured, approximate `RenderLockProxy` collection, disables the full map in viewports and keeps the proxy out of renders. The original `RenderLockMap` is still used for the render, including its shadows and reflections. This option currently supports the current downloaded Midtown map. It adds preparation time and makes the scene file larger: the full map is still loaded, so this is not a fix for running out of memory while opening the file. Small details and surfaces may differ in the proxy; check framing against the full map before a final render.

The map picker remembers its location; disabling inclusion retains that location. Keep the downloaded library unchanged. Edit and cut the final exported scene instead. Requested map identity, hierarchy and placement are checked before the scene is published. A recognized map failure can offer **Continue without downloaded map**; it does not enable partial character animation or retry automatically.

## In Blender

1. With the refreshed b20 build, the current map's image files connect automatically. Keep the extracted `Textures` folder in place: map images stay external to avoid adding about 1.34 GB to every scene. Before moving a scene to another computer, use **File > External Data > Pack Resources**, then save, or use the repair tool's packing option below. The simplified viewport proxy stays intentionally untextured; check the full map in Material Preview or Rendered mode.
2. Temporarily hide `RenderLockChar` while working on the map.
3. Install and use the separate [Deadlock Map Optimizer](https://github.com/Miruneko0/Deadlock-Map-Optimizer). Explicitly set its **Map** selector to **RenderLockMap**; hiding the characters alone does not scope the tool. Use its zone-box or camera workflow to keep the needed area. For the merged cell meshes, choose **Precise Cut** when you need a close boundary; **Entire Objects** can keep or discard a much larger area. RenderLock does not install or run this tool.
4. Show `RenderLockChar` again and save. Pack the relinked map textures yourself if you need a self-contained file, or distribute the texture folder with it.

### Repair an existing scene

You do not need to export the replay again or append another copy of the map.

1. Open your existing exported `.blend` in Blender. Use **Save As** first if you want a separate repaired copy.
2. Switch one editor to **Text Editor**, choose **Open**, and open RenderLock's `agr/blender_map_textures.py` (the separately supplied `Repair_RenderLock_Map_Textures.py` is the same script).
3. Choose **Run Script**. In the file picker, choose the unchanged `RenderLockMap.blend` from the extracted current map download. Enable **Pack repaired textures into scene** if you want the map images embedded; leave it off to retain external files.
4. Save the scene after the success message. Return to your usual workspace and use Material Preview or Rendered mode to inspect the full map.

The tool repairs missing image references in `RenderLockMap`, including nested material groups. It preserves already packed images and existing working texture replacements. It copies images shared with non-map data before changing their paths; shared materials or node groups must be made single-user first. It does not replace geometry, change UVs or move the map or characters. Its packing option packs repaired images and already connected images from this exact package; an artist's working replacement files stay unchanged.

If the extracted folder moved, run the repair again using its new location. Missing or changed package files are reported before any image paths change. Keep the downloaded library unchanged; run repair on the exported replay scene. The tool supports the current 24 October map package. For a previously qualified older map, or a custom library, use **File > External Data > Find Missing Files** and select its matching texture folder, then save. Automatic relinking does not add shaders, textures or material behavior absent from the supplied map.

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
