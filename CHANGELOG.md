# 0.3.0b13 - Character names and material setup

- Give actor collections, replay roots and imported meshes, armatures and empties canonical hero names with actor IDs and separate model-track labels. Preserve original object names, bone/material identities, rig selection and the shared root across character transformations. Generated cloth helpers retain their utility names.
- Explain the separate qualified Elagrimm library download, required lighting/follow-camera settings and embedded material/node-group output inside Lighting lab. Add a matching RenderLock-styled button to the supported library revision.
- Clarify the existing optional static emission and glass repair setting and its limits. This release does not change emission defaults, shader-append behavior or normal-map pixels; the reported normal-shading discrepancy remains under investigation.

# 0.3.0b12 - Export recovery and support ZIPs

- Save a bounded, verified failed-export support ZIP beside the normal logs, including early and final-save errors. Show its location in a branded recovery window with specific, user-selected retry choices.
- Add separate consent to keep supported approximate cloth and report unavailable or failed cloth while character models and animation remain strict. Recognized optional-feature failures suggest relevant settings; partial character animation remains an explicit choice.
- Fix the undefined attachment key that could interrupt otherwise supported particle-layer preparation. Existing unsupported cloth operations and particle limitations remain reported.

# 0.3.0b11 - Faster update checks and export timing

- Add a read-only installed-game check (`agr compat`) that reports the game build, probe atlas/cubemap layout, and whether character, NPR and static-map lighting resources match the qualified identities. Character-only readiness is reported separately from static map lighting, so a later map-content update does not look like a character export failure.

- Run the installed-game compatibility check before model conversion when character lighting or NPR is requested. A changed shader or texture layout now stops with the exact resource reason before a long model/animation job instead of failing later.

- Record per-stage timings in the export log, the saved report and `failure.json`, and in the Blender scene report. This makes it clear whether preparation, pose baking, lighting, NPR, cloth or effects is the long pole.

# 0.3.0b10 - October game update: light probes and NPR

- Read the installed light-probe atlas and cubemap layout from their VTEX headers instead of pinning the map/world hash, so a map-content update no longer stops hero Source lighting with "World probe layout requires requalification". Six-face atlas depth, cubemap depth, format, region bounds and resource identities are still checked. Legacy packages keep their recorded layouts.

- Accept the 6801 cubemap array depth in the bundled cubemap decoder (verified against VRF on the installed 256x256x171 array) and record the installed probe shader identity in the plan.

- Requalify the 6801 pbr shader for NPR material inputs and authored albedo defaults after confirming the complete 261-name material variable interface and the seven static defaults are identical to the previous shader. Older saved NPR packages remain readable.

# 0.3.0b9 - Transformation lighting fix and export diagnostics

- Fix a failed Blender scene build after a recorded character transformation when Source hero lighting or NPR materials were enabled; each form now keeps its own visible surface and probe selection instead of aborting the export. Strict and explicit partial rules are unchanged.
- Reuse the surface coverage already verified in the same scene build when applying NPR, instead of scanning every exported frame twice; saved-scene validation still verifies from scratch.
- Keep the Blender scene-build log beside the retained diagnostic files so a failed export includes the exact error, and copy its tail into failure.json.

# 0.3.0b8 - Character transformations and UI polish

- Integrate Kara's portrait-based character selection, recorded-team trays, accessible follow-camera placement, wrapping notes, copy controls and matching update/recovery artwork; retain all replay heroes and existing strict/partial export controls.
- Remove missing-reset-pose warnings from a cropped clip when the unavailable tick is outside its selected range; retain the original diagnostics as source provenance.
- Keep pose-availability metadata with retained particle history, so complete selected body animation can be reused without hiding missing historical inputs. Particle attachments still refuse incomplete history, and a reset-only selected range still refuses export.
- Preserve each movement sample's full recorded model resource ID, including character transformations; slicing a package into another form clears the previous form's derived model and rig bindings.
- Installed-game exports separate recorded model runs into their own geometry, skeleton and pose tracks under one pawn root and follow camera. Saved-package inspection and slicing preserve one player identity; no character is removed from selection.
- Keep strict refusal when a model reset has no recorded pose. Explicit partial export can retain both forms, hide the unavailable sample and identify its exact tick in the report. Particle model/attachment providers across model changes remain unsupported; the first form is never substituted for another.
- Match an unambiguous ASCII-case difference between animation and model bone names only when their named parents agree; retain these bindings in the pose report.

