------------------- MODULE ObservedSlideTrailFrames -------------------
EXTENDS Naturals, TLC
\* observed_slide_trail_states validates immutable same-run sprite/root proof,
\* uploaded trail frames, native/profile/layout identity before Blender geometry.
\* blender_observed_slide_trails projects whole current arrays in tick order.
\* Two ticks/two root slots: Absent != Empty != Missing. No particle IDs,
\* inferred births, lifetime joins or cache. Numeric/native evidence is separate.
\* simulated_effect_package owns allocation/rollback (ModifierMaskRender).
\* The same complete-array interface is reused by observed_slide_light_states
\* and blender_observed_slide_lights: root_count/particle_count distinguish
\* missing arrays from known empty; native quadratic-profile admission supplies
\* renderer qualification. This abstraction does not prove light shader math.
\* observed_slide_model_states/blender_observed_slide_models use this immutable
\* complete-array interface too: explicit current empty arrays, native material/
\* cohort qualification and dense ticks. Numeric/asset math remains separate.
CONSTANTS Missing, Absent, Empty, Positive
Ticks == 0..1
Roots == 0..1
VARIABLES phase, native, identity, renderer, cursor, output, accepted
vars == <<phase,native,identity,renderer,cursor,output,accepted>>
Complete == \A t \in Ticks: \A r \in Roots:native[t][r] \in {Absent,Empty,Positive}
Qualified == identity /\ renderer /\ Complete
Init == /\ phase="validate" /\ native \in [Ticks->[Roots->{Missing,Absent,Empty,Positive}]]
        /\ identity \in BOOLEAN /\ renderer \in BOOLEAN /\ cursor=0
        /\ output=[t \in Ticks|->[r \in Roots|->Missing]] /\ accepted=FALSE
Validate == /\ phase="validate"
            /\ phase'=IF identity /\ renderer /\ Complete THEN "project" ELSE "refused"
            /\ UNCHANGED <<native,identity,renderer,cursor,output,accepted>>
Project == /\ phase="project" /\ cursor<2
           /\ output'=[output EXCEPT ![cursor]=native[cursor]]
           /\ cursor'=cursor+1
           /\ UNCHANGED <<phase,native,identity,renderer,accepted>>
Publish == /\ phase="project" /\ cursor=2
           /\ phase'="done" /\ accepted'=TRUE
           /\ UNCHANGED <<native,identity,renderer,cursor,output>>
Returned == /\ phase \in {"done","refused"} /\ UNCHANGED vars
Next == Validate \/ Project \/ Publish \/ Returned
Spec == Init /\ [][Next]_vars
NoUnqualifiedGeometry == (cursor>0 \/ accepted) => Qualified
ExactCurrentRoots == \A t \in Ticks:t<cursor => output[t]=native[t]
CompletePublication == accepted => (Qualified /\ cursor=2 /\ phase="done")
=======================================================================
