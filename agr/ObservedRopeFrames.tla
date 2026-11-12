---------------------- MODULE ObservedRopeFrames ----------------------
EXTENDS Naturals, TLC
\* observed_rope_states validates complete immutable collection snapshots before
\* projecting geometry slots. Slots are local to each frame, never particle IDs.
\* Caller: simulated_effect_package.validate_package -> validate_package;
\* blender_observed_ropes -> geometry_frames. API only, no normal input loader.
\* Native count=0 is known empty; missing is not empty. No birth/seed/clock.
\* Generic package allocation/exception rollback uses the existing
\* ModifierMaskRender interface; JSON/native numeric semantics and camera/shader
\* fidelity are separate code/scene checks, not proved by this finite model.
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