# 0.3.0b7 — Update checks and character preview lighting

- Fix completed update checks attempting to configure a canvas text ID as a widget; automatic and manual checks now update the redesigned sidebar without a callback exception.
- Stage the scene, reusable package and report before replacing outputs; save failures restore the previous files and retain diagnostics. A temporary-file cleanup error no longer reports a successfully saved scene as failed.
- Keep the editable key/fill preview lights enabled for the Elagrimm character look, so partial indirect lighting is supplemented by the artist shader's direct-light response.
- Place and animate follow-shot preview lights relative to the selected character and camera for clearer, consistent character lighting. These remain editable artistic Blender lights; Source direct lighting and shadows are incomplete.
- Select follow-camera heroes by their actual inspected names, with Overview and stale-input checks; retain the full replay roster and existing hero ID/settings/CLI behavior.
- Support the qualified two-target point/orientation, parent and single-influence attachment rules used by the retained current-model regression. Preserve native-disabled missing-slave rules explicitly in reports; strict replay/asset identity and unsupported-mode checks remain.
- Preserve typed installed physics values used by supported cloth preparation, and accept explicit captured replay-time intervals in the shot backend. Full cloth, particle and NPR/runtime parity remains incomplete and reported.

# 0.3.0b6 — Redesigned UI, automatic cloth and lighting checks
- Integrate Kara's redesigned interface with the existing charcoal, cream and brass palette, RenderLock branding, character selection and in-app updates.
- Reconstruct supported painted cloth proxies automatically from installed assets. Saved-scene checks cover McGinnis and Fencer across all 301 frames of the tester's replay; other cloth layouts remain explicitly unsupported. This is approximate Blender cloth, not recorded Source cloth motion.
- Resolve omitted neutral albedo controls from the exact qualified pbr shader defaults. Bebop's partial NPR/Elagrimm export now saves without requiring those defaults to be written explicitly in each material. Explicit unsupported settings still fail.
- Check lighting-region probe count and both decoder budgets before optional map conversion. Explain installed map lighting data versus scene geometry and how to reduce an oversized character selection. Map-wide NPR and the tester's unsupported particle trees remain unsupported.

# 0.3.0b5 — Elagrimm character shader

- Replace the supported character-look library with Elagrimm's Deadlock shader; update the Lighting lab field and setup instructions. Toilet Horse Shoe is no longer supported.
- Bind converted diffuse, normal and ORM inputs, preserve converted emission, and adapt the author's group for partial Source lighting and artistic rim. The qualified GPL-3.0 library remains a separate download.
- Explain that game updates do not update rigs stored in older replays. A mismatched replay still requires compatible assets or a compatible newer replay; strict animation checks remain unchanged.

# 0.3.0b4 — In-app updates and consistent branding

- Use RenderLock's shared charcoal, cream and brass theme in the standalone installer and recovery window.
- Show the RenderLock logo, window/taskbar icon, styled title bar and matching progress line throughout update and recovery.
- Keep update failures inside the branded helper rather than opening a default-looking error dialog.
- Add the visible logo to the main navigation rail and share window branding with the helper.

- Add an Updates page with published release notes, optional startup checks and manual checks.
- Download only after a click; verify the GitHub ZIP digest and package manifest before offering Install and restart.
- Keep per-user settings, caches and unowned files; back up replaced app files, check startup, and roll back failed installs.
- Recover interrupted installs through an independent runtime; prevent another portable session from overlapping an update.
- Support beta version ordering and published prereleases without exposing draft releases or overwriting source checkouts.

# 0.3.0b3 — Character checklist

- Inspect replay or saved-package characters and choose named checkboxes with All / None controls.
- Read the compact recorded-player roster offline without loading per-tick tables or downloading hero catalogs; retain unknown hero IDs.
- Reset selections for a different input, discard stale inspection results, and reject an empty selection or an excluded follow-camera hero before export.
- Keep manual hero IDs available in Advanced, with strict export and compatibility checks unchanged.

# 0.3.0b2 — UI responsiveness, shared assets and compatibility diagnostics

