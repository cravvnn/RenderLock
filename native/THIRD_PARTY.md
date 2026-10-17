# Native extractor third-party notices ? 0.2.1

The Windows executable is built from `src/main.rs` and the exact versions in `Cargo.lock`, using Rust 1.99.0 and the MSVC Windows toolchain. Boon is 0.15.0, boon-proto is 0.4.11094174+6759 and pbdems2 is 0.3.6. The helper reports adapter version 0.2.0 independently of application version 0.2.1.

The complete resolved package inventory, license expressions, upstream repositories and included notice filenames are in [dependencies.json](licenses/0.2.1/dependencies.json). License/notice texts for all 131 resolved packages are retained under `licenses/0.2.1/<package>-<version>/`. Notices from the original helper are preserved separately in `licenses/` for provenance.

Build: `cargo build --release --locked --manifest-path native/Cargo.toml`, then copy `native/target/release/agr-extract.exe` into `bin/`. Windows execution was tested on the target replay. The old Linux binary has been removed; a Linux build and validation are required before using this release on Linux.

Upstream replay parser: https://github.com/pnxenopoulos/boon . No game assets or game executable code are bundled.
