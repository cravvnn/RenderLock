# Live card depth correction

The candidate replaces interpolated inverse-scale shape deltas with a dependency
graph evaluated at the current frame. World positions are computed using the
full object matrix, adjusted along the camera ray to preserve raster XY, then
transformed back using the current inverse object matrix. Point-domain depth
ratios and corner-domain weighted UVs are stored before changing positions;
material attribute division preserves source UV interpolation.

This per-evaluation graph has no application cache, clock, retry or frame
handler. It derives values from existing scene dependencies. Its allocation
boundary shares `ParticleDepthCardBake`: qualify all supplied/24/30/60 camera
poses before installing nodes; restore the original frame in finally; caller's
existing package transaction removes owned objects/materials/node groups after
failure. The old experimental sampled helper remains disconnected. Its
fractional geometry failure is preserved and is not evidence for the new graph.

Current admission supports effective constant bias and PERSP/ORTHO cameras with
rigid world transforms. Bounds use the existing conservative depth-floor and
near/far refusal. Behind-camera points retain their original positions and unit
UV ratios, matching the pure preflight's wholly-clipped branch. A private test
that bypasses the vector guard reproduces the otherwise possible visible ghost.
Mixed camera-plane geometry remains refused rather than qualified by this guard.
Camera/projection changes require requalification; arbitrary
new paths, clipping-floor behavior, motion blur/shutter intervals, game lighting,
Source framebuffer/shader parity and all other application components are not
proved by this model. Actual fractional-frame geometry and rendered UV checks
are required separately. Whole-application proof is incomplete.

Simplification: remove stored depth poses and animated UV ratio inputs. Current
geometry and UV weights derive from the same evaluation, so animated object scale
cannot multiply an interpolated inverse-scale displacement. No new frame handler
or persistent camera cache is introduced. The 22-state allocation model and
three killed mutants cover the unchanged caller transaction boundary only.

Published-renderer integration: the b27 renderer predates the development
render_scalar/render_contexts interface. This release uses its existing strict
scalar validator for literal/constant bias and refuses dynamic providers; no
development renderer, world provider or material-expression support is imported.
The small numeric arithmetic used by particle_depth_card_uv is copied verbatim
from the handoff dependency into that module, avoiding the unrelated full
texture-layer module. The normal build_package_effects transaction was tested
on the merged branch for zero/literal/wrapped bias and three refusal/rollback
cases. Full handoff-renderer hashes are not claimed for this narrow public port.
