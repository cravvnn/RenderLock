---------------------- MODULE ProjectileEffectAdmission ----------------------
EXTENDS TLC
\* recorded_projectile_effects.build_projectile_effects -> projectile_particles
\* validate_package -> simulated_effect_package.build_package_effects -> merge.
\* One loop iteration; independent successful/unsupported iterations accumulate
\* through a pure report merge. Native capture/publication: ProjectileCapture.
\* Whole Blender ID transaction: ParticleModelAdmission. This model composes
\* those interfaces with fresh artifact dir, child success and bound response.
\* Hash/field arithmetic, helper internals, OS failures and GPU/native fidelity
\* are concrete checks/explicit exclusions, not established by these booleans.
VARIABLES phase, inputsQualified, freshDirectory, childSucceeded, responseBound,
          sourceCurrent, assetsQualified, renderable, nonempty, allocated, accepted
vars == <<phase,inputsQualified,freshDirectory,childSucceeded,responseBound,
          sourceCurrent,assetsQualified,renderable,nonempty,allocated,accepted>>
SkipInputs == FALSE
ReuseDirectory == FALSE
IgnoreChildFailure == FALSE
SkipResponse == FALSE
SkipSource == FALSE
SkipAssets == FALSE
KeepEmptyAllocation == FALSE
AcceptEmpty == FALSE
Init == /\ phase="inputs" /\ allocated=FALSE /\ accepted=FALSE
 /\ inputsQualified \in BOOLEAN /\ freshDirectory \in BOOLEAN /\ childSucceeded \in BOOLEAN
 /\ responseBound \in BOOLEAN /\ sourceCurrent \in BOOLEAN /\ assetsQualified \in BOOLEAN
 /\ renderable \in BOOLEAN /\ nonempty \in BOOLEAN
Inputs == /\ phase="inputs"
 /\ phase'=(IF inputsQualified \/ SkipInputs THEN "directory" ELSE "failed")
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,allocated,accepted>>
Directory == /\ phase="directory"
 /\ phase'=(IF freshDirectory \/ ReuseDirectory THEN "simulate" ELSE "failed")
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,allocated,accepted>>
Simulate == /\ phase="simulate"
 /\ phase'=(IF childSucceeded \/ IgnoreChildFailure THEN "package" ELSE "failed")
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,allocated,accepted>>
Package == /\ phase="package"
 /\ phase'=(IF (responseBound \/ SkipResponse) /\ (sourceCurrent \/ SkipSource)
               /\ (assetsQualified \/ SkipAssets) THEN "render" ELSE "failed")
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,allocated,accepted>>
Allocate == /\ phase="render" /\ allocated'=TRUE /\ phase'="result"
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,accepted>>
Complete == /\ phase="result" /\ renderable /\ (nonempty \/ AcceptEmpty)
 /\ accepted'=TRUE /\ phase'="done"
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,allocated>>
Rollback == /\ phase="result" /\ (~renderable \/ ~nonempty) /\ phase'="failed"
 /\ allocated'=(IF ~nonempty THEN KeepEmptyAllocation ELSE FALSE)
 /\ UNCHANGED <<inputsQualified,freshDirectory,childSucceeded,responseBound,
                sourceCurrent,assetsQualified,renderable,nonempty,accepted>>
Next == Inputs \/ Directory \/ Simulate \/ Package \/ Allocate \/ Complete \/ Rollback
Spec == Init /\ [][Next]_vars
QualifiedOnly == accepted => inputsQualified /\ freshDirectory /\ childSucceeded
 /\ responseBound /\ sourceCurrent /\ assetsQualified /\ renderable /\ nonempty
WholeInstanceRollback == phase="failed" => ~allocated
NoFailureSuccess == phase="failed" => ~accepted
=============================================================================
