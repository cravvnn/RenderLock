# Contributing to RenderLock

Thanks for helping. RenderLock turns a selected section of a Deadlock replay into an animated Blender scene, and it is deliberately strict: unsupported data fails or reports itself, and never silently pretends to work. Contributions are held to the same standard.

## Before you start

- **Open an issue first for behavior changes.** Describe what you want to change and why before writing code, so effort is not spent on something that cannot land.
- **Never commit game assets, replays, or private data.** No `.dem` files, extracted game files, VPK contents, `.blend` scenes with packed game textures, or anything you do not have the right to redistribute. Reproductions should use synthetic data or data you are allowed to share.
- **Keep the fail-closed contract.** Do not let unsupported features look successful. Partial results stay explicitly partial, coverage reports keep listing what was and was not applied, and identity checks (replay, rig, bounds, task dependencies) stay strict.

## Setting up

See [Building](docs/BUILDING.md) for source, native helpers, pinned dependencies
and packaging, and the [guide index](docs/README.md) for user-facing workflows.

Requires 64-bit Python 3.12+ (3.14 is used for development) and Blender 5.2.x for scene work. Rust is needed only for the native replay extractor.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pytest
.venv\Scripts\python -m agr gui
.venv\Scripts\python -m pytest -q tests/test_external_map.py tests/test_export_recovery.py
```

The native replay extractor: `cargo build --release --locked --manifest-path native/Cargo.toml`, then copy the binary to `bin/agr-extract.exe`. The Windows portable package is staged with `tools/package_character_beta.py` (see Building).

Start with focused regression checks and broaden appropriately. Native Tk tests
can display windows; use a suitable isolated/non-visible desktop where supported
and record skipped or unverified native scope. Do not disrupt an active desktop
or describe mocked widgets as native/visual validation.

## What lives where

| Path | Contents |
| --- | --- |
| `agr/` | The application: CLI, desktop UI, replay and scene pipeline, Blender-side modules |
| `tests/` | Focused and broader regression checks, including optional native fixtures |
| `docs/` | User, lighting, CLI, troubleshooting and building guides |
| `native/` | Rust replay extractor and its licenses |
| `tools/` | Packaging, launcher and helper-build tooling |
| `research/` | Helper-tool sources (probe decoders, particle simulator) and the ValveResourceFormat patch set used for builds |
| `artwork/` | Project artwork and the app icon source |

## Making a change

1. Branch from `main`. Keep changes focused; one topic per pull request.
2. Match the surrounding style. The codebase prefers plain standard-library Python, small modules, explicit validation, and no new dependencies without discussing it first.
3. Add or update meaningful regression tests for behavior changes. Avoid tests that merely repeat prose or implementation details. Documentation edits need checked commands, links and feature scope.
4. Describe your validation in the pull request: what you ran, against which game build, and what you did **not** verify. Distinguish decoded data, numerical checks, saved-scene checks and visual inspection; never present one as another.
5. Update `CHANGELOG.md` for release behavior changes and the relevant guide when behavior or limits change. Keep the README as the entry point, link detailed workflows, and distinguish released features from experimental branches. Ensure new guides are included in source/portable documentation staging.

## Reporting bugs

Use the [support checklist](docs/TROUBLESHOOTING.md#reporting-an-issue). Include the support ZIP or startup log, exact steps, versions, game build, replay/range and requested features. Review paths and replay details before sharing. Only share data you have the right to distribute.

## Review and licensing

- Maintainers review for the fail-closed contract first, correctness second, style third.
- By contributing you agree that your work is licensed under the project's MIT license (`LICENSE`). Only submit work you have the right to license that way; if you adapted anything from another project (shaders, tools, documentation), say so and confirm its license permits it.
- AI-assisted work is welcome, but it is your responsibility: review it, test it, and be able to explain and maintain it.
- Be decent to each other. Harassment, bigotry or bad-faith behavior gets you removed from the project spaces.
