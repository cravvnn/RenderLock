---------------------- MODULE ModifierMaskRender ----------------------
EXTENDS Naturals, TLC
\* Sustained modifier_mask_effects -> build_package_effects -> Blender objects.
\* Imported ModifierMaskPlan and capture/serial models qualify raw inputs.
\* This model requires sustained positive owner gates before allocation and
\* uses the existing snapshot/batch_remove owner for all new datablocks.
\* No measured reference/lifecycle, sampled-particle, native timing or cache
\* redesign is admitted by this additive component.
SkipInputs == FALSE
SkipSustained == FALSE
VARIABLES phase, valid, created, published, failed, cleaned, inputsOK, sustained
vars == <<phase,valid,created,published,failed,cleaned,inputsOK,sustained>>
Init == /\ phase="validate" /\ valid=FALSE /\ created=FALSE
        /\ published=FALSE /\ failed=FALSE /\ cleaned=FALSE /\ inputsOK=FALSE /\ sustained=FALSE
Validate == \E input \in BOOLEAN, gate \in BOOLEAN:
  LET qualified == (input \/ SkipInputs) /\ (gate \/ SkipSustained)
  IN /\ phase="validate" /\ valid'=qualified
     /\ inputsOK'=input /\ sustained'=gate
     /\ phase'=IF qualified THEN "render" ELSE "refused"
     /\ UNCHANGED <<created,published,failed,cleaned>>
Allocate == /\ phase="render" /\ phase'="finish" /\ created'=TRUE
            /\ UNCHANGED <<valid,published,failed,cleaned,inputsOK,sustained>>
Finish == \E success \in BOOLEAN:
  /\ phase="finish" /\ failed'=~success
  /\ phase'=IF success THEN "done" ELSE "rollback"
  /\ published'=success
  /\ UNCHANGED <<valid,created,cleaned,inputsOK,sustained>>
Rollback == /\ phase="rollback" /\ phase'="error"
            /\ created'=FALSE /\ cleaned'=TRUE
            /\ UNCHANGED <<valid,published,failed,inputsOK,sustained>>
Next == Validate \/ Allocate \/ Finish \/ Rollback
Spec == Init /\ [][Next]_vars
NoUnqualifiedGeometry == created => (valid /\ inputsOK /\ sustained)
NoFailedCoverage == published => (valid /\ ~failed /\ created)
NoLeakedFailure == phase="error" => (~created /\ cleaned /\ failed /\ ~published)
=======================================================================
