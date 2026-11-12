-------------------- MODULE ModelHitboxInventory --------------------
EXTENDS Integers, Naturals, TLC
\* ModelHitboxInventory.Run and particle_hitbox_assets.collect_inventory.
\* This is the same small synchronous CreateNew/serialize/flush/exit/read
\* protocol as ModelRestInventory, applied to the simulation-only inventory.
\* No cache, retry, worker or publication state is introduced. identityOK
\* abstracts bounded source/order/helper validation; geometry is numerical.
CONSTANT InitiallyExists
VARIABLES phase, exists, content, exit, accepted, foreign, identityOK
vars == <<phase,exists,content,exit,accepted,foreign,identityOK>>
Init == /\ phase="start" /\ exists=InitiallyExists
        /\ content=IF InitiallyExists THEN "prior" ELSE "none"
        /\ exit="none" /\ accepted=FALSE /\ foreign=InitiallyExists
        /\ identityOK \in BOOLEAN
Preflight == /\ phase="start"
             /\ phase'=IF exists THEN "closed" ELSE "checked"
             /\ exit'=IF exists THEN "failure" ELSE exit
             /\ UNCHANGED <<exists,content,accepted,foreign,identityOK>>
CompetingCreate == /\ phase="checked" /\ ~exists
                   /\ exists'=TRUE /\ content'="prior" /\ foreign'=TRUE
                   /\ UNCHANGED <<phase,exit,accepted,identityOK>>
BeginOutput == LET fresh == ~exists
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
