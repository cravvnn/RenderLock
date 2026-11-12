# Explicit hitbox transport

`HitboxRuntimeInputs` parses complete immutable CP/model-bound shape histories,
then owns one sequential installed-frame cursor and one `HitboxFrameHistory`
per consuming `ParticleSystemState`. The simulation host calls `Advance` before
Start/Update. Inherited provider lookup passes the actual consuming state to
`Query`; children do not share their parent's collection-local clock.

`HitboxRuntimeInputs.tla` models Advance, consumer/CP routing, duplicate reuse
and current/previous publication. Two configurations exercise both independent
axes. Four guard mutants cover skipped input frames, consumer isolation, CP
isolation and duplicate refresh. `HitboxFrameHistory.tla` composes stable binding,
rewind rejection and initially empty previous poses, with four more mutants.
The synchronous immutable parse either returns a complete provider or throws;
there is no partially published file, worker, retry, loader callback or reset.

Simplification from the retained source: remove the stored `birth` member. The
already owned `frames` dictionary proves whether `active` is installed, so Query
uses one `TryGetValue` rather than a second sentinel fact and index lookup. The
models already abstract immutable birth/range validation; this changes no model
states and makes no measured state-count reduction claim. Dense input is bounded
to 10000 ticks and 200000 records, matching the new Python frame builder. Shape
array size is checked before materialization. An Int32.MaxValue endpoint refuses
before the inclusive tick loop can overflow.

The adjacent `HitboxFrameHistory.cs` is the canonical mirror of the namespaced
class carried in `tools/patches/vrf-explicit-hitbox-runtime.patch`; both helper
projects exclude the host mirror and use the dependency's type. Source checks
must preserve equality after transport line-ending normalization, retaining each
file's separate byte hash. The patch adds only hitbox types, sampling/locking,
an inherited provider and the LockToBone registration. It does not replace
snapshot code, authored root-CP writers, emitter batching or material parsing.
Stateless sampling, inherited delegate lookup and constructor input predicates
are checked by compiled-code oracles rather than a new lifecycle spec.

Only `ParticleSimulationProbe` defines `AGR_SIMULATION_HITBOX`. The unchanged
material parser build excludes the new code. Supplying hitbox fields to that
build is an explicit error. Requests without hitbox input retain their prior
output fields. The host acknowledges policy and bindings only for an installed,
fully parsed hitbox provider. Snapshot and hitbox histories cannot be combined.

Model bounds and isolated helper checks do not qualify engine acquisition,
native seed recovery, substeps, endcap/restart scheduling, arbitrary model inputs,
Blender rendering or a full effect. Silence creation admission/root CP3/4 writers
and ordinary-export pose acquisition remain separate integration gates. No
native reader or historical capture identity is widened by this component.
