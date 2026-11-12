----------------------- MODULE HitboxFrameHistory -----------------------
EXTENDS Naturals, Integers, TLC
\* Models Query called by HitboxRuntimeInputs and compiled-code oracles.
\* Single-threaded atomic Query; immutable validated shape arrays represented
\* by frame times. Two CPs, two identities, three times. Native lookup/math,
\* allocation, bad numerical inputs and exceptions are outside this model;
\* native_cp_history.py and actual C# boundary checks cover those separately.
\* No concurrent callback/cache publication: Query takes prebuilt immutable data.
CONSTANTS CPs, Owners
VARIABLES frame, previous, owner, expectedFrame, expectedPrevious, expectedOwner
vars == <<frame, previous, owner, expectedFrame, expectedPrevious, expectedOwner>>
Init == /\ frame = [cp \in CPs |-> -1]
        /\ previous = [cp \in CPs |-> -1]
        /\ owner = [cp \in CPs |-> "none"]
        /\ expectedFrame = frame
        /\ expectedPrevious = previous
        /\ expectedOwner = owner

Query(cp,t,b) ==
  LET valid == (owner[cp] = "none" \/ owner[cp] = b) /\ t >= frame[cp]
      referenceValid == (expectedOwner[cp] = "none" \/ expectedOwner[cp] = b) /\ t >= expectedFrame[cp]
      advance == valid /\ t # frame[cp]
      referenceAdvance == referenceValid /\ t # expectedFrame[cp]
  IN /\ frame' = IF advance THEN [frame EXCEPT ![cp] = t] ELSE frame
     /\ previous' = IF advance THEN [previous EXCEPT ![cp] = frame[cp]] ELSE previous
     /\ owner' = IF advance THEN [owner EXCEPT ![cp] = b] ELSE owner
     /\ expectedFrame' = IF referenceAdvance THEN [expectedFrame EXCEPT ![cp] = t] ELSE expectedFrame
     /\ expectedPrevious' = IF referenceAdvance THEN [expectedPrevious EXCEPT ![cp] = expectedFrame[cp]] ELSE expectedPrevious
     /\ expectedOwner' = IF referenceAdvance THEN [expectedOwner EXCEPT ![cp] = b] ELSE expectedOwner

Next == \E cp \in CPs, t \in 0..2, b \in Owners: Query(cp,t,b)
Spec == Init /\ [][Next]_vars
TypeOK == /\ frame \in [CPs -> -1..2]
          /\ previous \in [CPs -> -1..2]
          /\ owner \in [CPs -> (Owners \cup {"none"})]
HistoryOrder == \A cp \in CPs: previous[cp] < frame[cp] \/ frame[cp] = -1
Reference == /\ frame = expectedFrame
             /\ previous = expectedPrevious
             /\ owner = expectedOwner
=============================================================================
