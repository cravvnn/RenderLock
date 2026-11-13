# Bound slide rendering and ordinary export dispatch

`CapturedSlideRender.tla` models `build_captured_slide_cohort`: fresh binding
and complete cohort admission precede allocation; all four children must finish;
any child or receipt failure rolls back all new sibling IDs, preserving existing
objects, images and Actions. The lower per-package builder retains its existing
snapshot/batch-remove boundary. The outer owner is necessary: an inner rollback
cannot remove a sibling which completed before the failing child started.

The implementation snapshots each data group once and reuses the existing
four renderer dispatches. There is no new per-component persistent status store,
retry, fallback, copied renderer or alternative material implementation. The
finite model covers four component allocations and failure between/inside them,
with five guard mutants. Numerical capture admission remains with the input and
component validators; real Blender rollback/save/render verification is separate.

`cli._convert` reads `--captured-slide-inputs` before creating export work,
requires installed particle simulation, model constraints, explicit `--partial`
consent for the incomplete captured clock/renderer scope, and the exact admitted
range/history, and binds against the freshly evaluated output segment. The bound
snapshot travels through the existing particle runtime and is retained in the
effects audit directory. `build_recorded_effects` adds the cohort after ordinary
recorded/projectile/modifier work. The final CLI completion check runs before
publication, even under `--partial`; a missing/mismatched/failed explicit cohort
cannot be silently published as successful. Existing unsupported recorded birth
counts, rendered instance counts and strict/partial policies are not replaced.

This supplied cohort passed actual fresh normal-export, saved-scene,
all-component rollback and CPU Cycles acceptance. Headless command/publication tests
stub extraction and Blender explicitly and do not qualify native widgets, saved
data or rendering. Automatic live acquisition is not provided by this renderer.
Historical native/capture pins and original asset directories remain.

The Advanced page now exposes the same optional input through existing path-row
widgets. `gui.build_convert_command` is stateless and calls the shared option
admission function; the default is empty. Path normalization and preferences use
the unchanged generic GUI settings owner, with no new cache, writer, retry or
worker lifecycle. Headless Tcl checks cover settings round trips and explicit
partial consent is not remembered or enabled by the capture selection. This
additive field has no separate lifecycle machine; it delegates persistence to
the same settings path used by other optional particle inputs. Native widget
layout/appearance is not qualified by headless checks.

`export_recovery` remains a pure opt-in argv transform: explicitly continuing
without effects removes both supplied-input options and clears their GUI values.
It does not run a retry or alter the existing consent/worker lifecycle. A model-
rules omission is not offered while these inputs require those rules; the user
can review/clear them. No new recovery state owner or machine is introduced.
