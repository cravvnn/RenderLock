# Isolated particle geometry helper

This helper exports installed unskinned morph geometry and authored animation.
It does not simulate particles, export materials, reconstruct playback, or enable
`C_OP_RenderModels` in normal exports by itself.

`tools/codeql_build_helpers.py prepare --work <fresh-dependency-directory>` builds
an isolated upstream checkout at `42743fb93030c552f834fabb30708dad84d0cbf5` with only
`vrf-skeletonless-morph-animation.patch` and
`vrf-kv3-v5-unsigned-constants.patch`. `build` compiles this helper alongside
the other helpers. Direct builds require `-p:ParticleGeometryVrfProject=<that
geometry checkout>/ValveResourceFormat/ValveResourceFormat.csproj`. Use .NET 10.
Keep its output in a separate directory from ParticleProbe and the qualified
material parser. Never overwrite the latter's ValveResourceFormat.dll.

```
dotnet ParticleGeometryProbe.dll --capabilities
dotnet ParticleGeometryProbe.dll --particle-model-v1 <gameinfo.gi> <relative.vmdl> <empty-output-directory>
dotnet ParticleGeometryProbe.dll --particle-authored-model-v1 <gameinfo.gi> <relative.vmdl> <empty-output-directory>
dotnet ParticleGeometryProbe.dll --particle-static-model-v1 <gameinfo.gi> <relative.vmdl> <empty-output-directory>
dotnet ParticleGeometryProbe.dll --particle-texture-sheet-v1 <absolute.vtex_c> <existing-empty-output-directory>
```

The first admitted geometry subset has zero bones, no body selectors and one
bounded authored morph animation. Unsupported animation is rejected rather than
exported frozen. Materials, placement, Source interpolation, visibility and
lighting require separate qualification. The Python GLB reader additionally
checks every represented mesh/animation accessor before use.

`GeometryExport.tla` models the synchronous command's guard ordering and owned
descriptor publication. The two Python geometry/math modules are pure bounded
transformations, so they add no persistent lifecycle needing another state model.
The existing build tool has one preparation receipt; its exact configuration now
includes the separate geometry revision/patch list, rejecting older preparation.
No cache, retry, background worker, lock or game lifecycle was added. The design
deliberately reuses the existing build preparation path rather than adding a
second persistent build manager. The finite model excludes decoding arithmetic,
OS races and crash durability; those are not claims made by this helper.

The explicit authored-model command admits the separately qualified six static
mask variants and typed inventory commands; it is never a failed-morph fallback.
The separate static-model command has its own policy and accepts no animation,
at most one source root and no body selectors. It does not fall back from morph
failure. Consumers must still validate every GLB primitive, node and any complete
identity rest skin before cache publication. This command does not enable new
static material or normal-export providers by itself.
The pending texture-sheet command retains a bounded original flat atlas and
typed sequence timing. It refuses cropped/split formats, uses exclusive output
files and leaves failed prefixes unaccepted. `TextureSheetExport.tla` models
that admission and competing creation; decoder/image arithmetic still needs
actual helper validation. See `agr/ParticleTextureAnimation_COVERAGE.md` for the
current pending Python/Blender integration scope. Material parsing remains in its
separate directory and is not replaced by this helper.
# Authored owner-model rest inventory (pending provider integration)

`--inventory-model-rest GAMEINFO MODEL.vmdl FRESH_OUTPUT.json` emits typed compiled
model-order parent positions, rotations and scales with the source digest. It
does not export geometry/materials or infer a runtime model/attachment provider.
Existing files/directories refuse before loading; exclusive create also protects
a competing writer. Consumers must check process success, bounds and metadata.
The Python collector reads at most4MiB and verifies source/order/parents/runtime.
See `ModelRestInventory.tla` and `agr/ParticleSnapshotProvider_COVERAGE.md`.
