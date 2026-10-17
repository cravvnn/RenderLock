# Local particle simulator patches

Base: ValveResourceFormat commit `42743fb93030c552f834fabb30708dad84d0cbf5`.
The local checkout has `vrf-ramp-scalar-spline.patch` and
`vrf-invalid-transform-identity.patch` applied. They add the missing operator
and explicitly support the identity transform sentinel. Neither alters or
injects into the game.

For a fresh checkout at that commit, apply with:

```powershell
git -C research/upstream/ValveResourceFormat apply ../../patches/vrf-ramp-scalar-spline.patch
git -C research/upstream/ValveResourceFormat apply ../../patches/vrf-invalid-transform-identity.patch
$env:DOTNET_ROOT = (Resolve-Path .toolchains/dotnet).Path
& .toolchains/dotnet/dotnet.exe build research/ParticleProbe/ParticleProbe.csproj -c Release
```

The current research probe also requires `vrf-create-on-model-bones.patch`,
applied after the two patches above. This is an experimental bone-only subset,
not part of the verified dev8 release. It requires explicit model-order world
bone poses; missing poses fail. Mesh, local, shell, weighted/even, selected-shape,
dynamic-provider and inherited-velocity modes remain rejected. Replay wearable
binding and complete initializer/provider parity are not established.

`verify_model_particle_sampling.py` compares the Python sampler with bounded
native emulation; the probe's `--model-sampling-oracle INPUT OUTPUT` compares
the C# sampler against the captured-rig fixtures. `verify_regen_model_simulation.py`
checks repeatability, finite output and malformed-history rejection.
`bake_regen_model_study.py` and `verify_regen_saved_study.py` produce and reopen
a clearly labeled research scene. Keep all private inputs and outputs outside Git.

Do not apply twice. From the existing patched checkout, `git apply --reverse
--check` with the same patch verifies that it is present without reverting it.
The probe rejects an assembly without the new operator or verified identity
behavior, and labels its backend with both patch names.

Evidence: `research/verify_ramp_spline_oracle.py` executes only the hash-pinned
installed operator in bounded Unicorn emulation using synthetic memory. It
does not load the DLL into a game process. The C# probe's `--ramp-spline-oracle
<oracle.json> <report.json>` mode compares the actual new operator against those
independent results. Results live under `research/runs/overnight/ramp-spline-*`.
The 504 checked lanes include Priest's authored roll/yaw parameters and default
values. They are numerical evidence for this routine, not whole-system
or whole-game parity.

`research/verify_invalid_transform_oracle.py` verifies the enum value, runtime
identity initialization, provider setup and 16 time samples with bounded offline
emulation. The probe's `--invalid-transform-oracle <report.json>` checks the
actual parser/provider and confirms named, range and unknown transforms still
warn. With the renderer's per-layer sequence wrapping fix, three Priest firing
events now export; broader effect coverage is still partial.
