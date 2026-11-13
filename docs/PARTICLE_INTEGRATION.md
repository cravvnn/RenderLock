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
Original flat animated-sheet sampling is separately qualified for the bounded
Venator Flashbang profile. Other sheet/shader modes still require qualification.

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


## Qualified particle observation files

`convert --particle-observations observations.json --particle-probe <simulator>
--model-constraints` imports already captured, qualified owner observations.
The same optional file is available under Advanced > Particle simulation.
It requires Installed game assets, particle simulation and model bone rules.
Leaving it empty preserves normal export behavior. A configured file with those
requirements disabled is an error; clear the field to export without it.

The UTF-8 JSON envelope is `{"format":"renderlock-particle-observations",
"schema_version":1,"captures":[{"instance_id":"index:generation",
"capture":{...}}]}`. Each capture is the normalized
`observed_crossbow_owner_birth_6861_v1` record validated by
`agr.particle_crossbow_owner.validate_capture`; it is not a bound package from
another scene. Up to64 records/1MiB are accepted. Unknown envelope/record fields,
duplicate keys/identities, oversized/deep/nonfinite JSON, reserved-output aliases
and captures absent from the selected history refuse. All rows must match the
fresh replay/model/birth/provider before Blender receives them. The report
records the imported byte digest and instance IDs separately from FX coverage.

This does not capture anything from a running game, prove that user-edited
records are authentic, or establish complete particle coverage. Schema 1 supports
only the current qualified Windows Crossbow profile; positive
render children, other heroes/builds and Linux need their own qualification.
The separately typed Silence schemas are described below. Missing
observations remain unsupported and strict/explicit-partial rules are unchanged.


### Preparing observations from an existing capture report

`python -m agr prepare-particle-observations --capture-report report.json
--segment scene.agrscene --instance 5682:1` emits observation JSON on stdout
only after every requested instance passes. Save stdout as UTF-8 for use with
`convert --particle-observations`. Repeat `--instance` for multiple records.
A saved `.agrscene` can supply the recorded event/model identities; geometry
assets are not needed merely to normalize the report. The final export still
prepares and independently validates its fresh rig and original event history.

Only the already qualified current Crossbow report shape is accepted. The
normalizer checks the report's declared successful closure/restoration fields,
replay identity, exact birth sample, full owner serial, unique property group,
actual CP0 raw-record slot and existing module/model/graph profile. Simultaneous
indistinguishable births refuse. No observation/default pose is synthesized.
The report digest binds its original bytes. These checks do not authenticate
edited reports or replace the producer's native-reader and cleanup audits.

This command is an offline producer adapter. It does not launch, attach to,
control or read a game, create a live observation, or add other particle families.
The qualified collector remains a separate integration requirement.

## Supplied captured slide effects

The normal CLI and Advanced > Particle simulation expose the same optional
`--captured-slide-inputs` / **Captured slide effects** input. It accepts the
qualified retained Venator slide cohort: sprites, trails, lights and models.
Use the matching replay, output ticks 17920..18240 inclusive, 62 earlier ticks,
installed particle simulation, model bone rules and explicit partial export.
Choosing a file keeps the other settings; partial consent is required per export.
The remembered path is absolute so a different application working directory
does not select another capture. Clear it when the capture is no longer wanted.
Choosing **Continue without effects** clears supplied capture selections for
that retry. Omitting required model bone rules is not offered while these
capture inputs remain selected; review or clear them explicitly first.

The UTF-8 descriptor has `format: "renderlock-captured-slide-inputs"`, integer
`schema_version: 1`, one `source_scene: {path, sha256}`, and four ordered
`packages: [{backend, file: {path, sha256}}, ...]` entries. The backends are
`observed_slide_sprite_frames_v1`, `observed_slide_trail_frames_v1`,
`observed_slide_light_frames_v1` and `observed_slide_model_frames_v1` in that order.
Relative descriptor references resolve from the descriptor's directory. Original
asset directories currently must remain absolute and available; this is not a
portable asset archive or a tool for collecting arbitrary new replays.

The reader bounds each file, aggregate bytes, nesting and values. It rejects
duplicate keys, changed hashes, aliased/reserved outputs, missing components and
mixed reports/reader/freeze/cleanup provenance. Existing native/frame/material
validators still run. The original capture fingerprints stay unchanged; a
separate exact owner/pose/asset binding qualifies the freshly prepared target.
All four renderers share one allocation transaction. A late failure removes all
new sibling allocations; publication requires an exact four-component receipt
even in partial mode. Recorded birth and unsupported counters remain separate.

The qualified ordinary 60 FPS export contains 842 captured objects, with an
independent saved-scene/reopen, real fourth-component rollback, 24/30/60 FPS
sampling of the saved visibility curves and four CPU Cycles previews. Sampling
grids are not three fresh exports. This does not establish full game/hero/build
coverage, lifetime joins/orphans/endcaps, a common Source render clock or exact
lighting/pixel parity. Captured sprite/trail geometry is baked for the export
camera; camera edits require a new export. Automatic acquisition, portable
captured-asset relocation and other native profiles need separate integration.


## Supplied Silence observations and shared rendering additions

The same optional observation input also accepts schema 2 original bound Silence
birth/body envelopes, or schema 3 with an explicit original source-scene witness.
The birth and child packets keep their original identities. Schema 3 separately
proves that the fresh export consumes the same owning root, pose, model,
skeleton, constraints and recorded event inputs. Added earlier replay history is
allowed only when those consumed values match exactly. This does not qualify a
new native reader or permit rewriting old fingerprints.

