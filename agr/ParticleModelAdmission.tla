------------------------ MODULE ParticleModelAdmission ------------------------
EXTENDS TLC
\* particle_model_assets.prepare_model/validate_model, texture constructor-default
\* provenance and model sample preflight. The same defaultsQualified interface
\* also admits native_standard_light source/default provenance and light-shape
\* preflight; immutable parameter math adds no owner or lifecycle.
\* then simulated_effect_package.build_package_effects transaction. Source cache
\* completion/hash verification is an abstract interface to AssetConverter._entry;
\* this adapter adds no cache manager, retries, concurrency or crash recovery.
\* Renderer may allocate geometry, Key, material, image, light and isolated
\* embedded node Action before a late
\* failure. Every newly owned ID must be removed; preexisting IDs are untouched.
\* CLI invokes recorded_effect_simulation then this normal package transaction.
\* The exact owned literal birth provider uses these same abstract interfaces:
\* sourceCurrent includes full replay/event/range/native+definition identities;
\* captureComplete includes helper request/event/materialized-CP acknowledgments.
\* Its Python functions are pure, with no new schedule/clock or RELEASE state.
\* The static sphere uses sourceCurrent for pinned model/material/GLB identity
\* and renderable for complete per-sample checks. GeometryExport separately
\* models the distinct static helper command, with no failed-morph fallback.
\* native_rope_shader uses the same defaultsQualified/sourceCurrent gates for
\* current native seed/blend and installed shader identities. Depth proxy math,
\* immutable mesh attributes and layer composition add no clock/cache/lifecycle.
\* Shader fields39/40 add numeric simulator state, emitted through captureComplete;
\* their arithmetic/storage is checked by actual initializer/operator fixtures,
\* not by pretending finite protocol states prove float or shader arithmetic.
\* Native capture/source arithmetic, filesystem races and Blender internals are
\* excluded. Actual tests cover attributes/hash failures and late bpy rollback.
VARIABLES phase, sourceCurrent, defaultsQualified, captureComplete, renderable, allocated, accepted
vars == <<phase,sourceCurrent,defaultsQualified,captureComplete,renderable,allocated,accepted>>
SkipIdentity == FALSE
SkipDefaults == FALSE
GuessCapture == FALSE
KeepFailedObjects == FALSE
AcceptFailure == FALSE
Init == /\ phase="assets" /\ allocated=FALSE /\ accepted=FALSE
 /\ sourceCurrent \in BOOLEAN /\ defaultsQualified \in BOOLEAN /\ captureComplete \in BOOLEAN /\ renderable \in BOOLEAN
Assets == /\ phase="assets"
 /\ phase'=(IF (sourceCurrent \/ SkipIdentity) /\ (defaultsQualified \/ SkipDefaults) THEN "capture" ELSE "failed")
 /\ UNCHANGED <<sourceCurrent,defaultsQualified,captureComplete,renderable,allocated,accepted>>
Capture == /\ phase="capture"
 /\ phase'=(IF captureComplete \/ GuessCapture THEN "render" ELSE "failed")
 /\ UNCHANGED <<sourceCurrent,defaultsQualified,captureComplete,renderable,allocated,accepted>>
Allocate == /\ phase="render" /\ allocated'=TRUE /\ phase'="result"
 /\ UNCHANGED <<sourceCurrent,defaultsQualified,captureComplete,renderable,accepted>>
Complete == /\ phase="result" /\ renderable /\ accepted'=TRUE /\ phase'="done"
 /\ UNCHANGED <<sourceCurrent,defaultsQualified,captureComplete,renderable,allocated>>
Rollback == /\ phase="result" /\ ~renderable /\ phase'="failed"
 /\ allocated'=KeepFailedObjects /\ accepted'=AcceptFailure
 /\ UNCHANGED <<sourceCurrent,defaultsQualified,captureComplete,renderable>>
Next == Assets \/ Capture \/ Allocate \/ Complete \/ Rollback
Spec == Init /\ [][Next]_vars
QualifiedOnly == accepted => sourceCurrent /\ defaultsQualified /\ captureComplete /\ renderable
WholeInstanceRollback == phase="failed" => ~allocated
NoFailureSuccess == phase="failed" => ~accepted
=============================================================================
