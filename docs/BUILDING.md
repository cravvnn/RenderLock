# Source setup and builds

[All guides](README.md) · [Contributing](../CONTRIBUTING.md)

Use the [Windows portable ZIP](https://github.com/cravvnn/RenderLock/releases/latest)
for normal use. A source checkout needs Python dependencies and the appropriate
external/native tools; creating a virtual environment alone does not provide them.

## Python source setup

Use 64-bit Python 3.12+ with Tcl/Tk. From the checkout:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
.venv\Scripts\python.exe -m agr --help
.venv\Scripts\python.exe -m agr gui
```

On Windows, `Launch_RenderLock.bat` checks Python/dependencies and can download
requirements on first setup. `Launch_RenderLock_Development.bat` uses an existing
environment in a full checkout. The Windows portable launcher uses its bundled
runtime and does not install Python packages at startup.

`launch_renderlock.sh` provides source environment bootstrapping on Unix; it is
not a supported Linux/macOS distribution. See the separate
[Linux support branch](https://github.com/cravvnn/RenderLock/blob/feature/linux-support-20261024/LINUX.md)
for native dependencies, current fixes and pending qualification. No native Linux
release or update transaction is implemented in Windows b16.

A full Git checkout is needed for the `research` helper projects and repository
build metadata. The prepared source ZIP omits private/developer research workspaces
and stages the character-beta profile. A normal Git checkout defaults to development
features; do not assume its installed-map geometry controls match the beta ZIP.

## Replay extractor

Install Rust and build the locked native project:

```powershell
cargo build --release --locked --manifest-path native/Cargo.toml
New-Item -ItemType Directory -Force bin
Copy-Item native/target/release/agr-extract.exe bin/agr-extract.exe
```

For native Linux development the executable is `agr-extract` without `.exe`.
Use a platform-native build. This tool reads local replay files; no game process
or Proton prefix is required for offline extraction.

## Converter and helper dependencies

The Windows package uses the qualified Source 2 Viewer 20.1 CLI and matching
native companions. Obtain it from the
[upstream release](https://github.com/ValveResourceFormat/ValveResourceFormat/releases/tag/20.1).
Point Setup/CLI at `Source2Viewer-CLI.exe`, not the desktop viewer. Matching version
text alone does not qualify arbitrary binaries for optional lighting.

Lighting and particle helpers are separate .NET 10 projects. Their dependency
revisions differ. The
[helper build script](https://github.com/cravvnn/RenderLock/blob/main/tools/codeql_build_helpers.py)
pins lighting VRF `9e2e6c7f67e3c4e5a5f443a60c3a5d841ba8f304` and the particle
fork `42743fb93030c552f834fabb30708dad84d0cbf5`, with the reviewed particle patches.
Do not build a lighting helper against the particle fork or replace namespaces
without checking that contract.

For the pinned build workflow, choose a fresh dependency directory outside the
source checkout and run:

```powershell
python tools/codeql_build_helpers.py prepare --work "..\renderlock-helper-deps"
python tools/codeql_build_helpers.py build --work "..\renderlock-helper-deps"
```

This is dependency/compiler preparation, not decoded-pixel, simulation or render
qualification. The existing main workflow expects Windows native assets. Linux
asset selection improvements remain on the separate experimental branch.

`tools/prepare_lighting_helpers.py` can copy a verified local world/atlas/cube kit
to a fresh directory with a manifest. Run its `--help` for explicit paths. It does
not bundle a .NET runtime or grant qualification to a changed helper.

## Windows portable packaging

Use a fresh candidate output directory. From an environment with runtime
dependencies and the packager's `packaging` dependency installed:

```powershell
python tools/package_character_beta.py --source . --output "..\renderlock-candidate" `
  --native-helper bin/agr-extract.exe `
  --source2viewer-dir "..\source2viewer-20.1" `
  --lighting-kit "..\verified-lighting-kit" `
  --particles-dir "..\verified-particle-kit" `
  --dotnet-runtime "..\dotnet\shared\Microsoft.NETCore.App\10.0.0" `
  --launcher-exe tools/launcher/target/release/RenderLock.exe
```

Paths/runtime versions above are examples; supply the actually verified inputs.
Build the launcher with `cargo build --release --manifest-path tools/launcher/Cargo.toml`.
Use `python tools/launcher/set_icon.py` to embed the existing icon in a freshly
built launcher. [Artwork notes](../artwork/README.md) cover icon regeneration.

The packager validates the particle capability contract, stages beta source and
portable payloads, retains notices, and writes inventories/manifests and receipts.
Validation still needs archive checks and bounded packaged-runtime/feature checks.
Never package game assets, private replays, local credentials or research evidence.
The external map and Elagrimm library remain separate downloads.

See [publishing conventions](../UPDATING.md#publishing-a-compatible-update) before
preparing an updater-compatible release. Do not replace published ZIP bytes under
an existing version; use the release process and a new version for a published app.

## Checks and evidence

Start with focused non-displaying tests, for example:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_external_map.py tests/test_export_recovery.py
```

Native Tk tests can open windows and need a suitable display/lifecycle. Use an
isolated non-visible desktop where supported; do not disrupt someone's active
desktop or claim mocks as native coverage. Run broader checks appropriate to the
change and record skipped or unverified scope. Do not run game/export loops as a
substitute for a bounded regression case.

`tools/verify_saved_scene.py` reopens a scene with its companion package/report and
compares recorded poses/roots. In Windows b16, cloth-driven bones can intentionally
differ from that baseline. Use a cloth-off control; this tool is not a cloth-motion
oracle. The experimental Linux branch adds explicit PARTIAL scope for those bones.
Neither numerical pose checks nor a successful save establish game-render parity.
