ProjectileCapture.tla models the additive extraction/retention contract from
main's signon callbacks through exclusive publication and consumer admission.
One identity and three ticks cover a pre-shot row, owner selection changes,
absence/reappearance, row overflow, incomplete scan and output races/failures.
The concrete implementation additionally bounds 50,000 identities, 4,096 tracks,
4,096 rows per track, 500,000 total rows and 256 MiB of serialized JSON.

Capture retains every selected track from signon, including already completed
pre-shot tracks. Avoiding pruning removes the old shot-crop defect without a
new tombstone/eviction protocol. Subsequent unselected/changed-owner samples stay
visible to provider validation. Earlier unknown/unselected history remains absent;
its first-observed tick is preserved so the provider can refuse that prefix.
Any capture guard error propagates out of the callback and main; no continuation
or retry follows a failed capture. Concrete Rust tests check field rows, gaps,
deletion, budgets and exclusive/bounded writing. Python validates immutable
bounded JSON plus replay/helper/range/raw-sidecar/canonical payload digests.
Those synchronous calculations introduce no additional lifecycle model.

The existing animation/clock/marker output interfaces and command signature are
unchanged. No manager CREATE, native visibility, interpolation, orientation
override, exact native birth or post-deletion effect lifetime is inferred.
Observed entity absence is not independently qualified native endcap scheduling.
