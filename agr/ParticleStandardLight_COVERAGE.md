# Native-default particle light integration

`particle_standard_light` transforms immutable sampled values and the exact
qualified native profile into color, energy, radius and attenuation parameters.
It adds no runtime clock, cache, retry or process owner. Current Windows particle
module bytes are required when preparing new standard-light assets. Prepared
packets validate the explicit proof; a declared null or changed proof refuses.
Older packets without a proof retain their existing calibrated behavior and
cannot claim the new native-default conversion.

The current module's normalization/final-range/solver/bounds instructions passed
150 prepared detached cases; its color lookup passed 256 cases. Analytic sRGB
conversion has a declared absolute lookup error bound of 4e-7; it does not ship
the game's private lookup table. Named schema, constructor stores and RTTI-owned
POINT/FX branch dataflow are statically bound. The complete renderer/caller is
not executed, and no current live FX descriptor or Source pixel proof is claimed.

`ParticleModelAdmission.tla` models the reused source/default admission and
whole-package allocation rollback interface. Its arithmetic and Blender/driver
behavior are outside that finite abstraction. The light graph's new light,
isolated node Action and key data belong to the existing package transaction.
`configure_standard_light` refuses replacing an already animated node tree.
Radius is keyed before squaring at shader evaluation, preserving fractional
radius interpolation. It introduces no handler, playback state or cleanup owner.

Only default point/FX styles are admitted. Alternate shapes/attenuation,
color-scale providers and unsupported diffuse/specular/bounce controls refuse
before geometry. Explicit null settings do not become absent defaults.
EEVEE remains an approximate inverse-square/custom-distance adaptation; the
linear FX falloff graph is specific to Cycles. Light photometry, BRDF/fog,
external runtime tint and common submitted-frame timing remain unqualified.

Simplification: reuse existing simulator samples, scalar contexts, image/scene
assembly and package rollback instead of introducing live descriptor capture,
a second light population or an independent animation owner.

Acceptance: a fresh normal export produced one default POINT light in a saved
Cycles scene, with 72 effect objects including six retained mask variants.
Saved checks passed 19 sampled and 54 fractional light values, 143 object
bindings, 205 nonlight Actions, 65 effect mesh/UV fingerprints, 104 packed images,
null-proof refusal and rollback after an injected range-key failure. Legacy
packet values agree with the earlier baseline over all 21 selected frames.
Quadratic/linear node Action fixtures verify radius interpolation; the saved
actor passes 3,150 bone and 21 root checks. Four consecutive CPU Cycles frames
at 640x360/16 samples completed and were visually inspected. This is a fresh
normal default POINT-light proof, not a full FX replay or Source pixel match.

`tests/check_particle_standard_light_blender.py` provides a synthetic, asset-free
Blender regression for radius interpolation at 24/30/60 FPS, separate node/light
Actions and refusal to replace an animated shader. Those component fixtures do
not establish three separate fresh full exports or native photometric fidelity.

The first fresh normal export exposed factory EEVEE still being active during
effect construction, followed by a late switch to Cycles. Engine selection now
happens immediately after factory reset. `SceneRenderEngine.tla` models that
ordering and camera-before-effects, independently from allocation rollback.
The failed saved check is preserved; it is not a finite-range acceptance result.
A subsequent private verifier used the wrong total-scene count for one legacy
package. That failure was preserved and its slot returned before a separately
authorized corrected run passed the baseline component count of 46.
