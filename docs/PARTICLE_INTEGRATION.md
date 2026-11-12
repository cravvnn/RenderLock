# Particle integration candidate

This source candidate adds outward Mina bat projectiles to normal recorded-effect
exports. It includes network history retention, strict owner/request/capture
binding, animated model geometry and the sprite/rope layers used by the tracer.
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
