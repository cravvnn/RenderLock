# Paired-sheet motion-vector rendering

The retained shader implementation is connected to the existing normal
`blender_simulated_effects` sprite-material path. It prepares raw current/next
flow samples using base-layer UV controls, warps the two color samples with
opposite frame weights, and excludes flow from color composition. Its signed
maximum distance and source dimensions come from qualified source-sheet metadata.
Unpaired/missing metadata, mismatched clocks and trail/rope modes refuse.
Non-flow material branches keep their old graph and frame-selection behavior.

The pure helper owns no cache, writer, clock or cross-call state. The existing
texture parser/cache supplies immutable metadata; `build_sheet_nodes` owns keyed
atlas UV/frame selection, and the existing package transaction owns any new
images/materials/actions. No new cache, player, process or retry owner is added.
`MotionVectorSheet.tla` models qualification followed by separate raw samples,
both warps and composition before publication. The finite model has13states and
seven guard mutants for pairing, metadata, raw sampling, frame independence,
current warp, next warp and excluding flow from color. Numeric equations, actual
Blender graphs, saved pixels, native filtering and normal-export acceptance must
be checked separately. This is not a whole-program proof.

Simplification: reuse the existing sheet-node sampler/UV/fraction interface and
shared layer interpreter. Do not copy the research material builder wholesale;
the current render defaults, age snapshots, per-particle controls, native/profile
checks and non-flow paths remain intact. Renderer preflight checks pair admission
before allocation. Existing inner/outer package transactions remain responsible
for exceptions during graph creation.

Historical Silence smoke has pixel and normal-helper evidence, including signed
distance-3 and two64-frame1024atlases. Later normalized-batch evidence fixes the
earlier zero-alpha/radius swirl defect. Neither old proof alone qualifies the
new source tree or the entire Silence effect. Current Silence still needs its
separate birth/child/hitbox/static-model/material/native/helper provider work.
Fresh ordinary export, actual saved/rollback checks and Cycles frames remain
required before marking this port accepted. No current native pins are widened;
reference flow response is not Source pixel, timing or lighting equivalence.