Fresh evaluated hitboxes and CP attachments feed all four Silence systems. The
saved scene must reproduce those inputs before allocating replacement geometry.
The startup cylinder preserves affine component-scale/rotation order, including
shear. Body sprites use the pinned paired flow sheet; root/scalar operators and
normalized emission batches feed the original material fields. Ropes use the
retained reference distance-size fade/clamp and remain camera baked. Missing
provider/material inputs or unqualified provenance refuse; no child is omitted
to turn an incomplete tree into a successful effect.

This supplied historical profile completed a real ordinary export, independent
saved-input recapture, actual late allocation rollback and two short CPU Cycles
sequences. Startup and body-only off controls confirm visible contributions.
It does not establish native filtering, clocks, original RNG, fog, depth,
bloom, radiometry or Source pixel parity. Automatic acquisition, broader
Mina blink/sprint/native attachment/lifecycle providers and Linux remain held.
See `agr/ParticleSilenceNormal_COVERAGE.md` and the component coverage files.

Observation files are homogeneous: schema 1 Crossbow and schemas 2/3 Silence
cannot be combined into a mixed-profile envelope. A schema 3 source witness is
required for its fresh-scene equivalence path. Do not manually rewrite a bound
capture to make a different shot appear to match. The existing strict versus
explicit-partial publication rules remain in force.

## Supplied ambient inputs

`--captured-ambient-inputs` and Advanced > Captured ambient effects import the
qualified retained Venator censer sprite/rope groups. They are independent
captures with separate native/reader/freeze/cleanup provenance; no common Source
clock is inferred. Both remain unchanged while a separate exact binding proves
the fresh owning rig inputs. Both renderers share one transaction and require a
complete receipt even in partial mode. See `CAPTURED_AMBIENT_INPUTS.md` for the
bounded descriptor, required settings and non-portable retained asset paths.

The normal input profile has an independent saved reopen, real late second-
component failure rollback and four CPU Cycles frames with an ambient-off
control. Recorded-birth unsupported counts remain separate. Automatic collection,
portable asset relocation, lifetime joins/death/endcaps/orphans, wider heroes or
native profiles, map receivers and exact game lighting remain unqualified.

## Qualification and delivery boundary

The current combined two-hero ordinary export completed at 60 FPS over ticks
17920..18240 inclusive with 62 earlier history ticks. It saved 8,373 effect
objects: 5,088 from all 69 outward bats, 842 supplied slide objects, 1,605 supplied
ambient objects, 206 Silence objects, six sustained mask variants and other
recorded effects. It used explicit partial coverage and supplied capture inputs.

Independent reopening checked 642 root and 115,881 bone samples, all 69 bat
models, 207 model observations, both rope renderers and exact Silence input
recapture. The worst model-matrix and morph-weight errors were below 1.90e-6
and 1.59e-6 respectively. Empty-projectile rollback passed. All 4,179 file images
were packed, with no linked libraries. Four consecutive original-camera CPU
Cycles frames at 640x360/16 samples showed animated bats; hiding only their
model camera rays changed 784 pixels. This is a short diagnostic, not a full
sequence or Source image comparison.

The saved 60 FPS scene was sampled on 24/30/60 FPS morph grids; those are not
three fresh exports. All 81 earlier current-helper instance simulations and
assets stayed exact. All older fields of 69 outward-bat simulations stayed
exact, with only declared shader attributes added. Separately, the current
simulator preserved all 400,487 prior values/types/array lengths across 69
released frozen world-impact requests. That last check is simulator compatibility,
not an additional normal export. The supplied Crossbow profile has its own
current-runtime normal/save/rollback/Cycles qualification below.

The combined report retains 1,084 unsupported/unavailable occurrences, including
retained earlier history; 25 were born inside the output range. A homogeneous
Silence observation file cannot also supply Crossbow observations. Crossbow is
qualified separately, not silently subtracted from this combined report. See
`PARTICLE_SUPPORT.md` for the integrated/held inventory and remaining behaviors.

These changes are a source integration candidate, not a rebuilt or published
application. Keep the separate material, simulation and geometry helpers and
rebuild the required helpers from their pinned build glue/patches before a new
app package. An older simulation helper cannot consume all new inputs. Never
replace the separately qualified material parser to obtain simulation features.
Individual prepared-math, normal-export, saved-scene and visual checks establish
different scopes; none alone proves every particle or exact game lighting.
Unsupported occurrence totals include retained history and do not count visibly
missing effects. An earlier birth alone does not prove continued visibility.


### Current complete geometry runtime and Crossbow regression

The geometry helper must advertise `inventory-model-rest` as well as the existing
model/static/authored/sheet commands. The older tested geometry host used by some
retained Silence cases lacks that command; do not reuse it for Crossbow. A complete
geometry runtime includes the native Skia/spirv dependencies, not only its managed
host. The qualified complete host is
`670f09c73e96e1ac504c001b54f67da1442c9ba3c3d00ea3a2b868073dad3c2c`;
its parser is unchanged. Bat/cylinder/mask exports were byte-identical to retained
outputs. This is a runtime composition correction; tracked geometry source already
contains the required command and no parser/native build was changed.

Current simulation plus that complete geometry runtime passed a fresh supplied
Crossbow normal export, independent saved rig/input/UV/shader checks, actual late
rope rollback and four CPU Cycles frames. Its ring alpha now follows the final
30-particle normalized batch. All990alpha samples match the authored retained
linear curve within2.91e-7;924values/types differ from the previous growing-count
bug. Every other simulation field and the assets match. Native spline/tangent,
clock and lighting parity remain unqualified. The combined checks above cover
the other supplied selections; Crossbow remains separately qualified.
