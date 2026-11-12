--------------------- MODULE CrossbowOwnerInputs ---------------------
EXTENDS Integers, TLC
\* particle_crossbow_owner.bind/validate/request -> recorded_crossbow_effects
\* prepare_package -> ReplayEventTimeline/CrossbowOwnerInputs.Qualify ->
\* SnapshotRuntimeInputs -> validate_response -> simulated_effect_package
\* validate/build + validate_source_snapshot_rig. The original event remains3.
\* Acquisition/math/renderer are external obligations, not modeled evidence.
VARIABLES ownerOK, childrenOK, birthOK, snapshotOK, ackOK, rigOK,
          phase, epoch, boundEpoch, completed, originalMode, published
vars == <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,phase,epoch,
          boundEpoch,completed,originalMode,published>>
Init == /\ ownerOK \in BOOLEAN /\ childrenOK \in BOOLEAN /\ birthOK \in BOOLEAN
        /\ snapshotOK \in BOOLEAN /\ ackOK \in BOOLEAN /\ rigOK \in BOOLEAN
        /\ phase="bind" /\ epoch=0 /\ boundEpoch=-1
        /\ completed=FALSE /\ originalMode=3 /\ published=FALSE
Bind == /\ phase="bind"
        /\ phase'=IF ownerOK /\ childrenOK /\ birthOK THEN "capture" ELSE "refused"
        /\ boundEpoch'=epoch
        /\ UNCHANGED <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,epoch,completed,originalMode,published>>
Capture == /\ phase="capture"
           /\ phase'=IF snapshotOK THEN "simulate" ELSE "refused"
           /\ UNCHANGED <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,epoch,boundEpoch,completed,originalMode,published>>
Simulate == /\ phase="simulate" /\ completed'=TRUE /\ phase'="response"
            /\ UNCHANGED <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,epoch,boundEpoch,originalMode,published>>
Response == /\ phase="response" /\ phase'=IF ackOK THEN "load" ELSE "refused"
            /\ UNCHANGED <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,epoch,boundEpoch,completed,originalMode,published>>
ChangeSource == /\ phase \in {"capture","simulate","response","load"} /\ epoch=0 /\ epoch'=1
                /\ UNCHANGED <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,phase,boundEpoch,completed,originalMode,published>>
Load == /\ phase="load"
        /\ published'=(epoch=boundEpoch /\ rigOK)
        /\ phase'=IF epoch=boundEpoch /\ rigOK THEN "done" ELSE "refused"
        /\ UNCHANGED <<ownerOK,childrenOK,birthOK,snapshotOK,ackOK,rigOK,epoch,boundEpoch,completed,originalMode>>
Terminal == /\ phase \in {"done","refused"} /\ UNCHANGED vars
Next == Bind \/ Capture \/ Simulate \/ Response \/ ChangeSource \/ Load \/ Terminal
Spec == Init /\ [][Next]_vars
OwnerBound == phase \notin {"capture","simulate","response","load","done"} \/ ownerOK
ChildrenBound == phase \notin {"capture","simulate","response","load","done"} \/ childrenOK
BirthBound == phase \notin {"capture","simulate","response","load","done"} \/ birthOK
SnapshotIndependent == phase \notin {"simulate","response","load","done"} \/ snapshotOK
Acknowledged == ~published \/ ackOK
CurrentSource == ~published \/ epoch=boundEpoch
SavedRig == ~published \/ rigOK
Complete == ~published \/ completed
OriginalEventPreserved == originalMode=3
======================================================================
