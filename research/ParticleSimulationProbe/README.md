# Separate particle simulation dependency

This project compiles the shared ParticleProbe commands against an isolated
simulation parser. The material/cloth extractor remains at `tools/particles`
with its qualified parser. Geometry conversion is a third, separate process.
Do not replace the material parser DLL or relax its hash pin to ship simulation
changes.

`tools/codeql_build_helpers.py prepare` creates independent material, simulation,
geometry and lighting dependency checkouts. The simulation dependency uses the
same pinned upstream commit and baseline seven patches, plus the bounded
`vrf-vector-expression-initializer.patch`. `build` compiles all six helpers.
Direct builds require `-p:ParticleSimulationVrfProject=<prepared simulation
checkout>/ValveResourceFormat/ValveResourceFormat.csproj` and .NET 10.

The additional initializer implements only ADD to Normal, replacement and no
normalization. Other modes fail explicitly. Authored expression CP writes use
the v3 provenance policy; external overwrite and missing-read guards remain.
The shared capture code now emits the actual second sequence selector. Missing
selectors in older packages are not interpreted as zero for model rendering.

This is integration source, not a complete flying-bat support declaration.
Normal-export capture/provider qualification, complete rope/shader support and
the final portable runtime package have separate acceptance gates. No GUI default
or release runtime is switched by adding this project. The geometry/helper
command and normal package transaction models are adjacent to those components;
the shared input audit is exercised by actual helper oracles and strict Python
provenance checks. Native allocation, gameplay scheduling and crash durability
are outside these offline simulator checks.

See `docs/PARTICLE_INTEGRATION.md` for the later normal-export qualification and
the complete three-helper runtime layout; portable application packaging remains
a separate gate.

## Additive shader scalar snapshots

The isolated simulation dependency additionally applies
`tools/patches/vrf-shader-extra-fields.patch`. Attributes39/40 are actual float
properties through initializer/operator read and write, with emitted keys
`shader_extra_data1`, `shader_extra_data2` and policy`agr_shader_extra_fields_v1`.
Consumers never interpret absent old-packet fields as zero. No new RNG or clock
is introduced. The fields are simulator outputs, not captured engine pixels.

`dotnet ParticleSimulationProbe.dll --shader-extra-fields-oracle OUTPUT_JSON`
checks real initialization, add-to-initial interpolation and chained scalar
storage (1.5/4.5). The fresh flashbang request adds only these fields/policy;
all3,974 existing particle samples and all other output metadata remain exact.
The material parser remains separately pinned; never replace its DLL with this
simulation assembly. The actual managed build is separate from AOT app packaging.

## Pending explicit snapshot simulator

The simulation-only `vrf-explicit-snapshot-skinning.patch` registers the bounded
sequential single-bone snapshot initializer and full-strength rigid position-pair
operator. The immutable bone binding travels with the particle and its initial
array through compaction. Snapshot CP and model CP remain distinct; no root,
missing-bone or imported-rest fallback is supplied.

Only this project defines `AGR_SIMULATION_SNAPSHOT`. The shared host reads a
complete birth-to-end named-bone history before simulation, advances exactly one
tick before Start/Update, and acknowledges only the bindings actually queried.
Ordinary requests retain their previous output shape. The material helper keeps
its unchanged parser and does not compile snapshot-dependent commands.

This is an unaccepted component port. It does not qualify current replay owner
selection, attachment-mode changes, automatic model input acquisition, Crossbow
rendering or normal-export support. See `agr/ParticleSnapshotProvider_COVERAGE.md`
for the separate admission and saved-scene gates.

Only ParticleSimulationProbe defines`AGR_SIMULATION_SHADER_EXTRAS`. Shared probe
source conditionally emits these new fields/oracle/policy; ParticleProbe still
compiles against the unchanged material parser without the simulation patch.

## Explicit hitbox simulation inputs

The simulation-only `vrf-explicit-hitbox-runtime.patch` adds authored bounds/SRT
sampling and non-rigid Lock to Bone using explicit CP/model/owner identities.
`AGR_SIMULATION_HITBOX` enables the complete, bounded request-history reader.
Each consuming collection has its own current/previous history, while repeated
queries at one collection time reuse the first cached frame. There is no model,
root-bone or missing-history fallback. Snapshot/hitbox mixed inputs refuse.

Ordinary non-hitbox requests keep their previous output shape. Material parsing
stays in its separately pinned helper; the local history mirror is excluded from
both host projects because the simulation dependency supplies the compiled type.
Silence birth/root-CP writers, fresh pose acquisition and ordinary-export wiring
remain separate requirements. This component does not establish full Silence,
current native capture, saved-scene or Cycles support. See the adjacent hitbox
coverage documents for the state models, limits and code-oracle scope.

## Root positions and scalar warp

The following simulation-only patch adds qualified literal root position writes
and the registered PositionWarpScalar initializer. Existing point identity,
orientation and previous values are retained while positions propagate through
the existing tree, including local overrides. Known root writes get v4 provenance;
unknown transform fallbacks stay unqualified. Set-once and reset keep their
existing per-operator flags. Scalar interpolation uses explicit current/previous
positions and existing numeric providers; it introduces no emission/clock state.

The later normalized-emission patch fixes the zero-radius swirls left by this
root/scalar layer. All four systems remain enabled; helper-level positive samples
are separately checked. Full normal/save/render support still has separate gates.

## Normalized emission batches

Only the simulation project defines `AGR_SIMULATION_BATCH` and applies the batch
patch. The shared emitter callback owns a bounded transient age list; it detaches
before reservation/initialization. Initial and prewarm particles share that path,
with the existing public prewarm counters restored on successful EndPrewarm.
No fake denominator, extra particle clock or per-effect radius fix is used.
The material parser and unrelated child camera-distance scheduling stay unchanged.

`--spawn-batch-oracle` checks native prepared normalized-number rows plus real
capacity/replay/prune/age/callback/initializer-failure and prewarm boundaries.
Ordinary output changes where it previously used the growing count: the retained
flashbang ring alpha and flare rotation now follow the final batch count. Their
numerical changes are audited; new normal/saved/visual qualification is required.
The pinned upstream curve interpolation remains its existing linear approximation.

One qualified Silence instance also gets explicit material/aux policy markers and
direct scratch1/2/material-vector snapshots. Other requests keep their old field
shape. Missing material values are not filled with zero. This completes prepared
attribute transport, not fresh rig acquisition, geometry, Blender or Cycles proof.
