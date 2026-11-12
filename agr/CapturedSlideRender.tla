------------------------ MODULE CapturedSlideRender ------------------------
EXTENDS Naturals, TLC
\* captured_slide_render.build_captured_slide_cohort owns all sibling allocations.
\* validate_bound_cohort abstracts input and same-run/component admission; their
\* numeric/native/material content is checked by the immutable input validators.
\* FinishComponent abstracts inner build_package_effects success; Fail includes
\* exceptions during an inner build or completion-receipt validation. CLI calls
\* validate_completion before publishing even when partial export is selected.
\* Existing per-package rollback models remain the lower allocation interfaces.
\* This finite four-component model does not prove Blender batch_remove behavior,
\* native appearance, saved-scene persistence or actual fresh export integration.
SkipBinding == FALSE
SkipCohort == FALSE
EarlyPublish == FALSE
SkipRollback == FALSE
DeleteOld == FALSE
VARIABLES phase, bindingOK, cohortOK, created, completed, published, failed, oldIDs
vars == <<phase,bindingOK,cohortOK,created,completed,published,failed,oldIDs>>
Init == /\ phase="validate" /\ bindingOK \in BOOLEAN /\ cohortOK \in BOOLEAN
        /\ created=0 /\ completed=0 /\ published=FALSE /\ failed=FALSE /\ oldIDs=2
Validate == /\ phase="validate"
            /\ phase'=IF (bindingOK \/ SkipBinding) /\ (cohortOK \/ SkipCohort)
                       THEN "build" ELSE "refused"
            /\ UNCHANGED <<bindingOK,cohortOK,created,completed,published,failed,oldIDs>>
Allocate == /\ phase="build" /\ completed<4 /\ created=completed
            /\ created'=created+1 /\ phase'="finish"
            /\ UNCHANGED <<bindingOK,cohortOK,completed,published,failed,oldIDs>>
FinishComponent == /\ phase="finish" /\ completed'=completed+1 /\ phase'="build"
                   /\ UNCHANGED <<bindingOK,cohortOK,created,published,failed,oldIDs>>
Fail == /\ phase \in {"build","finish"} /\ phase'="rollback" /\ failed'=TRUE
        /\ UNCHANGED <<bindingOK,cohortOK,created,completed,published,oldIDs>>
Publish == /\ phase="build" /\ (completed=4 \/ EarlyPublish)
           /\ phase'="done" /\ published'=TRUE
           /\ UNCHANGED <<bindingOK,cohortOK,created,completed,failed,oldIDs>>
Rollback == /\ phase="rollback" /\ phase'="error"
            /\ created'=IF SkipRollback THEN created ELSE 0
            /\ oldIDs'=IF DeleteOld THEN 0 ELSE oldIDs
            /\ UNCHANGED <<bindingOK,cohortOK,completed,published,failed>>
Next == Validate \/ Allocate \/ FinishComponent \/ Fail \/ Publish \/ Rollback
Spec == Init /\ [][Next]_vars
QualifiedAllocation == created>0 => (bindingOK /\ cohortOK)
AllOrNothingPublication == published => (completed=4 /\ created=4 /\ ~failed)
NoFailedLeak == phase="error" => (created=0 /\ ~published)
ExistingUntouched == oldIDs=2
=============================================================================
