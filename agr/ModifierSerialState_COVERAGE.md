# Raw modifier serial state

`ModifierSerialState.tla` models the complete synchronous fold in
`modifier_serial_state.fold_capture`, including serial identity, ordered writes,
snapshot exclusion, slot reuse, present-only merging, explicit removal,
reapplication and refusing an unqualified prefix. `fold_retained` first invokes
the bounded capture/envelope validator. No mutable global, persistent cache,
callback, retry, file publication or independent availability state was added.

The input protocol is the pinned Boon 0.15.0 `ModifierState::apply_delta` raw
state contract. The concrete replay comparison invokes that actual full-protobuf
merger, not a second header-merger written for the test. This does not make the
raw projection an `EffectiveModifierState` or a client particle lifetime oracle.
Duration/aura rules, owner and view visibility, activation/phase and shader
controls require their own evidence before an effect can render.

The adjacent model bounds three writes, two serials, two slots and one nullable
data field. It checks 504 states through depth 5. All six mutants must break the
named rules: snapshot admission, ending a serial on slot reuse, slot identity,
zero defaults, ignored removal and accepting a prefix after a missing serial.
Slots exist only as a checker witness for the obsolete design; the production
fold has no slot-to-owner state. Simplification removes that unnecessary state,
uses one serial dictionary, and rejects unknown input by an exception instead
of retaining a second failed/available state. This is a new component, so no
earlier production state count is asserted.

Run with the project's D: pinned tla-ts 0.0.8:

    node local-data/Tools/tla-skill/check.mjs agr\ModifierSerialState.tla

Tests cover full signon state before the output window, inclusive queries,
same-tick ordering, snapshot exclusion, omitted versus empty data, removal of
the correct serial after slot reuse, no stale fields after reapply, and unknown
serial/kind refusal. Digest and schema calculations are bounded pure validation,
not an additional lifecycle. Byte parsing and hashing are concretely tested.

The portable envelope now has a canonical `capture_sha256` as well as the exact
original sidecar byte digest. This binds the embedded object without requiring
the original pathname or whitespace. `validate_envelope` rechecks capture schema,
coverage, replay identity, requested inclusive range and both digest shapes. Old
helpers remain explicitly unavailable; earlier experimental available envelopes
without this parsed-payload digest must be regenerated. The native sidecar v1,
helper CLI and `.crav` format are unchanged. Parent producer/publication remains
covered by `native/src/ClientModifierWrites.tla`.

Integration also accepts a saved shot contained by the original capture, after
full envelope validation through `validate_view`. Original history/range/digests
remain unchanged; the default query ends at the selected shot end, and explicit
queries cannot read later state. Earlier signon queries remain possible. These
pure range checks introduce no new fold state. Concrete tests distinguish valid
contained views from expanded, inverted, mistyped or tampered ranges and verify
that future writes do not leak into the selected shot's result.
