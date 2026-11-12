# Silence startup cylinder and affine transform hierarchy

The exact pinned Silence startup renderer/model/material is admitted through the
existing normal model-asset preparation, renderer preflight and model dispatch.
Other static model selectors, including Mina's channel shell, remain refused.
Only the existing explicit `--particle-static-model-v1` command and its static
policy are accepted. There is no fallback from failed morph conversion. Legacy
retained morph-policy descriptors remain unchanged and refuse this new path.

The pure descriptor/sample validators own no state or writer. They require exact
renderer, source/parsed material identities, one static root, no animations/body
groups, bounded byte-hashed embedded GLB geometry, consecutive integer samples,
finite model transforms, unit opacities and captured material/auxiliary fields.
Missing fields26/27 cannot become invented values. ALPHATEST is retained but is
not read by the pinned compiled material; OFFSET drives translucent UV velocity.
Normal preparation's existing content-verified cache owns helper/output lifetime;
its identity now includes this profile. The cache protocol is not redesigned.

The startup material reuses the retained reference PBR/alpha graph. Source
lighting, packed normal response, depth behavior and exact material-clock phase
remain approximate. The original parsed material and texture bytes are checked;
this is not general dynamic Source material-VM evaluation.

For non-local anisotropic scale the mesh is parented under a component-scale
node and normal-frame node. Their product is frame * scale * Euler, preserving
shear instead of decomposing it into one lossy Blender TRS. Representable paths
explicitly use quaternion mode. A foreign parent is refused before mode changes
or allocation; changed parents/axes or invalid units refuse before key writes.
Each constructor is synchronous. Existing package allocation snapshots own all
new meshes, parents, materials/images/actions; scene time is restored in finally.

`ModelTransformHierarchy.tla` covers call-time parent/axis admission, paired root
and leaf key writes and complete publication after the owned constructor returns.
Its21states and4guard mutants check ownership, fixed axes, final root and final
leaf. Numeric shader/geometry fields, constructor details, real Blender matrices,
saved actions and rollback are separate code/runtime obligations. The model
composes with existing package rollback and tick-loop ownership; it is not a
whole-program proof. Fake Blender tests reconstruct the numeric product and key
targets, but do not substitute for actual saved-scene matrices.

Simplification: port only the pinned Silence renderer/material and shared affine
hierarchy. Reuse current static geometry command/reader, cache, model dispatch,
visibility interpolation and allocation transaction. Do not import the old
generic static-model adapter or Mina material profile, loosen animated selectors,
relabel old geometry output, or add a second simulator/process owner.

Historical startup material/transform studies and current offline retained-byte
checks establish inputs and arithmetic only. Fresh static conversion, actual
Blender construction/reopen/rollback, full normal Silence input/provider admission
and short Cycles renders remain required before acceptance. Native birth/child,
CP0 hitbox/model selection, authored CP3/4 and batch inputs remain separate work.
