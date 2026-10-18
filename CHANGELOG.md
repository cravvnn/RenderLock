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
