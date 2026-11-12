------------------------ MODULE ParticleModelAdmission ------------------------
EXTENDS TLC
\* particle_model_assets.prepare_model/validate_model and model sample preflight,
\* then simulated_effect_package.build_package_effects transaction. Source cache
\* completion/hash verification is an abstract interface to AssetConverter._entry;
\* this adapter adds no cache manager, retries, concurrency or crash recovery.
\* Model renderer may allocate geometry, Key, material and image before a late
\* failure. Every newly owned ID must be removed; preexisting IDs are untouched.
\* CLI invokes recorded_effect_simulation then this normal package transaction.
\* Native capture/source arithmetic, filesystem races and Blender internals are
\* excluded. Actual tests cover attributes/hash failures and late bpy rollback.
VARIABLES phase, sourceCurrent, captureComplete, renderable, allocated, accepted
vars == <<phase,sourceCurrent,captureComplete,renderable,allocated,accepted>>
SkipIdentity == FALSE
GuessCapture == FALSE
KeepFailedObjects == FALSE
AcceptFailure == FALSE
Init == /\ phase="assets" /\ allocated=FALSE /\ accepted=FALSE
 /\ sourceCurrent \in BOOLEAN /\ captureComplete \in BOOLEAN /\ renderable \in BOOLEAN
Assets == /\ phase="assets"
 /\ phase'=(IF sourceCurrent \/ SkipIdentity THEN "capture" ELSE "failed")
 /\ UNCHANGED <<sourceCurrent,captureComplete,renderable,allocated,accepted>>
Capture == /\ phase="capture"
 /\ phase'=(IF captureComplete \/ GuessCapture THEN "render" ELSE "failed")
 /\ UNCHANGED <<sourceCurrent,captureComplete,renderable,allocated,accepted>>
Allocate == /\ phase="render" /\ allocated'=TRUE /\ phase'="result"
 /\ UNCHANGED <<sourceCurrent,captureComplete,renderable,accepted>>
Complete == /\ phase="result" /\ renderable /\ accepted'=TRUE /\ phase'="done"
 /\ UNCHANGED <<sourceCurrent,captureComplete,renderable,allocated>>
Rollback == /\ phase="result" /\ ~renderable /\ phase'="failed"
 /\ allocated'=KeepFailedObjects /\ accepted'=AcceptFailure
 /\ UNCHANGED <<sourceCurrent,captureComplete,renderable>>
Next == Assets \/ Capture \/ Allocate \/ Complete \/ Rollback
Spec == Init /\ [][Next]_vars
QualifiedOnly == accepted => sourceCurrent /\ captureComplete /\ renderable
WholeInstanceRollback == phase="failed" => ~allocated
NoFailureSuccess == phase="failed" => ~accepted
=============================================================================
