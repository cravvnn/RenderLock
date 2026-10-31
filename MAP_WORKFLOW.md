# Full maps and a lighter viewport

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

<a id="repair-an-existing-scene"></a>

## Repair an older exported scene

You do not need to export the replay again or append another copy of the map.

1. Open your existing exported `.blend` in Blender. Use **Save As** first if you want a separate repaired copy.
2. Switch one editor to **Text Editor**, choose **Open**, and open RenderLock's `agr/blender_map_textures.py` (the separately supplied `Repair_RenderLock_Map_Textures.py` is the same script).
3. Choose **Run Script**. In the file picker, choose the unchanged `RenderLockMap.blend` from the extracted 24 October map download. Enable **Pack repaired textures into scene** if you want the map images embedded; leave it off to retain external files.
4. Save the scene after the success message. Return to your usual workspace and use Material Preview or Rendered mode to inspect the full map.

The tool repairs missing image references in `RenderLockMap`, including nested material groups. It preserves already packed images and existing working texture replacements. It copies images shared with non-map data before changing their paths; shared materials or node groups must be made single-user first. It does not replace geometry, change UVs or move the map or characters. Its packing option packs repaired images and already connected images from this exact package; an artist's working replacement files stay unchanged.

If the extracted folder moved, run the repair again using its new location. Missing or changed package files are reported before any image paths change. Keep the downloaded library unchanged; run repair on the exported replay scene. The tool supports the 24 October map package. For a previously qualified older map, or a custom library, use **File > External Data > Find Missing Files** and select its matching texture folder, then save. Automatic relinking does not add shaders, textures or material behavior absent from the supplied map.

If a scene has `RenderLockProxy`, switch back to the full map while editing or cutting it: show the monitor restriction toggle in the Outliner's Filter menu, then click the monitor beside `RenderLockMap` to show the full map and the monitor beside `RenderLockProxy` to hide the proxy. The proxy is a snapshot and does not automatically follow cuts or other edits to the map. After cutting, remove the outdated proxy and work with the full map. A new export creates a proxy for the original downloaded map, before any manual cuts. The proxy's camera icon remains disabled so it does not render.
