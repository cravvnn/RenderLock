------------------------- MODULE ParticleWorldProvider -------------------------
EXTENDS Naturals, TLC
\* Synchronous Python->C#->package world provider admission. Independent input
\* bits abstract the pure typed/event/resource/range checks in particle_world_origin
\* and WorldOriginInputs; concrete types/defaults are Python/actual-helper tests.
\* Existing owned event lifecycle is unchanged; world admits no DESTROY. Native
\* allocation, simulation, Source phase and Blender geometry are separate proofs.
VARIABLES phase, validWorld, validSource, responseAck, requestMatch, accepted, published,
 existing, touched, packageBytes, validated
vars == <<phase,validWorld,validSource,responseAck,requestMatch,accepted,published,
 existing,touched,packageBytes,validated>>
MutSkipWorld == FALSE
MutSkipAck == FALSE
MutSkipSource == FALSE
MutSkipRequest == FALSE
MutOverwriteExisting == FALSE
MutPublishPartial == FALSE
Init == /\ phase="preflight" /\ validWorld \in BOOLEAN /\ validSource \in BOOLEAN
 /\ responseAck \in BOOLEAN /\ requestMatch \in BOOLEAN
 /\ existing \in BOOLEAN /\ touched=FALSE /\ packageBytes="none" /\ validated=FALSE
 /\ accepted=FALSE /\ published=FALSE
QualifyRequest == /\ phase="preflight"
 /\ accepted'=(validWorld \/ MutSkipWorld)
 /\ phase'=(IF (validWorld \/ MutSkipWorld) THEN "claim" ELSE "failed")
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,published,existing,touched,packageBytes,validated>>
ClaimOutput == /\ phase="claim"
 /\ phase'=(IF (~existing \/ MutOverwriteExisting) THEN "request" ELSE "failed")
 /\ touched'=(~existing \/ MutOverwriteExisting)
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,published,existing,accepted,packageBytes,validated>>
WriteRequest == /\ phase="request" /\ phase'="response"
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,published,existing,accepted,touched,packageBytes,validated>>
ValidatePackage == /\ phase="response"
 /\ validated'=(accepted /\ (responseAck \/ MutSkipAck) /\ (requestMatch \/ MutSkipRequest) /\ (validSource \/ MutSkipSource))
 /\ phase'=(IF (accepted /\ (responseAck \/ MutSkipAck) /\ (requestMatch \/ MutSkipRequest) /\ (validSource \/ MutSkipSource)) THEN "write" ELSE "failed")
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,accepted,published,existing,touched,packageBytes>>
WriteWhole == /\ phase="write" /\ phase'="done" /\ packageBytes'="complete" /\ published'=TRUE
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,accepted,existing,touched,validated>>
CrashDuringWrite == /\ phase="write" /\ phase'="failed" /\ packageBytes'="partial" /\ published'=MutPublishPartial
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,accepted,existing,touched,validated>>
FailBeforeWrite == /\ phase \in {"claim","request","response"} /\ phase'="failed"
 /\ UNCHANGED <<validWorld,validSource,responseAck,requestMatch,accepted,published,existing,touched,validated,packageBytes>>
Next == QualifyRequest \/ ClaimOutput \/ WriteRequest \/ ValidatePackage \/ WriteWhole \/ CrashDuringWrite \/ FailBeforeWrite
Spec == Init /\ [][Next]_vars
WorldAdmission == accepted => validWorld
AckBinding == published => responseAck
SourceBinding == published => (validSource /\ requestMatch /\ validWorld)
NoOverwrite == existing => ~touched
NoPartialSuccess == published => (validated /\ packageBytes="complete")
TypeOK == /\ phase \in {"preflight","claim","request","response","write","done","failed"}
 /\ accepted \in BOOLEAN /\ published \in BOOLEAN
=============================================================================
