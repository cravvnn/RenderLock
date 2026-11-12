# Pending Source-rest snapshot provider foundation

This component is not accepted normal-export Crossbow support. The current
candidate leaves normal creation/native profile admission unchanged. Existing
rendered r10 support remains the accepted source milestone.

`particle_model_rest_assets.collect_model_rest` calls the separate geometry
helper's `--inventory-model-rest` command. The command reads typed compiled
model-order parent positions, rotations and scales, not imported GLB or NM rest.
Its fresh exclusive output is accepted only after successful process exit,
bounded single-snapshot parsing, source/bone-order/parent checks and helper/parser
identity recheck. No fallback, cache, retry, worker or native runtime selection
is introduced. Partial or occupied outputs are never accepted as cached success.

`ModelRestInventory.tla` models that producer/reader protocol, including a
competing output creator and failure during serialization/flush. Its fresh and
existing configurations check19 and4 states. Three unique guard mutants cover
exclusive creation, failed-write rejection and metadata identity. They do not
prove parsing, matrix arithmetic, filesystem crash durability or game ownership.
The design reuses the existing helper and caller-owned stage, without adding a
second publication/cache manager. The smaller original writer-only model is
extended only with the collector's explicit validation predicate.

`particle_model_rest` and `particle_snapshot_matrices` are pure bounded input
validation/affine transformations with no persistent state. They preserve exact
model order, parent hierarchy, scale and supplied quaternion, without silent
normalization. Missing, cyclic, nonfinite or mismatched rest data refuses.

`capture_source_snapshot_provider` uses the existing borrowed-frame
`capture_model_bones` operation, whose finally block restores the caller's frame.
An explicit selected bone list retains the complete model layout but leaves
unselected rows unavailable (NaN internally); only the finite selected row is
serialized. The default all-bone path retains its original strict scale/finite
checks. The earlier imported-rest experimental function is preserved and clearly
separated from the Source-rest path. No frame handler or new animation clock is
added. The enclosing existing package transaction owns Blender data allocation.

Immutable provider packets bind replay/range/rig/model/bone order/rest/events
and queried response identities. Reload re-evaluates the selected saved rig
motion. A content digest is an identity check, not authentication against a user
rewriting both source data and its claimed digest. Native creation and CP/model
selection require their own evidence before these inputs can enable an effect.

Current evidence separates these boundaries:

- Current installed snapshot types retain19 initializer and3 operator fields.
  Named vtables and chained-unwind ownership bind the prepared body slices.
 21 scalar constructor defaults,624 row-prefix choices,144 matrix cases,
 84 initializer cases and20 rigid-loop cases/140 particles pass. Acquisition,
  full constructors, emission context and scheduling remain outside these checks.
- The separate helper reads all227 current Priest rest rows exactly as the
  earlier typed inventory. The parser remains the separately qualified geometry
  parser. New invalid-name/existing-output guards and all prior geometry command
  checks pass; the static sphere GLB remains byte-exact to r10.
- The retained original210-frame rig recaptures the exact earlier provider;
  save/reopen, edited-motion refusal and frame restoration pass. This is retained
  source/rig component evidence, not fresh runtime owner acquisition.
- An asset-free actual Blender fixture builds and independently reopens at
 24/30/60FPS. Its authored rest deliberately differs from Blender rest; all nine
  Source matrices follow the authored basis. Edited motion and incomplete history
  refuse; all-bone capture still rejects nonuniform cloth, while an explicit
  selected bone never fabricates a cloth transform.

The simulation-only dependency now registers the narrow initializer/operator and
the shared host's conditional named-bone transport. A real Release build passes
with the pinned upstream configuration. The registered paths match28 current
native initializer rows and100 update rows (maximum3.8146973e-6 Source units),
plus18 lifecycle checks and23 refusals. The actual input reader checks3 queries
and29 invalid cases, including matrix length before materialization. The current
installed eight-system tree runs with six synthetic poses:528 position pairs
agree within2.635743e-5 Source units. This is supplied-pose composition evidence,
not replay acquisition. The accepted r10 flashbang request and every simulation
output value remain exact. The shared material host separately compiles against
its unchanged parser; the new simulation patch never reaches that dependency.

Adjacent transport, particle-storage and two cursor configurations check18,
218,49 and36 states respectively;16 unique guard mutants fail as expected.
The host uses the existing single tick loop and existing simulator arrays,
without a second clock, per-particle side dictionary or cache. The model chain
follows parse/advance/query/ack into immutable particle ownership, initial copy,
compaction and slot reuse; pure matrix arithmetic remains a numerical obligation.
The enclosing normal package transaction is not yet connected to this provider,
so these component models do not establish whole-export acceptance.

Remaining integration gates are current CP0 owner/model and original
mode3-to-mode1 creation qualification,
complete normal request/package validation, remaining Crossbow rope inputs,
and fresh normal-export/saved-scene/Cycles acceptance. No old native capture or
hash-only widening can replace these gates. Silence, other hitbox providers,
Source lighting and map reconstruction remain separate.
