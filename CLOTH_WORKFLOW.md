# AGR 0.2.0 experimental surface cloth

Enable **Cloth / secondary motion (experimental)** with **Installed game**. Map conversion can remain off. AGR reads the installed character physics and skin weights, separates the supported garment surface, adds Blender native Cloth with animated body proxies, and bakes the selected frames. Successful bakes store their point cache in the scene so the saved result can be opened, scrubbed and rendered without AGR or external cache files. AGR is needed when using its rebake helper.

## Current coverage

This first release supports **Seven’s waist cloth only**. The supported model uses one 240-vertex simulation surface, plus a matching 240-vertex backface surface that follows the same simulation through Geometry Nodes. This keeps the two sides together instead of simulating them independently.

Seven’s sleeves, leather straps and cables, and other heroes’ garments remain unsupported. They keep their existing skinned animation. The report uses `baked_partial` for a successful waist bake with unsupported parts; this does not mean the export crashed. A failed native simulation is reported separately.

This option does not simulate Haze’s hoses or garments on other heroes. Check `VALIDATION.md` for recorded validation results and remaining limits.

## How the native simulation is built

Source FE control names and skin weights identify the waist faces. The importer preserves each face’s material, winding and UVs when separating cloth from the body. The garment’s attachment to the body and its source skin weights determine animated anchors. Open hems are not pinned solely because they are mesh boundaries; pin counts are recorded for each bake. The original rig action, every bone channel, root tracks and event timing remain unchanged.

Four low-resolution rounded capsules follow the torso, pelvis and thighs. Their radii are reduced to fit below the initial waist surface and animated seam, because the original control-point collision shapes intersect the rendered garment. The fit factors are recorded in the report. They are hidden from rendering and isolated in the cloth’s collision collection. The map is not required. These proxy shapes and Blender cloth settings are approximations; this is a newly computed simulation, not historical Source 2 cloth playback. Some clipping against the trousers remains possible. Self-collision, box collision, map collision and game-driven wind are absent.

The source objects are retained in a hidden backup collection. Their separated visible body and garment copies preserve the source visibility animation. Do not enable that backup collection in renders; it contains the unsplit originals for recovery.

## Add a death animation, then rebake

1. Save a separate copy, then add or edit the body/death animation, root motion or timing in Blender.
2. Set the intended scene frame range and FPS.
3. In **Scripting**, select **AGR_Rebake_Cloth.py** in the Text Editor and click **Run Script**. The same helper is supplied as `tools/Rebake_AGR_Cloth.py` in the AGR folder.
4. If prompted, select the DeadBlend program folder containing `Launch_DeadBlend.bat`.
5. Inspect **AGR_CLOTH_REBAKE_REPORT**, review the cloth, and save a new copy.

The helper recomputes only AGR’s owned cloth caches, using the existing fitted body proxies. It does not bake unrelated map or user simulations. It leaves the body action intact. When a rebake fails its surface checks, the failed cloth output is disabled and the waist returns to ordinary skinned motion; the report identifies the failure. Large pose changes may require manually adjusting those proxies. The old cache is not automatically restored, so save a copy before rebaking an edited scene.

A cloth bake is tied to the body animation, range and FPS used to compute it. Appending or linking a static map alone does not require rebaking. Cloth may intersect floors or scenery during a death because this workflow intentionally omits map collision. Adjust the animation or configure and rebake additional collision manually if a shot needs it; the default cloth collision collection contains only AGR’s fitted body proxies.

## Development: decompiled Dynamo proxy
The active development branch additionally accepts a prepared
`agr_decompiled_fe_proxy_v1` definition through an entity's existing
`secondary_motion_file`. It reconstructs the authored proxy, pins and collision
shapes, binds the FE-weighted garment in the rest pose, and stores the resulting
motion as shape keys. The ordinary CLI export path has been verified on Dynamo
for replay108605183 ticks15300-15420:380 simulation vertices,3305 affected garment
vertices,121frames. This remains `baked_partial`; other heroes are not validated.

Preparation is currently a research workflow (`research/prepare_fe_workflow_check.py`),
not automatic detection in the Installed game checkbox. The Seven rebake helper
does not rebake this proxy. Regenerate the export after editing animation, range
or FPS. There is no recorded pre-shot cloth state, self-collision or map collision.

### Version-matched local proxy cache
Installed game cloth preparation now also checks `prepared-cloth-v1` under the
selected asset cache. The validated Dynamo definition is registered in this
project's `research/asset-cache`. With that cache selected, the regular Cloth
option finds it automatically. `research/register_dynamo_cloth.py` reproduces
registration from the existing verified decompilation. Compiled model hash,
decoded physics, resource identity and definition integrity must match; updates
invalidate the entry. A cache miss does not claim support for a new layout.
A full Installed game export with this cache and `--material-repairs` has passed
saved-scene cloth and shader checks for the target121-frame Dynamo segment.