- Reduce repeated resize layout and decorative redraw work while retaining the interface design.
- Rename the bone-constraint option to Model bone rules and explain how supported aim, follow and twist rules supplement animation separately from cloth.
- Read verified character models and textures directly from the shared cache across exports and tick ranges; cache material repairs separately and show reuse in the log.
- Explain skeleton compatibility failures with bones versus masks, counts, the first differing index/name and resource/tick context. Preserve structured diagnostics in failed and partial exports; strict rig validation remains unchanged.
- Include the credits and changelog in portable packages, and the contributing/workflow documents in source archives.

# 0.3.0b1 — RenderLock character beta

- Rename the product to RenderLock (window, sidebar, docs, launchers, package names).
- New interface: rounded card layout, raised navigation rail, DPI-aware scaling and a matching title bar.
- New native RenderLock.exe launcher (no console; clear dialogs on failure); the command script stays as a fallback.
- App icon from the project artwork: the window, the taskbar and the launcher executable use the RenderLock emblem; sources live in `artwork/`.
- Cloth: the experimental native resimulation (Seven waist and the Haze hose prototype) and its rebake helper were retired; the cloth option now uses version-matched decompiled proxy definitions.
- Bundle Source 2 Viewer 20.1, the character-look helper kit, the particle helper and a .NET runtime into the Windows portable; testers need only Blender and the game.
- New Map tab for the installed map region used by the lighting tools; the Lighting lab keeps the character-look options.
- Experimental character look enabled in the beta: partial Source lighting, probe reflections and the Toilet Horse Shoe shader (partial/artistic; limits listed in the coverage report).
- Armatures are hidden for a clean preview (unhide to inspect or pose); renders are unchanged.
- Particle/reliability fixes: source-tick visibility and light timing at any output FPS, conservative expiry, cropped saved packages keep only in-range effects.
- ValveResourceFormat 20.1 adoption and requalified probe layouts for the current game build.

# 0.2.1 ? current replay compatibility repair

- Update Boon to 0.15.0 and rebuild the Windows replay extractor.
- Correct new replay coordinates to WORLD_HALF 32768.
- Read AG2 V3 binary topology and dynamic values without lossy string conversion or stale vector indices.
- Decode topology dependencies using the recorded task registry; preserve dynamic capacity separately from used bytes.
- Accept only unique same-index ASCII-case mask aliases.
- Evaluate observed cached-pose writes/reads within an animation context; reject missing writes explicitly.
- Match the installed IK solver's short-segment early return, verified with bounded binary checks.
- Fix Windows launcher resolution with current-directory executable search disabled.
- Validate 12 animated players over 121 frames in replay 108605183; retain explicit particle/cloth limitations.
- Remove the obsolete bundled Linux helper; this release's native binary is Windows-only.

# 0.2.0 — separate maps and experimental Seven waist cloth

- Default map conversion off, migrate the old preference once, and preserve paths/cache.
- Add a reusable map Link/Append guide and retain optional installed map conversion.
- Add native Seven waist-cloth preparation from installed FE identifiers and skin weights.
- Use one 240-vertex native cloth surface and a matching 240-vertex backface sharing its simulated motion, with surface acceptance checks.
- Preserve original body actions, every rig bone track, mesh face materials and UVs.
- Add an AGR-only native cloth rebake helper after animation, root motion, frame-range or FPS edits.
- Report completed, partial, unsupported and failed cloth separately, per hero and surface. Sleeves, straps and other heroes’ garments remain unsupported.
- Move automatic ragdolls/map collision out of the active scope.

# 0.1.9 — map DATA text parsing

- Add a strict map-specific KV3 reader and cached DATA adapter.
- Preserve core model/animation/particle converter identities and cache entries.
- Retain failed map DATA text with useful parse locations for diagnosis.
- Distinguish failed map stages from supported partial effects in completion messages.
- Clarify that unsupported particle counts include earlier replay history.

# 0.1.8 — map dependency and particle resource fixes

- Correct particle resource indexing for the installed path-to-archive-entry inventory.
- Recover unresolved particle names from IDs retained in 0.1.7 saved packages.
- Stage entity-property model references and child entity-lump dependencies for map export.
- Preserve preparation failures in final coverage and explain empty particle output.
- Keep model/animation converters, caches and recorded hero tracks unchanged.

# 0.1.7 — installed scene content

