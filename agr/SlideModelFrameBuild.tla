------------------------ MODULE SlideModelFrameBuild ------------------------
EXTENDS Naturals, TLC
\* blender_slide_model_frame import/build called by the allocation transaction.
\* Private check uses same snapshot/batch_remove owner as ModifierMaskRender;
\* normal observed-slide-model dispatch reuses that owner. Numeric qualification belongs
\* to particle_slide_mesh and its caller, not this finite allocation model.
\* Includes failure inside material import, mesh, copied graph and image loads.
VARIABLES phase, qualified, created, published, failed
vars == <<phase,qualified,created,published,failed>>
Init == /\ phase="validate" /\ qualified \in BOOLEAN
        /\ created=0 /\ published=FALSE /\ failed=FALSE
Validate == /\ phase="validate"
            /\ phase'=IF qualified THEN "build" ELSE "refused"
            /\ UNCHANGED <<qualified,created,published,failed>>
Allocate == /\ phase="build" /\ created<4 /\ created'=created+1
            /\ UNCHANGED <<phase,qualified,published,failed>>
Fail == /\ phase="build" /\ phase'="rollback" /\ failed'=TRUE
        /\ UNCHANGED <<qualified,created,published>>
Finish == /\ phase="build" /\ created=4 /\ phase'="done" /\ published'=TRUE
          /\ UNCHANGED <<qualified,created,failed>>
Rollback == /\ phase="rollback" /\ phase'="error" /\ created'=0
            /\ UNCHANGED <<qualified,published,failed>>
Returned == /\ phase \in {"done","error","refused"} /\ UNCHANGED vars
Next == Validate \/ Allocate \/ Fail \/ Finish \/ Rollback \/ Returned
Spec == Init /\ [][Next]_vars
QualifiedAllocation == created>0 => qualified
NoFailedPublication == published => (qualified /\ ~failed /\ created=4 /\ phase="done")
NoLeakedFailure == phase="error" => (created=0 /\ failed /\ ~published)
=============================================================================
