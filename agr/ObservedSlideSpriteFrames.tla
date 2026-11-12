------------------- MODULE ObservedSlideSpriteFrames -------------------
EXTENDS Naturals, TLC
\* Complete immutable native current-property frames -> sprite frame projection.
\* Implements observed_slide_sprite_states validation and whole-frame iteration
\* in blender_observed_slide_sprites. Two ticks, two root slots; Absent is an
\* observed absent CURRENT property, never proof of no orphan/native lifetime.
\* Empty and Positive stand for complete arrays for every pinned child/renderer;
\* Missing means unknown/truncated. Numeric/schema/profile/UV/clock proofs are
\* independent actual-code/native checks. Generic allocation/rollback remains
\* ModifierMaskRender; no new transaction or process ownership is introduced.
CONSTANTS Missing, Absent, Empty, Positive
Ticks == 0..1
Roots == 0..1
VARIABLES phase, native, identity, cursor, output, accepted
vars == <<phase,native,identity,cursor,output,accepted>>
Complete == \A t \in Ticks: \A r \in Roots:native[t][r] \in {Absent,Empty,Positive}
Qualified == identity /\ Complete
Init == /\ phase="validate" /\ native \in [Ticks->[Roots->{Missing,Absent,Empty,Positive}]]
        /\ identity \in BOOLEAN /\ cursor=0
        /\ output=[t \in Ticks|->[r \in Roots|->Missing]] /\ accepted=FALSE
Validate == /\ phase="validate"
            /\ phase'=IF identity /\ Complete THEN "project" ELSE "refused"
            /\ UNCHANGED <<native,identity,cursor,output,accepted>>
Project == /\ phase="project" /\ cursor<2
           /\ output'=[output EXCEPT ![cursor]=native[cursor]]
           /\ cursor'=cursor+1
           /\ UNCHANGED <<phase,native,identity,accepted>>
Publish == /\ phase="project" /\ cursor=2
           /\ phase'="done" /\ accepted'=TRUE
           /\ UNCHANGED <<native,identity,cursor,output>>
Returned == /\ phase \in {"done","refused"} /\ UNCHANGED vars
Next == Validate \/ Project \/ Publish \/ Returned
Spec == Init /\ [][Next]_vars
NoUnqualifiedGeometry == (cursor>0 \/ accepted) => Qualified
ExactCurrentRoots == \A t \in Ticks:t<cursor => output[t]=native[t]
CompletePublication == accepted => (Qualified /\ cursor=2 /\ phase="done")
=======================================================================
