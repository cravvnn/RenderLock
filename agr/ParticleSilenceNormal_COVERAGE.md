# Silence ordinary-export input bridge (source candidate)

`recorded_effect_simulation.build_recorded_effects` dispatches only the exact
Silence resource to `recorded_silence_effects.prepare_package`. No observation
means unavailable. The new schema 2 observation file carries both **original
bound** envelopes; `bind_runtime_observations` validates them against the exact
scene without rebinding or changing their fingerprints/native identities.
Schema 1's Crossbow reader/binder behavior is preserved by the runtime adapter.
Both paths take one bounded file snapshot. The binding batch returns all or none.

The existing `--particle-observations` option (also available in Advanced) accepts
schema 2: the same `format: "renderlock-particle-observations"` envelope with
`schema_version: 2` and `captures: [{instance_id, profile: "silence", birth, child}]`.
`birth` and `child` are complete original bound packets, not raw captures or file
paths. They must match the exact replay/scene/model/history. Schema 2 does not
mix profiles or bind an old packet to a newly generated scene. The existing
`prepare-particle-observations` command still prepares schema 1 Crossbow reports.
Fresh-scene equivalence for historical Silence packets remains an explicit
input proof, not a permissive importer option. Schema 3 adds `source_scene` to
each schema-2 record. The importer validates both original envelopes against
that source witness and creates a separate `particle_silence_binding` proof.
Packets are never rebound. Equal inclusive shot range, replay, exact recorded
instance/events, owner basis, model/skeleton bytes, complete root samples and
all q/t/s/fresh pose rows from birth through end are required. Earlier warmup
outside the consumed span may differ; no consumed motion can differ. The only
nonidentical preparation dependency admitted is the separately audited
effect_assets source/target pair already used by retained slide binding. All
constraint definitions, hierarchy and attachment data remain exact.

This bridge is a pure bounded snapshot comparison and immutable proof builder,
without persistent state/cache/retry/handler, so it needs no additional lifecycle
model. ZIP member/NPY headers are bounded and typed before array allocation.
Repeated admission recomputes the proof against source and target files. Its
target identity binds the fresh scene independently; original birth/child hashes
and original native profiles remain distinct. Newly evaluated Blender poses and
saved recapture are still mandatory. Numeric/source equivalence cannot establish
the output image or current native acquisition.

`particle_render_controls.render_contexts` keeps the generic v1/v2/v3 gate.
Authored-root v4 snapshots require an explicit `(package, scene_data)` pair,
the exact simulation object from that package, and full Silence package
validation before any snapshot is indexed. The package renderer supplies this
pair only after ordinary package and saved-rig validation. No bare policy name,
boolean flag, copied simulation or unrelated effect can authorize root inputs.
This is another pure admission check, with no new lifecycle/cache/token state.

`blender_silence_inputs.capture_points` reuses `evaluated_snapshot_rig` and
`capture_model_bones`, selecting only authored hitbox bones. Full tick history,
model order, positive uniform scale and finite poses remain mandatory. CP0 uses
the replay root's declared render basis, CP1 its explicit parent-body attachment,
and CP2 the recorded literals. No fallback is selected/invented and no unrelated
cloth bone is made rigid. `SnapshotRigEvaluation.tla` covers the existing single
borrow/finally protocol and its saved-input recapture caller. Numeric matrices,
bone identity and availability are separate checks.

`ModelHitboxInventory.tla`, beside the existing C# producer, covers the fresh
inventory output/collector protocol. It preserves occupied files, rejects failed
writes even if complete bytes remain, and requires validated metadata. The
collector owns no retry/cache/cleanup manager. Its caller's unique artifact dir
contains partial diagnostics on failure. Actual sources and helper hashes are
rechecked. A failed log's exclusive creation also fails the preparation.

The new observation binding, request assembly/validation and hitbox conversion
are pure bounded operations: no additional persistent state, lock, clock or
handler requires a separate lifecycle model. Package preparation uses the
existing synchronous per-instance helper and existing package render transaction.
Canonical request identity retains original events/creation, both observations,
selected hitbox frames and substitute seed. Normal packages recapture the saved
rig and CP/hitbox inputs **before geometry allocation**, refusing changed motion.
The legacy prepared-package route keeps its prior admission rules; it is not
retroactively marked as fresh ordinary-export evidence.

Simplification: reuse one scene-state borrow and existing all-or-none importer,
exclusive helper writer and package transaction. No old cross-effect wrapper,
second pose cache, automatic live reader, lifecycle manager or alternate clock
is added. Component checks cannot establish ordinary export/save/render acceptance.
This is an unaccepted source candidate until fresh normal export, saved/rollback
and short Cycles checks (including prior features/flashbang) pass.
