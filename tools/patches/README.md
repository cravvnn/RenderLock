# Helper build dependencies

`tools/codeql_build_helpers.py` restores the public upstream source needed to
compile RenderLock's four C# helpers. The lighting helpers use Source 2 Viewer
20.1, commit `9e2e6c7f67e3c4e5a5f443a60c3a5d841ba8f304`. The particle helper
uses commit `42743fb93030c552f834fabb30708dad84d0cbf5` with these existing local
patches, in order:

1. `vrf-ramp-scalar-spline.patch`
2. `vrf-invalid-transform-identity.patch`
3. `vrf-create-on-model-bones.patch`

The patches are retained here so a clean CI checkout can resolve the same types
as the helper sources. They do not broaden the application's supported particle
coverage or establish whole-system parity. Model sampling remains an experimental
bone-only subset; incomplete providers are rejected. Upstream is MIT licensed;
see the ValveResourceFormat notices and credits already included in this repository.

The separate `vrf-kv3-v5-unsigned-constants.patch` is retained for compatibility
work. The CodeQL build does not need that parser-only patch to resolve helper types.

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
