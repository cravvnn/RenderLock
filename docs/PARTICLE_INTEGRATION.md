# Particle integration candidate

This source candidate adds outward Mina bat projectiles to normal recorded-effect
exports. It includes network history retention, strict owner/request/capture
binding, animated model geometry and the sprite/rope layers used by the tracer. It also connects captured scalar
controls and collection-age texture curves used by Venator crossbow muzzle flashes.
It is based on the b29 public source; it is not a new published application build.

## Runtime layout

Keep three independent helper directories:

- `tools/particles/ParticleProbe.dll`: existing material/physics helper and its
  separately qualified material parser. Preserve the parser identity enforced by
  `agr/model_material_groups.py`.
- `tools/particle_simulation/ParticleSimulationProbe.dll`: upgraded simulation
  helper with its isolated pinned simulation dependency.
- `tools/particle_geometry/ParticleGeometryProbe.dll`: geometry-only helper with
  its isolated morph-export dependency.

Build the geometry and simulation projects under `research/` with their documented
dependency properties. `tools/codeql_build_helpers.py` records the distinct pinned
sources and patch sets. Do not replace the material parser with either new parser.
Include the updated native `bin/agr-extract` executable too. An older extractor
can still provide actor data, but cannot establish projectile capture coverage.

The GUI prefers the new simulation helper when bundled. Recognized old bundle
settings migrate to it; external choices remain explicit. A missing new helper
does not silently downgrade to the legacy material helper. The existing CLI
`--particle-probe`/`--dotnet` options select the same runtime without new flags.
The native extraction CLI and `.crav` v1 contract are unchanged.

## Verified scope

One fresh Mina/Venator replay range completed the normal CLI path through actor
extraction, installed assets, skeletal evaluation, particle simulation, Blender
and saving. All 69 selected outward projectiles completed. The saved scene was
reopened and checked, including model transforms/morphs and both rope renderers;
eight consecutive CPU Cycles preview frames were rendered.

Morph timing is checked at 24/30/60 FPS. The encoder bounds the complete linear
curve's float32 representation error before consolidating coincident frame
values. Larger differences still refuse the instance. Whole-instance rollback
also covers an empty result and late shader/shape-key failures.

The internal v2 sidecar preserves history before the shot and subsequent owner
changes, with exact replay/helper/range/payload provenance. Missing capture is
unavailable, not an empty success. An enclosed saved-shot view keeps the original
hashed capture intact. Existing non-projectile package identities are unchanged.

A second fresh normal CLI range qualified both Venator muzzle-flash instances.
Their captured CP5 switch drives direct and inverse alpha scaling; texture curves
use actual child-system collection age. Missing snapshots refuse the whole
instance. UV origins wrap after time interpolation, and clamped corner transforms
precede triangle interpolation, avoiding wrapped-endpoint animation artifacts.
Actual fractional-frame pixel tests cover both paths, packed save/reopen and
late missing-input rollback. The saved normal scene passed 2,878 alpha checks and
eight CPU Cycles preview frames over both firing moments. This establishes the
input/rendering connection; approximate lighting remains a fidelity limit rather
than Source pixel equivalence.

## Static-sheet UV correction

Fresh static sprite-sheet assets bind the measured feature/vertex/pixel shader
identities. Their sheet branch omits the ordinary texture's random UV displacement;
ordinary noise textures retain their explicit substitute seed and warning. This
repairs hard smoke rectangles without an artificial edge fade or simulation change.
Unknown shader inputs and incomplete sheet inventories refuse. Legacy packets
without this proof retain their old behavior; regenerate exports to use the fix.
Typed original animated-sheet sampling remains separate unintegrated work.

A short fresh muzzle export has21simulation frames exactly equal to the earlier
export, unchanged definitions/textures/input hashes and45unchanged mesh/UV hashes.
Independent reopen and fourCPU Cycles frames verify the normal path;3150bone
checks and21root checks pass the existing saved-scene verifier. Synthetic pixel,
legacy-negative and late sibling rollback checks distinguish both texture branches.
This is a rendering-path correction, not all shader variants or Source pixel parity.

## Optional capture failures

Projectile capture is independent of actor and clock extraction. A capture data
or finite-budget failure discards every partial projectile track, stops further
capture attempts and records unavailable coverage. A fully observed empty scan
remains available. Serialized-size checking happens before output creation;
output collisions, writes, flush/sync and core parser errors still fail extraction.

