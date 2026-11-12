---------------------- MODULE ModelRestInventory ----------------------
EXTENDS Integers, Naturals, TLC
\* Geometry-only helper dispatches ModelRestInventory.Run. Its caller is
\* particle_model_rest_assets.collect_model_rest: fresh path, successful exit,
\* bounded immutable parse, source/order/parent identity and unchanged helper.
\* identityOK abstracts these validation predicates, not matrix arithmetic.
\* Model decoding
\* and immutable identity/bounds checks finish before BeginOutput (stateless).
\* CreateNew/Serialize/Flush/Dispose precede successful command return. A failed
\* write may leave its fresh artifact; it is never a successful command result.
\* The collector checks exit status and parse completeness. Its returned input
\* is not normal renderer admission. No retry or existing-path reuse is inferred.
CONSTANT InitiallyExists
VARIABLES phase, exists, content, exit, accepted, foreign, identityOK
vars == <<phase,exists,content,exit,accepted,foreign,identityOK>>
Init == /\ phase="start" /\ exists=InitiallyExists
        /\ content=IF InitiallyExists THEN "prior" ELSE "none"
        /\ exit="none" /\ accepted=FALSE /\ foreign=InitiallyExists
        /\ identityOK \in BOOLEAN
Preflight ==
  /\ phase="start"
  /\ phase'=IF exists THEN "closed" ELSE "checked"
  /\ exit'=IF exists THEN "failure" ELSE exit
  /\ UNCHANGED <<exists,content,accepted,foreign,identityOK>>
CompetingCreate == /\ phase="checked" /\ ~exists
                   /\ exists'=TRUE /\ content'="prior" /\ foreign'=TRUE
                   /\ UNCHANGED <<phase,exit,accepted,identityOK>>
BeginOutput ==
  LET fresh == ~exists
  IN /\ phase="checked"
     /\ phase'=IF fresh THEN "writing" ELSE "closed"
     /\ exists'=TRUE
     /\ content'=IF fresh THEN "partial" ELSE content
     /\ exit'=IF fresh THEN exit ELSE "failure"
     /\ UNCHANGED <<accepted,foreign,identityOK>>
Serialize == /\ phase="writing" /\ phase'="flush" /\ content'="complete"
             /\ UNCHANGED <<exists,exit,accepted,foreign,identityOK>>
CloseSuccess == /\ phase="flush" /\ phase'="closed" /\ exit'="success"
                /\ UNCHANGED <<exists,content,accepted,foreign,identityOK>>
WriteFailure == /\ phase \in {"writing","flush"} /\ phase'="closed" /\ exit'="failure"
                /\ UNCHANGED <<exists,content,accepted,foreign,identityOK>>
ReadResult == /\ phase="closed"
              /\ accepted'=IF exit="success" /\ content="complete" /\ identityOK THEN TRUE ELSE accepted
              /\ UNCHANGED <<phase,exists,content,exit,foreign,identityOK>>
Next == Preflight \/ CompetingCreate \/ BeginOutput \/ Serialize \/ CloseSuccess \/ WriteFailure \/ ReadResult
Spec == Init /\ [][Next]_vars
PreservePrior == foreign => content="prior"
AcceptedSuccess == accepted => (exit="success" /\ content="complete")
QualifiedMetadata == accepted => identityOK
=====================================================================
