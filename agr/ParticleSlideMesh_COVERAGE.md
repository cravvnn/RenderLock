Port status: component API only; no ordinary CLI cohort input or fresh normal slide acceptance yet. Historical native pins and original fingerprints remain.

# Slide mesh projection

`particle_slide_mesh.prepare_slide_model_frame` is a pure numeric leaf over an
explicit affine matrix, static geometry, bound material frame and instance RGBA.
It allocates no Blender/file/process resources, holds no state between calls and
does not infer a lifetime, so no independent TLA+ lifecycle is needed.

The exact material and selected VS/PS identities bound this profile. VCS defaults
for quantization and secondary selection and authored disabled model-axis UV
scaling are fixed for this material. UV scroll precedes the derived transform;
V flips once after that transform. Normals retain inverse-transpose affine math.
Repeated-index triangles are counted and excluded before Blender polygon
creation: the pinned source has four zero-area padding triangles. No other
geometry repair, decimation or face rejection is accepted.

The caller owns capture/cohort/instance-color provenance and resource identity.
This helper does not admit partial capture as a full export. Derived host trig,
gamma conversion and Blender PBR/filtering are approximations, distinct from the
qualified VS/PS numeric leaves. Saved-scene/material allocation and publication
must use the existing package rollback owner; this pure helper publishes none.

`blender_slide_model_frame` is an internal allocation component, still absent
from normal export dispatch. `SlideModelFrameBuild.tla` models its validate,
four-stage allocation, failure, complete publication and caller rollback. Its
default finite check passes 15 states and three guard-removal mutants fail.
`ModifierMaskRender` is the existing intended transaction owner. The private
component check uses its snapshot/batch_remove boundary; it does not establish
that the new backend is integrated into `build_package_effects`.

The material-only pinned VRF bundle supplies existing packed-map conversion.
The component replaces UV, tint and the two opacity-mask inputs, accepts zero
self illumination only, validates decoded texture dimensions, and preserves
complete corner normals. The isolated saved/reopened component and two renders
are distinct from dense clip coverage, Source PBR parity or final-scene approval.

`prepare_slide_model_frame_from_cmdl` binds exact current instance/module/type,
native setter identity, complete slot and raw float4 association. It consumes the
game's evaluated output instead of reconstructing blend/desaturation/white-RGB
or using lifetime field1 as opacity. It remains pure and has no new lifecycle.
The observed CPU color and alpha are retained separately from the explicit
Blender host sRGB approximation; final packed GPU color remains unqualified.
