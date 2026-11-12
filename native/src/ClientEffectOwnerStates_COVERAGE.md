# Owner state capture and shared publication

`ClientEffectOwnerStates.tla` covers the full additive capture lifecycle:
`main.rs` filename reservation, from-signon observation, bounded retention in
`Capture::push`, sticky failure, derived availability, exclusive publication,
flush/close failures and consumer admission after successful extraction. Missing
fields remain explicit raw data, not a failed semantic claim or a zero value.
The default model checks 852 states through depth 10; the existing-file model
checks 2 states. Thirteen guard-removal mutants must fail their named invariants.
An independent protocol witness records invalid identity/order/duplicate/budget
inputs, so deleting a guard cannot redefine the expected truth.

`client_modifier_writes::write_rows_new` is the one shared exclusive bounded
writer. Factoring the prior inline writer preserves its serialization and
close/flush behavior. Its old `ClientModifierWrites.tla` and existing concrete
writer test continue to cover that caller; this model covers the added caller.
There is no second publication status, retry loop, retained serializer cache or
mutable availability flag. Availability is derived from the scan and first
failure. The model's availability variable denotes that returned calculation.
This simplification avoids parallel flags that could disagree.
Integration adds checker witnesses for actor-callback progress and the frozen
failed prefix. They add no production state or baseline model states. Removing
either independence or the post-failure retention guard now breaks its invariant.

The owner model bounds three observations, two rows and small independent word
and serialized budgets. Production byte arithmetic, integer/array decoding and
hash correctness are checked concretely, not proved by that abstraction. The
fixed-array reader checks schema length/type and each public FieldPath index;
real extraction is compared with an independent wire-key inventory. It does
not flatten the duplicate display names. Python `replay_capture.py` provides
synchronous bounded read/schema-callback/provenance admission shared by both
internal sidecars; each caller retains its own fixed policy and validator.
The shared containing-capture view validator is a synchronous immutable check,
not another retention lifecycle. It validates the full original envelope/range
before checking shot containment; it never crops rows or rewrites digests.

Run the project's D: pinned tla-ts 0.0.8:

    node local-data/Tools/tla-skill/check.mjs native/src

The model does not cover all existing exporter lifecycles or infer semantic
owner visibility. Actor filtering, pose availability, masks, duration/aura,
view state, effect activation/endcaps, shaders and lighting require separate
qualification. See `agr/ClientEffectOwnerStates_CONTRACT.md`.
