# Linux test kit: 0.3.0b17.dev3

This is **experimental source**, not a self-contained Linux binary release or an
update to Windows b16. Extract it into a new folder beside your working copy.
Keep the old app, its native tools, caches and successful scenes unchanged.
Read [LINUX.md](LINUX.md) for dependencies and pending qualification gates.

## Reuse a working Linux installation

The launcher below borrows its Python environment and native replay extractor,
and selects its managed model extractor when present (via `AGR_MODEL_EXTRACTOR`). New Python source comes from this kit. Lighting/particle simulation helper locations stay
explicit, so there is no copying of Windows binaries or silent replacement of
the old installation. No dependency download or game process is required.

```sh
cd /path/to/RenderLock-0.3.0b17.dev3-Linux-test-source
OLD="$HOME/Applications/RenderLock"
python3 tools/run_linux_test.py --tools-from "$OLD" -- --help
```

For a fresh installation, follow the native source setup in [LINUX.md](LINUX.md)
instead. Source archives omit the developer-only lighting helper projects; use
the full branch checkout if rebuilding those projects.

## First test: strict short range, cloth off, Standard look

Use a new output filename and the same asset cache as the old installation.
Replace the paths and hero/range below with a completed local replay. This Haze
range comes from the supplied community witness; it is not a universal fixture.

```sh
GAME="$HOME/.local/share/Steam/steamapps/common/Deadlock"
mkdir -p "$HOME/blender_work/renderlock-linux-test"
python3 tools/run_linux_test.py --tools-from "$OLD" -- convert \
  --demo "$GAME/game/citadel/addons/replays/119001653.dem" \
  --game-install "$GAME" \
  --source2viewer "$OLD/tools/source2viewer/Source2Viewer-CLI" \
  --asset-cache /path/to/existing/asset-cache \
  --no-effects --heroes 13 --shot-hero 13 --start-tick 30000 --end-tick 30002 --fps 64 \
  --blender "$HOME/.local/bin/blender" \
  --output "$HOME/blender_work/renderlock-linux-test/haze-standard.blend"
```

Do not add `--partial` to make a failing control appear successful. This first
test does not request cloth, effects or Source lighting. If it fails, retain the
support ZIP and log; report the first failure before trying other features.

Run the saved-scene verifier with your Blender executable and this kit's script:

```sh
blender --background --factory-startup --disable-autoexec --python-exit-code 1 \
  --python tools/verify_saved_scene.py -- \
  --blend "$HOME/blender_work/renderlock-linux-test/haze-standard.blend" \
  --output "$HOME/blender_work/renderlock-linux-test/haze-standard-verify.json" --all-frames
```

The verifier separates the character and map texture contracts. Character
material images must be packed, or the result is `FAIL` (exit 1). A scene with
an external map keeps its map textures external until you cut and relink them,
so those images are reported as declared limits and the result is `PARTIAL`
(exit 2). Pass `--external-map-images` when you deliberately keep the map
textures external and want the verifier to accept that instead of returning
`PARTIAL`; character images are still required to be packed.

## Map follow-up

Repeat the short control with `--external-map /path/to/RenderLockMap.blend` and a
different output filename. For a later timing comparison, reuse the supplied
321-frame range only after the short control succeeds. Preserve both reports;
compare map counts/alignment, character poses and separate map timings.

- The log now announces loading and localization **before** those long steps.
  Localization can still take several minutes; this kit claims no speedup.
- After a successful append the export also applies three map-scene settings,
  recorded under `map_scene` in the scene report (and `map_scene` warnings
  appear in the summary):
  - **EEVEE shadow pool raised to 2048 MiB** (only when the scene had a lower
    value). About 6,500 map meshes overflow the 512 MiB default and Blender
    silently drops shadows without saying so in a normal viewport. Lower it in
    **Render Settings > Shadows** if GPU memory is tight on a small card.
  - **The review grid is hidden.** `Reference grid (not map geometry)` is an
    88 m curve; with the real map appended it would render as a grey grid in
    characters-only passes.
  - **A `RenderLockChar` view layer excludes `RenderLockMap`.** Use it for
    viewport and material-preview work on characters. Excluding a collection
    is cheaper than per-object `hide_render` and skips evaluation. It does
    **not** free memory: an excluded 38.9 M-vertex map is still resident, so
    characters-only memory only drops if the map is in a separate file.
- A generated hero-follow shot gets a bounded visibility recheck after append.
  `camera.external_map_visibility` records the samples, including
  `first_obstructed_frame`, the nearest `hit_object` and `hit_distance_m`. An
  obstruction warning means move the camera or cut the map; no camera keys are
  changed automatically. Sampled-clear does not guarantee every frame is
  unobstructed: a 3-frame control samples frames 1, 2 and 3 only, so a clear
  short range does not prove a longer export of the same start tick is clear.
- Cut in **Solid** view first. Use the external optimizer's **Map = RenderLockMap**
  and **Precise Cut**. Remove unused map materials/images after cutting, then
  relink the retained textures. See [MAP_WORKFLOW.md](MAP_WORKFLOW.md).
- Missing map images cause EEVEE GPU texture errors and pink **map props**.
  Packed character textures are unaffected; the earlier witness report that
  described a pink character was a magenta map mannequin prop. Hide
  `RenderLockMap` to confirm the character separately. Resolve the retained map
  textures before EEVEE material preview/rendering; Cycles does not repair
  missing images.

## Optional follow-ups and what to return

After strict controls, test cloth with separate cloth-only consent. A verifier
result with declared cloth exclusions is `PARTIAL`/exit 2; it is not a full pose
PASS. Test effects and lighting in separate jobs with explicit helper paths and
their own limits. Unknown Linux NPR decoder/native pairs remain refused; this
kit does **not** adopt the community's hash/`None` bypass or local patches.

Return the exact command, app version, OS/Blender/.NET/tool identities, support
ZIP (if failed), export/verification reports, timings and one representative
render. For map pink/occlusion issues, include map-visible and map-hidden views.
Do not send private credentials or the entire map/replay unless requested.

These changes were checked with focused Python tests and small background
Blender fixtures on Windows. Native Linux export, Wayland UI, full-map memory,
EEVEE behavior and fresh-machine setup still need this test round.
