# Shared hitbox frame history

`HitboxFrameHistory.Query` owns one immutable frame per control point and per
owning history. It validates identity and monotonic collection time before
publishing. The first previous frame is empty. The first query at a time supplies
the current shapes; repeated queries return that same object even if a later
consumer asks for another set or weighting. Binding changes and rewinds refuse
without modifying history. A new simulation requires a new history.

The adjacent model checks two CPs, two identities and three times. Four mutants
remove binding, monotonicity, duplicate-reuse and empty-first-history rules.
`HitboxRuntimeInputs` is the normal helper caller, composing its own consumer/CP
routing model with this one. The private/compiled oracles exercise the same
class, but are not a renderer or native-clock proof. Source-frame payloads are
abstracted in this model; actual geometry/math/finite checks require code tests.

The dependency patch carries the identical source/model beside the compiled
class. Helper projects exclude this mirror to prevent duplicate definitions.
Publication takes prebuilt immutable value records synchronously, so no pending
callback, separate prepare/commit state, worker or recovery owner is introduced.
No further history-state simplification was selected: current/previous, binding
and time each retain an independent observable obligation. Native acquisition,
Blender export, material rendering and full Silence remain outside this model.