The compiled extractor preserves actor and clock output byte for byte on the
tested replay, both with healthy capture and a private reduced-budget control.
Healthy projectile output also matches the previously qualified capture. Normal
CLI controls verify all three outcomes: character-only export succeeds; strict
requested-effects export refuses publication when capture is unavailable; explicit
partial export keeps the other effects and an unavailable-coverage warning.
Saved scenes reopen with packed assets and pass actor checks; seven consecutive
CPU Cycles preview frames cover the character-only and explicit-partial controls.
The reduced budget exists only in a private test source copy, not the product.

The bounded model checks capture independence, discarded prefixes, no retry and
exclusive publication across four observations. Rust, Python and actual replay
checks cover concrete fields, limits and provenance separately. This compatibility
fix does not qualify additional effect families or relax the coverage gate.

## Raw modifier-history transport

Normal replay extraction now retains bounded, ordered `ActiveModifiers` writes
from signon before cropping actor output. The portable scene package embeds the
validated raw bytes and replay/helper/range/payload provenance. Snapshots,
same-slot writes, removals and absent versus empty protobuf fields remain distinct.
This is internal extraction data; the extraction signature, CLI and `.crav` v1
contract are unchanged. An old helper advertises unavailable capture, not absence
of active modifiers. A failed capture retains an explicitly unavailable diagnostic
prefix and does not resume copying writes. Real output I/O errors remain fatal.

Fresh native comparisons preserve the existing actor, clock and projectile bytes
exactly and reproduce the earlier raw modifier reference. A private row-budget
control preserves those same outputs with unavailable modifier coverage. The
normal export's saved scene package retains the earlier writes; reopen checks
find unchanged simulation, assets, geometry and animation. Four CPU Cycles frames
verify the existing rendering path. The reduced test budget is not a product option.

This dependency does not reconstruct active modifier state, mask activation,
particle births, phase or stop/endcap scheduling. Those providers remain separate
work and current partial-export refusals remain in force. Envelope validation
binds the original extraction range; a later subrange consumer must validate the
containing capture without rewriting its hashed history or treating snapshots as
creation events.

## Owner arrays and raw serial state

Normal extraction also retains complete selected-owner state-word arrays with
exact index/serial identity, life state, nullable missing fields and bounded
provenance. The fixed-array reader preserves every declared uint32 element;
duplicate display names never collapse different indices. These raw inputs do
not decide client visibility or effect activation. Unavailable capture and
incomplete individual fields remain distinct.

The serial-keyed reader folds ordered modifier deltas, merges only present fields
and removes the exact serial on an explicit removal. Slot reuse does not remove
an earlier serial, and snapshots are not creation events. Its projection agrees
with pinned Boon's actual full-message merger at eight measured query ticks.
It remains a raw-state reader for later providers, not a particle lifetime model.

Both input envelopes support enclosed saved shots without rewriting their
original range, history or digests. Default serial queries stop at the selected
shot end; earlier signon state remains available and later writes cannot leak
into that result. Expanded or tampered views refuse.

Fresh full replay capture reproduces the qualified owner-array reference and an
independent wire-index inventory while preserving actor, clock, projectile and
modifier bytes. A private bounded-failure control preserves those same outputs.
A fresh normal export and saved subrange retain the complete input envelopes;
reopen, actor checks and four CPU Cycles frames preserve existing rendering.
The mask activation profile still requires current-build qualification before a
modifier-derived rendering stage is connected.

## Remaining limits

This is not complete game-particle support. Other named attachments, derived
modifier lifetimes, hitbox/snapshot providers and broader world-origin profiles
remain separate integration work. Historical births are not proof of effects
still visible in the shot. The normal coverage report retains those refusals;
explicit partial export is still required where applicable.

The admitted creation profiles are measured Windows client identities. New game
builds and Linux native profiles require qualification. The projectile renderer
uses observed network appearance and in-flight transforms; client visibility,
interpolation/orientation overrides and post-deletion endcaps are not reconstructed.
Seeds, PBR, lighting and bloom remain approximations. Rope geometry is baked for
the export camera. No exact Source lighting or map-interaction claim is made.