- Correct glTF bone-helper visibility without changing model meshes.
- Decode recorded particle messages and compile supported installed effect layers.
- Add explicit installed-world conversion, regional cropping and Blender placement.
- Add independent optional-stage coverage and durable map failure diagnostics.
- Keep core model/animation cache identities and source-integrity checks.
- Include a guarded development hose prototype; public cloth simulation remains withheld.

# 0.1.6 Alpha

- Supply a complete runtime-source replacement for the reported null-byte startup failure.
- Check release source SHA256, lengths and Python syntax before AGR imports; name each failed file in the startup log.
- Validate Python directly from final release ZIP bytes and verify archived files against their manifest before publishing the package.
- Preserve the 0.1.5 animation fixes, installed-asset cache format, settings and Python environment.
- The recovered 0.1.5 ZIPs are valid; the cause of the user's extracted-source damage remains unknown.

# 0.1.5 Alpha

- Accept uniquely identifiable same-index ASCII-case bone aliases; log and report the exact names while keeping installed rig spelling and strict mask order.
- Skip and report absent authoring bone weights in masks before feathering, following the reference BoneMask constructor. Reject malformed counts/weights.
- Implement observed FollowBone mode 2 for McGinnis using the supplied client routine's component and coordinate-space behavior; unknown modes still fail.
- Preserve the converter identity and completed model/clip caches.
- Add regression coverage for identity collisions, actual renames, reordered masks, and missing-bone mask expansion.

# 0.1.4 Alpha

- Fixed false VPK bounds errors for preload-only and empty entries. Unused offsets are no longer validated as payload locations.
- Preserved CRC and actual-payload bounds checks; added detailed archive-entry error context.
- Added ten regression cases and an actual-resource fixture index check.
- Game paths, cache, animation, cameras and working Windows launcher behavior remain unchanged.

# 0.1.3 Alpha

- Added installed Steam game discovery, read-only VPK resources, verified conversion cache, and replay-ID model binding.
- Prepared only the recorded animation clips needed by the selected range, retaining graph/skeleton checks.
- Added Installed game / Prepared assets GUI selection; retained existing launchers and single overview camera.
- Added explicit static generated-cloth reporting and installed weighted-bone checks.
- Fixed symlink-preserving Blender Python launch for local validation.
- No game assets bundled; map/effect/physics/cloth application stages remain pending.

# 0.1.2 alpha

- Fixed the confirmed startup bug: Python Install Manager returned -1610612730, but `if errorlevel 1` does not catch negative values. Batch launchers now accept only zero as success.
- Failed Python launcher probes now continue to other Python candidates. Added common direct-install paths and optional AGR_PYTHON_EXE for a custom python.exe location.
- Disabled automatic runtime installation during discovery. If no runtime is found, logs include the installed-runtime list and Python command paths.
- Python 3.12.14 satisfies the requirement; placing AGR on a different drive from Python is supported. Actual discovery on the user's Windows installation still needs the next run.
- Added Windows-only cmd regression cases for the observed negative error, positive failure, and command-not-found. These cases are skipped on Linux; no Windows execution claim is made.

# 0.1.1 alpha

- Startup now saves a diagnostic log and keeps the console open after the desktop exits, including an unexpected successful exit with no visible window.
- A small Windows batch wrapper captures the separate worker's errors. Python setup uses standard-library code and an isolated local environment. Checks distinguish missing 64-bit Python, Tcl/Tk, dependency installation, and GUI failures.
- GUI startup and conversion messages are copied into the launcher log; callback tracebacks are retained. CLI propagates GUI startup failure exit codes.
- Blank Hero IDs selects every hero in the range. Asset availability no longer silently filters the roster to four heroes.
- Missing models are listed before strict export; explicit partial mode retains those player tracks as placeholders.
- Export creates exactly one static overview camera, encompassing all selected trajectories. Player follow cameras and imported asset cameras are removed.

The included asset pack still contains four hero models. Real replay validation confirmed 12 player tracks, four modeled heroes, eight movement placeholders, and one camera. Separate scene validation confirmed 12 independent model rigs using repeated supported assets. These checks do not establish 12 distinct hero asset/animation support.

The original Windows startup failure is not yet diagnosed. Send logs/AGR_startup.log from the new launcher if startup still fails.
