# Panorama override: the "Loading shaders" card

These five files are compiled by `tools/build_ui_override.py` into
`native/assets/ui/pak02_dir.vpk`, which the launcher hash-checks and mounts in
native-camera sessions only.

## Status (2026-10-28): sources redesigned, pack not rebuilt

The sources hold Kara's redesigned card (Figma card [design reference] on screen [design reference],
icon boil [design reference]). The shipped `pak02_dir.vpk` was **not** rebuilt: compiling
needs Valve's `resourcecompiler.exe` from a Source 2 SDK, and none was used on the
machine where the redesign was made. The pack still holds the old card (500 px,
teal top border, "DEADLOCK DOLLY", 34 px title), pinned as
`0af30912a471efc984e653ebc57a4c05f210e07f707f42d3bec997fc945b2fe1`. Until it is
rebuilt and re-pinned, the game shows the old card.

## What changed

- `layout/citadel_prewarm_assets_status.xml`: `Details` now holds the brand label
  "Deadlock Dolly" (was "DEADLOCK DOLLY"), a `DollyTitleRow` with the icon slot
  (`IconContainer` > `Icon`, moved here) and the "Loading shaders" title, then the
  unchanged description. A `DollyChalkClip` > `DollyChalk` panel follows `Details`.
  Root classes (`StatusNotification DollyLoadingProbeV2`), `hittest` and the
  title/description texts are unchanged; no ids.
- `layout/hud_hideout.xml`: only the `PrewarmStatus` width changed, 500px to 420px
  (still centred, `position: 0px 16% 0px`).
- `styles/citadel_prewarm_assets_status.css`: the stock rules at the top are
  untouched; the `DollyLoadingProbeV2` rules are the new card below.
- `styles/citadel_client_status.css`, `styles/citadel_hud_and_db_overlay.css`:
  unchanged.

The card, as built (1920 x 1080 reference):

| Part | Value |
|---|---|
| Card | 420 x 112, `#0f1012`, 1 px `#29292d` border, radius 6, shadow `#00000073 0px 8px 24px 0px` |
| Place | centred, top edge at 16 % of the hideout HUD height (173 px at 1080p) |
| Content | 21 px in from every edge: brand 16 + 6 + title row 26 + 6 + description 16 = 112 |
| Brand | "Deadlock Dolly", `sans` 12 px medium, `#a6a6a6` |
| Icon | Familiar "helpers" ability icon (Figma: "Familiar sticker icon"), `s2r://panorama/images/hud/abilities/familiar/familiar_helpers_psd.vtex`, 26 x 26, `wash-color: none` |
| Title | "Loading shaders", `oracle` (VALVE Oracle) 21 px semi-bold, `#ffffff`, 5 px after the icon |
| Description | "Your replay will open automatically.", `sans` 12 px regular, `#a6a6a6` |
| Chalk | `s2r://panorama/images/hideout/chalk_doodle07_psd.vtex`, opacity 0.04, right-hand crop in a 124.5 x 215 frame tilted 5.75 degrees anticlockwise, clipped by the card |
| Boil | while `.Prewarming` only: the icon holds a pose for 800 ms, then snaps (0.01 % = 0.5 ms keyframe step, no tween) to the next, poses 0 > 3 > 1 > 5 > 2 > 4 on a 4.8 s loop; nudges up to 0.5 px, tilts up to 0.5 degrees (toned down at Kara's request; RenderLock's boil steps every 500 ms) |

Inter is not in the game, so the labels use the stock `sans` (Retail Demo).
The values above describe the intended source design.

## Rebuild

Needs a Source 2 SDK in the reviewed "Reduced CSDK 12" layout:
`game/citadel/gameinfo.gi`, the compiler at `game/bin_cs2/win64/resourcecompiler.exe`
(or pass `--compiler`), and `game/citadel` exported from the current Deadlock
`pak01_dir.vpk`. The card's two images are in the current game (checked
2026-10-28); if the compiler reports them missing, re-export `panorama/images`.

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py tools\build_ui_override.py --sdk "D:\deadlock 3d\Reduced_CSDK_12"
```

It compiles the five sources into `<SDK>\game\citadel_addons\dolly_capture`,
refuses a pack without the `ClientServerDebugStats`, `GameLogoIcon`,
`DollyLoadingProbeV2`, `DollyFamiliarBoil` or `DollyChalk` rules, writes
`native\assets\ui\pak02_dir.vpk`, and prints each resource's sha256 and then
`sha256 <pack hash>`.

## Re-pin

Update `UI_OVERRIDE_PACK_SHA256` in `dolly/launcher.py` to the new pack
hash printed by the builder. Review the resource hashes in the build output and
verify the rebuilt pack in a native-camera session using the checklist below.

Run the core source regression suite from the checkout root:

```powershell
& $py -m unittest discover -s tests -q
```

These headless source tests do not exercise Panorama or qualify the rebuilt pack;
the in-game check is needed to assess the actual integration.

## In-game checklist (native-camera session)

- [ ] **Appearance timing:** the card shows only while Deadlock shows its own
  shader-prewarm status (the engine's `Prewarming` class), at the same moments as
  the old card, and hides when prewarming ends. It sits in the hideout HUD's
  `MainContents`, so like before it fades in with the HUD (1 s, once a hero is
  set, not during the intro).
- [ ] **Size and position:** 420 x 112 at 1920 x 1080 (Panorama scales it with the
  resolution), centred horizontally, top edge at 16 % of the screen height
  (173 px at 1080p). Nothing inside is clipped.
- [ ] **Box:** near-black `#0f1012`, 1 px `#29292d` border, 6 px corners.
- [ ] **Body font:** "Deadlock Dolly" and "Your replay will open automatically."
  in the stock sans (Retail Demo), 12 px grey; "Loading shaders" in VALVE Oracle
  21 px semi-bold white; nothing in capitals.
- [ ] **Familiar icon colours:** the icon shows its own colours, not washed
  white or grey like stock HUD icons; 26 x 26, left of the title, 5 px gap,
  level with the title line.
- [ ] **Boil:** every 0.8 s the icon jumps to another held pose, a tiny shift
  (0.5 px or less) and tilt (0.5 degrees or less); it never slides or wobbles
  between poses (no tween); the six poses repeat every 4.8 s and start from rest
  each time the card appears.
- [ ] **Chalk doodle:** the hideout chalk doodle, barely visible (4 %), at the
  card's right end, tilted, cut off at the card's edges (nothing outside the card).
- [ ] **Shadow:** a soft dark shadow below the card (0 8 24, black at 45 %).
- [ ] **Still hidden:** the Deadlock mark of the client status (`GameLogoIcon`)
  and the match/server debug stats (`ClientServerDebugStats`).

If anything is off, change these sources (never the pack), rebuild and re-pin.
