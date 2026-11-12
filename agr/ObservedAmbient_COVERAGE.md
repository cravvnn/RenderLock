# Captured ambient component boundary

This is component/API integration for two pinned ambient sprite systems (three
renderers) and two pinned rope systems. The separate ordinary input layer is
described in `CapturedAmbientRender_COVERAGE.md`; it does not change the
four-component captured-slide cohort or introduce a runtime collector.
`simulated_effect_package.validate_package` admits each package independently;
`build_package_effects` dispatches it inside the existing allocation transaction.
The ordinary slide loader's same-capture checks are unchanged.

The retained sprite and rope inputs have the same historical owner-motion
fingerprint but different raw capture reports, freeze and cleanup provenance.
Neither shared Source clocks nor a single capture run are implied. References
keep the original fingerprints, profile definitions, native module pins and
separate provenance. They refuse the fresh export context directly. The existing
owner-binding check separately establishes byte-identical poses and assets for
the ordinary ambient input layer without rewriting original packages. Hashes detect
inconsistent inputs; they do not authenticate independently edited captures.

## State and composition

| Component | Owner or model | Scope |
| --- | --- | --- |
| `observed_rope_states` | `ObservedRopeFrames.tla` | Validate dense complete arrays, project each frame, publish only complete output. |
| `observed_sprite_states` | `ObservedSpriteFrames.tla` | Same finite array contract for the independently pinned sprite capture. |
| `observed_sprite_geometry`, shared sprite math/material, rope math | Stateless | Synchronous transforms only; no cache, process, file writer or frame handler. |
| `blender_observed_ropes`, `blender_observed_sprites` | Existing `build_package_effects` transaction | Allocate new datablocks and restore scene time in `finally`; remove all new datablocks on exceptions. |
| Generic transaction | Existing `ModifierMaskRender` allocation/rollback interface | Reuse its created-data ownership pattern, not its modifier-specific sustained-gate semantics. |

The two array models each enumerate two ticks and two collections, with missing,
known-empty and positive states. Guards require qualified identity and complete
arrays before projection; output slots must equal the current frame, and a
prefix cannot publish. Four mutants remove identity, completeness, exact-frame
copying and final completeness. Both models pass with 372 states and all four
mutants killed. They do not model numeric shader fields, native acquisition,
filesystem integrity, camera projection, actual Blender operations or process
crashes. These models do not certify the entire RenderLock application.

`test_observed_ambient_dispatch` checks the real component dispatcher against
fake bpy allocation sets, including a late exception across every tracked
datablock group. This is transaction logic evidence, not Blender rollback proof.
Real allocation failure, saved-scene reopen and rendered sequence checks remain
required before ordinary-export acceptance.

## Simplification and remaining fidelity

Reuse the existing captured-sprite material and geometry equations, sheet input
checks, constant visibility helper and package transaction. Do not copy the old
optional ambient preparation/report-merging wrappers or add another state owner.
The shared material function is AST-identical to the retained sprite material.
The port needs only its manual-frame profile admission guard and render loop.
No extra model states are introduced for shared stateless code.

Rope UV field 26 maps to measured `scratch_float1`; missing or nonfinite values
refuse. Only particle-normal orientation requires a normal stream. Screen and
world-Z orientations do not consume it. No normal or field-26 fallback is
synthesized. Other rope fading/clamping and finite-child-bound changes from the
research copy are excluded from this port.

Arrays hold between captured ticks. Frame-local ordinal slots are geometry
indices, never persistent particles, births, ages or seeds. Camera-dependent
geometry must be rebuilt after camera changes. Sprite collection time, manual
frames and shader random inputs remain measured. Rope scene light is neutral;
native render timing, root/endcap/orphan lifetime, shader/depth/fog/lighting
equivalence, map/hero interaction and Source pixel parity remain unqualified.
