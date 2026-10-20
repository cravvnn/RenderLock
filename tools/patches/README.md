# Helper build dependencies

`tools/codeql_build_helpers.py` restores the public upstream source needed to
compile RenderLock's four C# helpers. The lighting helpers use Source 2 Viewer
20.1, commit `9e2e6c7f67e3c4e5a5f443a60c3a5d841ba8f304`. The particle helper
uses commit `42743fb93030c552f834fabb30708dad84d0cbf5` with these existing local
patches, in order:

1. `vrf-ramp-scalar-spline.patch`
2. `vrf-invalid-transform-identity.patch`
3. `vrf-create-on-model-bones.patch`
4. `vrf-kv3-v5-unsigned-constants.patch`
5. `vrf-bounded-model-extraction.patch`
6. `vrf-set-child-control-points.patch`
7. `vrf-per-child-control-point.patch`

The patches are retained here so a clean CI checkout can resolve the same types
as the helper sources. They do not broaden the application's supported particle
coverage or establish whole-system parity. Model sampling remains an experimental
bone-only subset; incomplete providers are rejected. Upstream is MIT licensed;
see the ValveResourceFormat notices and credits already included in this repository.

The unsigned KV3 constants patch reads the current model data without changing
signed constant semantics. The bounded model extraction patch adds an explicit
animation-export switch, enabled by default. RenderLock's cloth extractor disables
it and limits output to LOD0 render meshes and reconstructed proxies; ordinary
upstream extraction retains its existing behavior. This does not add particle
operators or expand qualified model sampling beyond the bone-only subset.

The child-control patch supports position-only `C_OP_SetChildControlPoints` with
literal first source zero. It writes matching direct children and all their
descendants, retaining the native control-point and live-particle bounds.
Orientation and expression-driven source variants remain diagnosed. The helper
reports supplied, internally authored and unsupplied control-point accesses;
a later authored write cannot erase an earlier missing input. This does not
qualify named or wearable replay providers, model hitbox sampling, or whole
particle trees containing other unsupported operators.

The per-child patch supports `C_OP_SetPerChildControlPoint` with literal first
source zero, increment +1 or -1, a fixed child count, and velocity-derived or
disabled orientation. Each matching direct child receives one source particle's
position and optional frame, recursively propagated to its descendants. Native
particle frames are retained directly; a nonorthogonal frame is rejected where
quaternion interpolation would lose information. Other source expressions,
increments and orientation attributes remain diagnosed. Authored child controls
use the `agr_authored_child_controls_v2` provenance policy and cannot replace an
explicit external input or erase an earlier missing read. This does not qualify
model hitbox initialization, bone lock, named-child acquisition, or adaptive rope
rendering, which remain separate requirements for the original sprint tree.

## CodeQL build

Install the .NET 10 SDK and use a fresh work directory outside the source checkout:

```text
python tools/codeql_build_helpers.py prepare --work <new-external-directory>
python tools/codeql_build_helpers.py build --work <same-directory>
```

Preparation fetches pinned public commits, applies the particle patches, builds
dependency assemblies and restores all four helper projects. CodeQL initialization
belongs between these commands. The second command rebuilds every helper under
the extractor without rebuilding the upstream dependency. No game, replay,
private binaries or running services are required. These are analysis builds,
not a replacement for qualified release-tool packaging.
