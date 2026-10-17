# Animation research integration

These modules come from the user's saved Deadlock animation research packages.
Their original notices and the Esoterica and ValveResourceFormat MIT licenses
remain in this directory. Game models, compiled clips, and textures are separate
inputs and are not bundled with this Python package.

The AGR integration changes relative imports, makes recipe index widths explicit
from recorded tables, permits the actual recorded task registry to select known
operations, checks cached clip shapes and finite values, and rejects unsupported
secondary animation. `agr.animation` owns replay identity, provenance, contextual
mapping, range selection, stale pose handling, and per-entity output.

The legacy command-line entry points in these research modules are not AGR's
public interface. Use the top-level AGR launcher or `agr.animation.evaluate_segment`.
