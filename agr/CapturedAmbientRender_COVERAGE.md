# Independent captured ambient inputs in ordinary exports

`captured_ambient_inputs` reads one bounded descriptor, its original scene and
two original packages. It reuses the existing slide transport's JSON decoder,
file-size/aggregate/node/depth limits, alias detection and reserved-output checks.
It does not call, loosen or replace slide same-run admission. All ambient native,
profile, array and material validators run independently before owner binding.
Capture hashes are preserved; report/freeze/cleanup provenance is retained per
component. Matching owner motion is not proof of a common capture clock.

The descriptor and bound snapshot are immutable synchronous inputs, with no
cache, retry, process, file writer or storage lifetime. They need no separate
lifecycle model. Source-package asset paths currently remain absolute. File
hashes detect inconsistent bytes; they do not authenticate edited captures.

`CapturedAmbientRender.tla` models `build_captured_ambient`: qualify fresh binding
and both components before allocation, finish each inner package, and require
the full receipt before publishing. Any inner or receipt failure rolls back all
new sibling datablocks. Preexisting IDs remain. Its two-component finite model
has21states and kills5mutants: omitted binding, omitted component admission,
early publication, incomplete rollback and removal of existing data. The
per-package transaction is the lower allocation interface. Numeric geometry,
JSON/native field details, Blender batch_remove, process crashes, saved scenes
and render appearance are not established by this finite model.

The CLI snapshots inputs before creating output work, binds the fresh animated
segment, passes the bound snapshot through the ordinary particle runtime and
requires the exact completion receipt before final publication even in partial
mode. The existing exclusive artifact directory/file creation owns audit output;
no new storage owner is introduced. Existing replay/rig/animation and strict
coverage guards remain unchanged. Explicit without-effects recovery removes the
new value option and clears its GUI selection; omission of required model bone
rules is withheld while capture input remains selected. Existing GUI settings
and explicit retry ownership are reused, with no new worker or automatic retry.

The receipt requires all321arrays and exact source point/empty-array totals for
every authored layer, plus independent source provenance and reference hashes.
Sprite meshes preserve nonempty arrays, including degenerate zero-alpha quads.
Ropes with fewer than two points or geometry proven outside homogeneous camera
planes can yield fewer meshes: their processed-array totals must still match.
This is not a missing-input-as-empty fallback. Mesh counts are bounded by input
arrays, must sum exactly across layers, and remain separate from recorded
particle births, rendered-instance counts and unsupported parent/lifecycle notes.

Simplification: reuse bounded input primitives, existing owner/pose binding,
component validators, two inner allocation transactions and one outer rollback.
Keep the four-component slide path unchanged. Avoid a second simulator, capture
clock synchronizer, cache or automatic acquisition workflow.

Headless CLI tests deliberately stub extraction/Blender to prove transport and
publication refusal; Tcl-only GUI tests exercise no native widgets. Real input
binding and actual ordinary export/save/rollback/Cycles evidence must be recorded
separately. Until those gates pass this is source wiring, not release-qualified
ambient support. Source timing/lifetime/endcaps/orphans, shader/depth/fog/light
parity, map interaction, portability, wider builds and Linux remain unqualified.
