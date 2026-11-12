----------------------- MODULE HitboxRuntimeInputs -----------------------
EXTENDS Integers, Naturals, TLC
\* Models the complete mutable transport component in HitboxRuntimeInputs.cs:
\* Advance plus Query's context/CP routing to HitboxFrameHistory.Query.
\* Normal Program.cs calls Advance before Start/Update; operators read Query.
\* Shape/header parsing is immutable/stateless, checked by actual code tests.
\* Payloads encode input tick/set choice; ages encode collection-local time.
\* The existing HitboxFrameHistory model separately checks binding identity.
\* No reset is inferred: reused consumers with a rewind fail. Acquisition,
\* native clock parity, renderer and whole effects are outside this model.
CONSTANTS Contexts, CPs
Keys == Contexts \X CPs
VARIABLES active, ages, current, previous, expectedActive, expectedAges, expectedCurrent, expectedPrevious
vars == <<active,ages,current,previous,expectedActive,expectedAges,expectedCurrent,expectedPrevious>>
Init == /\ active = -1
        /\ ages = [k \in Keys |-> -1]
        /\ current = [k \in Keys |-> -1]
        /\ previous = [k \in Keys |-> -1]
        /\ expectedActive = active
        /\ expectedAges = ages
        /\ expectedCurrent = current
        /\ expectedPrevious = previous
Advance(t) ==
  /\ active' = IF t = active+1 THEN t ELSE active
  /\ expectedActive' = IF t = expectedActive+1 THEN t ELSE expectedActive
  /\ UNCHANGED <<ages,current,previous,expectedAges,expectedCurrent,expectedPrevious>>
Query(context,cp,age,choice) ==
  LET key == <<context,cp>>
      refKey == <<context,cp>>
      valid == age >= ages[key]
      refValid == expectedActive >= 0 /\ age >= expectedAges[refKey]
      advance == valid /\ age # ages[key]
      refAdvance == refValid /\ age # expectedAges[refKey]
  IN /\ active >= 0
     /\ ages' = IF advance THEN [ages EXCEPT ![key] = age] ELSE ages
     /\ previous' = IF advance THEN [previous EXCEPT ![key] = current[key]] ELSE previous
     /\ current' = IF advance THEN [current EXCEPT ![key] = active*2+choice] ELSE current
     /\ expectedAges' = IF refAdvance THEN [expectedAges EXCEPT ![refKey] = age] ELSE expectedAges
     /\ expectedPrevious' = IF refAdvance THEN [expectedPrevious EXCEPT ![refKey] = expectedCurrent[refKey]] ELSE expectedPrevious
     /\ expectedCurrent' = IF refAdvance THEN [expectedCurrent EXCEPT ![refKey] = expectedActive*2+choice] ELSE expectedCurrent
     /\ UNCHANGED <<active,expectedActive>>
Next == (\E t \in 0..1: Advance(t)) \/ (\E context \in Contexts,cp \in CPs,age \in 0..1,choice \in 0..1: Query(context,cp,age,choice))
Spec == Init /\ [][Next]_vars
Reference == /\ active = expectedActive /\ ages = expectedAges
             /\ current = expectedCurrent /\ previous = expectedPrevious
==========================================================================
