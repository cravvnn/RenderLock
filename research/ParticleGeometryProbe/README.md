# Isolated particle geometry helper

This helper exports installed unskinned morph geometry and authored animation.
It does not simulate particles, export materials, reconstruct playback, or enable
`C_OP_RenderModels` in normal exports by itself.

`tools/codeql_build_helpers.py prepare --work <fresh-dependency-directory>` builds
an isolated upstream checkout at `42743fb93030c552f834fabb30708dad84d0cbf5` with only
`vrf-skeletonless-morph-animation.patch`. `build` compiles this helper alongside
the other helpers. Direct builds require `-p:ParticleGeometryVrfProject=<that
geometry checkout>/ValveResourceFormat/ValveResourceFormat.csproj`. Use .NET 10.
Keep its output in a separate directory from ParticleProbe and the qualified
material parser. Never overwrite the latter's ValveResourceFormat.dll.

```
dotnet ParticleGeometryProbe.dll --capabilities
dotnet ParticleGeometryProbe.dll --particle-model-v1 <gameinfo.gi> <relative.vmdl> <empty-output-directory>
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
