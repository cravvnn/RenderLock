# Portable runtime notices

The Windows release script includes Python's installed `LICENSE.txt` and
PyInstaller's installed `COPYING.txt` alongside these notices. PyInstaller's
bootloader distribution exception is in its COPYING file. The source repository
does not contain the Python interpreter or the PyInstaller bootloader itself.

The Tcl/Tk notices here were obtained from the upstream 8.6 branch release:

- [Tcl notice](https://github.com/tcltk/tcl/blob/core-8-6-16/license.terms)
- [Tk notice](https://github.com/tcltk/tk/blob/core-8-6-16/license.terms)

The actual Tcl/Tk runtime version is read during the Windows bundle self-test.
These notices do not assert that a particular Python installation bundles
exactly 8.6.16. The cvar unlocker retains its separate MIT license and pinned
provenance under `third_party/cvar_unlocker/`.

The native in-game panel contains Dear ImGui, copyright Omar Cornut and
contributors, under its MIT license. The exact vendored revision and file
hashes are recorded in `native/vendor/imgui/UPSTREAM.json`. Its license and
MinHook's license are copied into the portable notices folder by the Windows
build script. The project uses the official Win32 and DirectX 11 backends.

Reconstructed clip audio uses ValveResourceFormat's Source2Viewer-CLI. Dolly
downloads the pinned official Windows CLI on first use and verifies the archive
and executable by SHA-256; the CLI is not bundled in the release. Its MIT
notice and pinned hashes are in `ValveResourceFormat-MIT.txt`.
