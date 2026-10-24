# Experimental native Linux support

Linux support is being developed separately from the Windows portable release.
There is no supported Linux release archive or Linux updater yet. Do not treat
the Windows ZIP as a native Linux installer.

A community CachyOS/Arch, KDE Wayland witness reports successful Standard, Source
lighting and Elagrimm exports using native tools, after local dependency and
decoder-gate changes. Its scope is one Haze replay/range, with partial export
options selected. It establishes a useful test lead, not general compatibility.
The supplied reports and renders are not a replacement for decoder qualification.

## Changes on this branch

- Helper-kit copies and cache identities include `.so`, numbered `.so` files and
  `.dylib` companions. Changing a Linux native decoder invalidates its cache key.
  Companions resolving outside the chosen helper directory are refused.
- Lighting helper builds select the correct native Skia asset with
  `ProbeRuntimeIdentifier=linux-x64` or `win-x64`. Missing assets stop the build
  with a specific message. This does not grant a rebuilt helper qualification.
- Steam discovery includes the Flatpak Steam root. Converter discovery on Linux
  selects native CLI names instead of a leftover Windows `.exe`. Blender on PATH
  remains preferred; `~/.local/bin/blender` and `/opt/blender/blender` are fallback
  locations. An explicit executable can always be selected.
- Texture-decoder identity uses `libSkiaSharp.so` on Linux rather than treating a
  missing Windows DLL as the native identity. Unknown decoder/native-library
  pairs remain refused.
- Saved-scene verification distinguishes declared cloth-modified bones from
  unmodified recorded motion. Cloth exclusions are explicit and result in
  `PARTIAL`/exit 2, never a full PASS. Body-pose errors still FAIL/exit 1. A cloth-off
  scene is the control for a full recorded-pose PASS/exit 0.

## Native dependencies and paths

Use Python 3.12 or newer in a virtual environment, the dependencies in
`requirements.txt`, Python Tk for the desktop UI, a native x86-64 replay extractor,
native Source 2 Viewer CLI, .NET 10 and Blender. Blender capabilities are probed;
the existing code does not require an exact 5.2.1 patch version. The community
witness used Blender 5.2.2. A Flatpak Blender executable wrapper must preserve
arguments and permit access to all input, cache, helper and output paths; that
sandbox workflow has not been qualified here.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cargo build --release --locked --manifest-path native/Cargo.toml
mkdir -p bin
cp native/target/release/agr-extract bin/agr-extract
```

Use native `Source2Viewer-CLI`, not its Windows executable. Explicit locations can
be supplied with `--source2viewer`, `--blender`, `--particle-probe`, `--dotnet`,
`--world-region-probe`, `--lighting-dotnet`, `--probe-atlas-decoder` and
`--cubemap-decoder`. The `.dll` helper entrypoints are managed .NET assemblies;
the native image library next to them must match their Skia dependency version.
Keep output and `--asset-cache` outside the game installation, on a filesystem
that preserves case and normal file semantics. Default Linux caches use
`$XDG_CACHE_HOME/RenderLock/assets` or `~/.cache/RenderLock/assets`.

Native Steam roots and Flatpak Steam libraries are discovered without entering a
Proton prefix. `DEADLOCK_INSTALL` or `--game-install` can select an installation
explicitly. The exporter reads replays and assets offline; it does not require
sharing the game's Wine/Proton process environment.

## Rebuilding the lighting helpers

Use a full Git checkout for helper development; release source ZIPs omit the
developer `research` helper projects.

Use the **lighting** dependency revision pinned in `tools/codeql_build_helpers.py`:
`9e2e6c7f67e3c4e5a5f443a60c3a5d841ba8f304` (VRF 20.1). The separate particle fork
`42743fb93030c552f834fabb30708dad84d0cbf5` is not a substitute.

The reported `SteamDatabase.ValvePak` compile error came from building the world
helper against that particle revision, whose ValvePak package uses a different
namespace. The pinned lighting revision uses ValvePak 5.0.2.177 and the existing
namespace. All three helpers compile against those correct references; changing
the namespace globally would break that pinned dependency contract.

Build the pinned VRF lighting project with copied local dependencies first:

```sh
dotnet build /path/to/pinned-vrf/ValveResourceFormat/ValveResourceFormat.csproj \
  -c Debug -p:CopyLocalLockFileAssemblies=true

dotnet build research/WorldRegionProbe/WorldRegionProbe.csproj -c Release \
  -p:ProbeReferences=/path/to/pinned-vrf/ValveResourceFormat/bin/Debug \
  -p:ProbeRuntimeIdentifier=linux-x64
```

Repeat the helper build for `ProbeAtlasDecode` and `CubemapDecode`. The references
must include `runtimes/linux-x64/native/libSkiaSharp.so` from the **same** dependency
build. The pinned lighting project uses SkiaSharp 4.151.1; mixing its managed
assembly with a 4.151.2 native library is not qualified by these build checks.
For a prepared native dependency at another location, `ProbeNativePath` can be
supplied explicitly. Keep that asset's version and provenance matched to the
managed references. ARM and macOS helper builds are outside this branch's scope.

Freshly built DLLs are not silently added to the product's qualified helper
hashes. Compilation, a helper self-test, native execution and decoded pixel
qualification are separate checks.

## Remaining release gates

The community patch added a Linux CLI hash with `None` for its native companion.
That patch is not adopted. The reported CLI hash is
`3aaa77568beaf6b130d974993a42088d8ce58b2e001fdda42a2fd8aae58ffb4d`;
its reported native image hash is
`5039f4daa53be6fdf1742af522c24e3b77d5e7653936cc87ee09b9603cbdee53`.
The latter matches the Linux x64 asset in the official SkiaSharp 4.151.1 NuGet
package examined during this work. The actual Linux CLI binary and an independent
decoded-pixel comparison were not supplied. Neither a matching version string
nor successful scene export qualifies all texture decoder semantics.

Before accepting a Linux decoder/helper profile:

1. Record exact executable, managed assembly, native companion and input hashes.
2. Run on Linux and compare bounded NPR/BRDF, normal/emission, probe-atlas and
   cubemap outputs against trusted references. Preserve layout, midpoint, source
   identity and size checks. A missing `.so` must fail before expensive work.
3. Export a short current replay range with broad partial animation disabled.
   Check Standard first, then explicit partial Source lighting and Elagrimm.
   Use cloth-off pose verification as a strict control, and separate cloth
   simulation checks. Reopen, inspect packed/external assets and compare images.
4. Exercise a supported recorded particle witness; the supplied Linux clip
   produced no completed particle layers. No new particle support is inferred.
5. Test native packaging, Tk on X11/Wayland, filesystem case, fresh-machine
   dependencies and installation. The Windows updater remains disabled on Linux
   by the existing platform guard. A Linux update transaction is not implemented.

Animation-table snapshot mismatches and unsupported static-map lighting remain
strict. The report alone does not justify continuing with mismatched animation
tables, bypassing the static-lighting shader gate, adjusting emission globally,
or claiming full cloth/effect/game-render fidelity.

Local checks for this branch ran on Windows: focused Python tests, cross-platform
managed compilation/native asset copying, a Windows helper self-test, and small
actual background Blender verification fixtures. No native Linux runtime was
available for an independent end-to-end run.
