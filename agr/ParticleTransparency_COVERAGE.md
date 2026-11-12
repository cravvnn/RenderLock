# Cycles transparency for generated particle scenes

`configure_particle_transparency` runs after effects construction and before
new-scene publication. When Cycles has rendered geometry, it raises transparent
bounces to at least 128 and preserves a higher existing value. It does not
modify materials or particle arrays. The existing new-scene export lifecycle
owns saving/failure; no separate cache, process, persistent writer or transaction
is introduced. Existing saved projects are not edited by export preparation.

`SceneRenderEngine` models renderer selection, camera/effects ordering, this
setting and publication. Finite cases cover geometry/no geometry, Cycles/Eevee,
and initial budgets 8/128/256. Five guard mutants cover early-engine selection,
camera readiness, omitted transparency setup, reducing a higher value and
changing unrelated scenes. The model checks ordering/settings, not Blender
rendering or geometry. Actual saved/reopened scene and Cycles image controls
remain separate obligations.

A dense captured-smoke control kept camera, geometry, materials, resolution and
samples fixed. At 8 transparent bounces it showed black card edges; at 128 those
edges disappeared. This supports the bounded rendering fix, not a guarantee for
unlimited overlap, original Source filtering/framebuffer behavior or lighting.
