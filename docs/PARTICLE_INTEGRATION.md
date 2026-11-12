# Particle integration candidate

This source candidate adds outward Mina bat projectiles to normal recorded-effect
exports. It includes network history retention, strict owner/request/capture
binding, animated model geometry and the sprite/rope layers used by the tracer. It also connects captured scalar
controls and collection-age texture curves used by Venator crossbow muzzle flashes.
It also renders the qualified sustained Halloween mask model on Venator from
retained modifier and owner inputs, independently of recorded particle births.
It is based on the b29 public source; it is not a new published application build.

## Runtime layout

Keep three independent helper directories:

- `tools/particles/ParticleProbe.dll`: existing material/physics helper and its
  separately qualified material parser. Preserve the parser identity enforced by
  `agr/model_material_groups.py`.
- `tools/particle_simulation/ParticleSimulationProbe.dll`: upgraded simulation
  helper with its isolated pinned simulation dependency.
- `tools/particle_geometry/ParticleGeometryProbe.dll`: geometry and typed model
  metadata helper with its isolated morph/static export dependency. Its patch
  set includes skeletonless morph export and KV3 v5 unsigned constants.

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
These raw-input checks remain distinct from the derived renderer below.

## Sustained modifier mask model

A fresh normal export now resolves one sustained Venator mask component from
exact serial/parent identity and complete owner samples. The current Windows
client predicate and bodygroup argument/setter paths were independently checked
offline against the installed modules. The admitted selector supplies authored
bodygroup zero explicitly; this does not qualify the generic renderer's disabled
attribute defaults or other switch subtypes. Changed native inputs refuse.

The component uses the model matching the recorded resource handle, its verified
source bytes, the evaluated skeleton and the authored named `mask` attachment.
Typed model particle settings supply scale. Optional Model bone rules need not
be enabled; when enabled their existing constrained evaluated pose is retained.
Six source variants and their seven authored materials are preserved, with one
selected variant visible. No hero-name fallback, guessed attachment, scale,
particle birth, age or seed is introduced.

The saved normal scene has one mask component/six model objects. All 200 existing
actions, 137 object bindings and 65 effect mesh/UV fingerprints remain exact.
Saved hero checks cover 3,150 bone and 21 root samples; all 21 mask attachment
samples pass. Assets are packed with no external libraries. Actual post-import
failure rollback removes all new data and restores frame/rig visibility; changed
owner, scale and gate inputs refuse before geometry. Four CPU Cycles frames
verify the visible component. Separate Blender component fixtures check 24/30/60
FPS against affine-retimed saved actor actions; only 60 FPS is a fresh full CLI
export. Between-tick transforms remain interpolated approximations.

This admission requires positive gate and owner availability at every selected
tick, with a stable indefinite modifier. Stop/death/endcap transitions, changing
modifier settings, missing owners and measured lifecycle references remain
unsupported. A false request is never converted into instant hiding. Parent
ambient/sugar-rush layers stay explicitly unsupported; component counts do not
inflate recorded particle-instance or birth counts. Approximate lighting is
still overexposed in the retained preview and is not Source shader/lighting parity.

The upgraded geometry helper also freshly reproduces the previously qualified
bat GLB and morph metadata exactly. Its static command does not become a fallback
for a rejected morph export. The separately pinned material parser is unchanged.

## Native-default particle lights

Newly prepared standard-light packets now bind the qualified installed Windows
particle module and convert its default POINT/FX color, intensity and finite
falloff rules. Unknown module bytes, shapes, attenuation styles and unsupported
settings refuse explicitly. Declared null values are not constructor defaults.
Older prepared packets without this additive proof retain their prior behavior.

The scene selects its renderer before building engine-dependent effects. Cycles
uses a finite quadratic/linear-minus-boundary graph with radius keys in a
separate node Action. Radius interpolates before squaring. EEVEE remains an
approximate inverse-square/custom-distance adaptation; no EEVEE linear FX parity
is claimed. No new light population, clock or playback handler is introduced.

A fresh 60 FPS normal export of the qualified Venator range retains 72 effect
objects, including six mask variants and one new-profile point light. Saved
checks preserve 143 object bindings, 205 nonlight Actions and 65 effect mesh/UV
fingerprints; 19 sampled and 54 fractional light comparisons pass. Legacy light
values, late range-key rollback, existing Action preservation and null-proof
refusal pass. The saved hero passes 3,150 bone/21 root checks, with 104 packed
images and no linked libraries. Four CPU Cycles 640x360/16-sample frames complete
and show animated weapon effects with the retained character and mask.

Current-module qualification separately covers 150 prepared native arithmetic
cases and 256 actual color lookup cases. The analytic lookup has a declared
absolute error bound of 4e-7. The named constructor/defaults and FX branch are
statically bound; no full current FX replay, renderer/caller execution, Source
photometry, pixel equivalence or exact map-lighting interaction is claimed.

## Original sheets and the owned flashbang

The measured Windows build now supports the original literal-at-birth Venator
flashbang path in normal exports. The entire13-system tree is admitted together:
original30-frame fireball sheet, pinned static sphere, layered smoke/distortion,
sparks/trails, camera-sampled rings and one qualified default point light.
A separate static-model helper command never substitutes for failed morph output.
The unchanged model GLB's repeated-index zero-surface triangles are removed only
when constructing its Blender mesh to prevent a Blender5.2 custom-normal crash.

Fresh60FPS CLI and independent reopen pass:158 new target objects plus6 masks,
29 actual atlas samples/56 held fractional checks/25 distinct frame rectangles,
20 sphere samples,425 brightness samples and4,950 bone/33 root checks. Prior
23 object bindings/21 Actions/16 meshes remain exact. Actual late key allocation
rolls back all new data. The default hero-follow camera misses the explosion;
its four rendered frames verify scene stability. A separately rebuilt camera
pointed at the effect requalifies depth, retains exact atlas animation and renders
four visible CPU Cycles640x360/16-sample frames. Its66 ring meshes pass57 held
visibility checks sampled at24/30/60FPS. Only60FPS is a full fresh CLI run.

The simulation parser separately adds shader fields39/40. Existing3,974 particle
samples stay exact after removing only these additive fields/policy. Typed
missing fields still refuse. Constant unseeded uniform texture controls use the
existing declared deterministic substitute policy; no engine RNG recovery is
claimed. Collection-age brightness uses actual child-system snapshots.

Rope depth interpolation, native zero-segment-seed UVs and the bounded BLEND_ADD
approximation require current module/shader proofs. Conservative homogeneous
XY/W rejection avoids rejecting entirely invisible triangles; visible camera-
plane/depth-floor cases still refuse. Camera changes require rebuilding sampled
ropes and requalifying cards. The compiled native slices and analytic pixels do
not establish Source framebuffer/blending/filtering/lighting parity.

Ground projection is a proved zero-world-receiver result in character-only
scenes. A pending/imported map still refuses until a real receiving-surface and
material provider exists. Old historical controls, mask parent ambient/lifecycle,
original seed, broader providers, Linux native profiles and exact lighting remain
incomplete. See the adjacent coverage documents for component and model bounds.
Application rebuild and publication remain separate.

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
