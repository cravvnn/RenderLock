ProjectileCapture.tla models the additive extraction/retention contract from
main's signon callbacks through exclusive publication and consumer admission.
One identity and four ticks cover a pre-shot row, owner selection changes,
absence/reappearance, row overflow, incomplete scan and output races/failures.
The concrete implementation additionally bounds 50,000 identities, 4,096 tracks,
4,096 rows per track, 500,000 total rows and 256 MiB of serialized JSON.

Capture retains every selected track from signon, including already completed
pre-shot tracks. Avoiding pruning removes the old shot-crop defect without a
new tombstone/eviction protocol. Subsequent unselected/changed-owner samples stay
visible to provider validation. Earlier unknown/unselected history remains absent;
its first-observed tick is preserved so the provider can refuse that prefix.
An optional capture guard error discards the entire partial payload and disables
all future capture callbacks, while actor extraction continues. This terminal
state replaces propagation through the core actor callback; it does not retry.
Only two bounded new reason values are admitted, with zero tracks/rows required
for a failed capture. A complete observed empty scan remains available.
Exact serialized-size counting precedes file creation; data-budget overflow uses
the same discard state. Real output races, writes, flush/sync and core actor/parser
errors still fail the extraction. Consumer merge propagates unavailable coverage,
so existing strict completion refuses it even when manager objects succeeded.
Explicit partial export preserves those objects and the unavailable reason. Concrete Rust tests check field rows, gaps,
deletion, budgets and exclusive/bounded writing. Python validates immutable
bounded JSON plus replay/helper/range/raw-sidecar/canonical payload digests.
Those synchronous calculations introduce no additional lifecycle model.

The existing animation/clock/marker output interfaces and command signature are
unchanged. No manager CREATE, native visibility, interpolation, orientation
override, exact native birth or post-deletion effect lifetime is inferred.
Observed entity absence is not independently qualified native endcap scheduling.

The first isolation model had229states at three observations. Aligning discarded
internal state with the code reduced it to165; a surviving no-retry mutant showed
three ticks could fail only at the final observation. Four ticks now cover the
post-failure callback:246states,10guard mutants all killed. The existing-output
variant has2states. This model abstracts typed field contents and OS errors;
concrete Rust/Python checks, compilation and fresh replay comparison remain
separate requirements. Removing capture failure propagation preserves all actor
and strict particle obligations; it does not enlarge support or suppress errors
as successful empty captures.
