---- MODULE CravPreflight ----
EXTENDS Naturals
\* preflight_reconstruction, called by CLI plan-crav. Read-only, one invocation.
\* Actual byte hashing/stat checks are abstract stable/changed observations;
\* decoder identity equality is a boundary result, not a model of Boon.
\* Init follows the initial collection validation and file hashes. Recheck is
\* the final observations, not a lock on files through future extraction.
\* One permanent edit per file, no hostile edit-and-restore/ABA guarantee.
\* Cancellation after return and edits after final observation are outside the
\* result: downstream extraction must pin inputs again. No scene/export proof.
\* Simplification: no retained owner/cache/job state, only a per-call plan.
\* Optional prepared map: fully verified before decode (abstract), identity
\* rechecked after decode. Its byte verification is map_package.load_package.
VARIABLES pc, replayChanged, packageChanged, cancelled, metadataOK,
          sameReplay, samePackage, planned, hasMap, mapChanged, sameMap
vars == <<pc,replayChanged,packageChanged,cancelled,metadataOK,sameReplay,samePackage,planned,hasMap,mapChanged,sameMap>>
mapVars == <<hasMap,mapChanged,sameMap>>
Init == /\ pc="decode" /\ replayChanged=FALSE /\ packageChanged=FALSE
        /\ cancelled=FALSE /\ metadataOK=FALSE /\ sameReplay=FALSE
        /\ samePackage=FALSE /\ planned=FALSE
        /\ hasMap \in BOOLEAN /\ mapChanged=FALSE /\ sameMap=FALSE
ChangeReplay == /\ pc \in {"decode","recheck"} /\ ~replayChanged /\ replayChanged'=TRUE
                /\ UNCHANGED <<pc,packageChanged,cancelled,metadataOK,sameReplay,samePackage,planned>> /\ UNCHANGED mapVars
ChangePackage == /\ pc \in {"decode","recheck"} /\ ~packageChanged /\ packageChanged'=TRUE
                 /\ UNCHANGED <<pc,replayChanged,cancelled,metadataOK,sameReplay,samePackage,planned>> /\ UNCHANGED mapVars
ChangeMap == /\ hasMap /\ pc \in {"decode","recheck"} /\ ~mapChanged /\ mapChanged'=TRUE
             /\ UNCHANGED <<pc,replayChanged,packageChanged,cancelled,metadataOK,sameReplay,samePackage,planned,hasMap,sameMap>>
Cancel == /\ pc \in {"decode","recheck","return"} /\ ~cancelled
          /\ cancelled'=TRUE
          /\ UNCHANGED <<pc,replayChanged,packageChanged,metadataOK,sameReplay,samePackage,planned>> /\ UNCHANGED mapVars
Decode == /\ pc="decode" /\ metadataOK' \in BOOLEAN /\ pc'="recheck"
          /\ UNCHANGED <<replayChanged,packageChanged,cancelled,sameReplay,samePackage,planned>> /\ UNCHANGED mapVars
Recheck == /\ pc="recheck" /\ sameReplay'=~replayChanged /\ samePackage'=~packageChanged
           /\ sameMap'=(hasMap => ~mapChanged) /\ pc'="return"
           /\ UNCHANGED <<replayChanged,packageChanged,cancelled,metadataOK,planned,hasMap,mapChanged>>
Return == /\ pc="return" /\ pc'="done"
          /\ planned'=(sameReplay /\ samePackage /\ sameMap /\ metadataOK /\ ~cancelled)
          /\ UNCHANGED <<replayChanged,packageChanged,cancelled,metadataOK,sameReplay,samePackage>> /\ UNCHANGED mapVars
Done == /\ pc="done" /\ UNCHANGED vars
Next == ChangeReplay \/ ChangePackage \/ ChangeMap \/ Cancel \/ Decode \/ Recheck \/ Return \/ Done
Spec == Init /\ [][Next]_vars
StableReplay == planned => sameReplay
StablePackage == planned => samePackage
MatchingIdentity == planned => metadataOK
NotCancelled == planned => ~cancelled
StableMap == planned => (hasMap => ~mapChanged)
====
