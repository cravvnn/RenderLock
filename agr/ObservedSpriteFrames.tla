---------------------- MODULE ObservedSpriteFrames ----------------------
EXTENDS Naturals, TLC
\* observed_sprite_states validates measured complete immutable collection frames
\* before blender_observed_sprites emits its three render layers. Two collections,
\* each mapped to fixed pinned renderers. Slots are local to a frame, never IDs.
\* Caller: simulated_effect_package.validate_package -> validate_package;
\* blender_observed_sprites -> build_frame. API only, no normal input loader.
\* Missing arrays are not known empty. Measured shader random/animation/clock
\* numeric semantics and authored profile pins are independent native/code checks.
\* Existing ModifierMaskRender generic package transaction owns allocation and
\* rollback; the abstract created-data set does not depend on the mesh count.
\* No new simulator lifetime, cache, playback handler or process owner is added.
\* Whole saved/moving output and Source lighting/pixel parity require scene checks.
CONSTANT Missing
Ticks == 0..1
Layers == 0..1
VARIABLES phase, native, identity, cursor, output, accepted
vars == <<phase,native,identity,cursor,output,accepted>>
Complete == \A t \in Ticks: \A r \in Layers:native[t][r] \in {0,1}
Qualified == identity /\ Complete
Init == /\ phase="validate" /\ native \in [Ticks->[Layers->{Missing,0,1}]]
        /\ identity \in BOOLEAN /\ cursor=0
        /\ output=[t \in Ticks|->[r \in Layers|->Missing]]
        /\ accepted=FALSE
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
Next == Validate \/ Project \/ Publish
Spec == Init /\ [][Next]_vars
NoUnqualifiedGeometry == (cursor>0 \/ accepted) => Qualified
ExactFrameArrays == \A t \in Ticks:t<cursor => output[t]=native[t]
CompletePublication == accepted => (Qualified /\ cursor=2 /\ phase="done")
=======================================================================
