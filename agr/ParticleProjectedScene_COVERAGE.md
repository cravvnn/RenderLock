# Explicit no-world-geometry layer

This conditional path handles the actual retained hero-only scene, without
inventing a map surface or substituting the hero meshes. Its exact projected
renderer excludes characters and enables world/water. Any map, unknown geometry
or collection/other instancing refuses the entire instance before bake. Hidden
geometry is classified too; animation cannot hide a future receiver from this
check. The receiver predicate is checked again before returning success.
RenderLock's explicitly marked reference-grid curve is a review helper, not a
Source world receiver. Fresh grids carry this role; unmarked older curves refuse.
The retained legacy grid was separately inspected before adding role metadata.
Fresh world preparation pins both client.dll and particles.dll because the
authored definition alone does not declare all measured renderer defaults.

`ParticleProjectedScene` models these checks with geometry appearing between
preflight and finish, independently for map, unknown and instanced geometry.
The synchronous classification has no cache or retained object handles. The
existing package wrapper removes newly created Blender data after a raised
exception; its actual Blender rollback is a separate API/caller check, not a
claim that this finite model proves Blender internals or all other application
components. Whole-program proof remains incomplete.

This never qualifies a Source/game-world empty effect, projected shader, missing
opacity input, GPU clipping, actual map surface or future appended map. Reports
state the exported-scene condition and require rebuilding after adding geometry.
The nonprojected explosion layers can render in this scene while map projection
remains unfinished. No owned lifecycle gate or unknown renderer gate is relaxed.

Simplification: use one derived scene-role predicate before and after the existing
bake; no new persistent empty-result cache, object lifecycle or geometry alias.

Deferred map composition is also a receiver: build_scene marks legacy external
maps, and build_map_first explicitly passes the same pending-world context into
its temporary character stage. That marker is checked before helper work and
again by the package renderer. It is not a .crav/replay field or CLI option.
The model's pending bit covers this declared future composition; a dedicated
mutant removes that guard; separate mutants exercise world, unknown and instanced geometry. This closes false empty-receiver admission without
claiming projected map rendering or changing the map's loading/texture pipeline.
