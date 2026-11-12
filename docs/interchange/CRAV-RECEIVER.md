# Local .crav preflight

This receiver handles the existing camera/replay profile. It is not the requested
captured-scene importer; see [the captured-scene direction](CRAV-CAPTURED-SCENE.md).
Successful preflight must not promote camera observations to captured scene data.

`python -m agr plan-crav --package shot.crav --demo local.dem`
checks a reconstruction-profile collection against the actual local replay and
prints a plan. Omit `--demo` to use the package's relative location hint. Repeat
`--shot ID` to select shots; selection preserves document order and each shot's
own FPS. No replay, media or installed assets are copied into the collection.

Preflight streams SHA-256 with bounded reads, decodes replay metadata through
the pinned replay reader, and checks SHA, map, build, tick rate, total ticks and
any declared match ID. It rechecks the collection and replay after decoding.
An input change, missing file, mismatched identity or cancellation refuses the
plan. Cancellation is checked between read chunks and around the decoder; it
cannot interrupt a decoder call internally. Files are observed, not locked into
an immutable snapshot. Extraction must recheck identity when it consumes them.

## Explicit full prepared map

`--map-package FOLDER` names a complete prepared RenderLock map
(`map-package.json`, currently Midtown `dl_midtown`). Every declared byte is
verified before the replay is decoded, and the package identity is rechecked
afterwards. The map is the caller's choice: the replay header label (`start` in
current replays) is reported as `replay_header_map` and never selects a map.

A full prepared map contains every converted map object, so it is a superset of
any `camera-path` crop and margin; the plan reports `crop: none`. The collection's
`include_lighting_dependencies` requirement needs packaged `lightmaps/` or
`volumes/` files, otherwise preflight refuses. With a map, the pending
whole-path map-selection item becomes *capture-time map content identity*: the
v1 `.crav` does not pin which installed map content was loaded during capture,
so today's package is not proven to match it. Lighting fidelity remains part of
saved-scene and visual verification.

The result is `planned`. It does not export a scene or satisfy the pending
installed-asset, capture-time map content, camera-first scene or
saved-scene/visual gates. The older camera-only importer continues to refuse a
reconstruction-profile collection. A successful plan must not bypass that gate.

## Implementation coverage

| Component | Verification and limits |
| --- | --- |
| `preflight_reconstruction` / CLI `plan-crav` | Adjacent `agr/CravPreflight.tla` models one read-only call, permanent file changes (including an optional prepared map), decoder identity result and cancellation. Five guard mutants. Real-file tests cover changed replay/package, explicit relocation, wrong SHA before decode, metadata mismatch, cancellation, a broken map refused before decoding and map files edited/removed during decoding. |
| `map_plan` | Pure; requires packaged lighting files when lighting dependencies are requested. Map byte verification is the existing `map_package.load_package` contract. |
| `_stable_digest` | Bounded per-call stream and context-managed handle; real-byte hash/stat checks. Path ctime is excluded from handle comparisons because Windows APIs can report different timestamp kinds. Hashing, decoder internals, hostile edit-and-restore and later file changes are outside the finite model. |
| `plan_reconstruction` | Pure validation and deep-copied job construction; no retained state. Tests cover identity, shot selection, mixed FPS and fractional extraction endpoints. |
| Dolly capture and publication | Separate repository's `CravCapture`, `CameraOnlyCapture` and existing `ExportCleanup` models; this receiver does not model or replace those owners. |
| Full scene reconstruction | Pending integration and qualification. No whole-program, visual parity, liveness or crash-recovery proof. |

The preflight uses no cache, reservation, retained job owner or output publisher.
The finite model has 168 states (56 before the optional map was added); it
removes no previous production states and makes no state-reduction claim. Run it with the pinned `tla-ts` directory checker
on `agr`; the five expected mutants must violate their named invariants.
