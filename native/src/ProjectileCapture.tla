---------------------- MODULE ProjectileCapture ----------------------
EXTENDS Integers, Naturals, TLC
\* Entire additive projectile feature: main from-signon callback, Capture
\* begin/observe/finish, summary/write_new and replay consumer admission.
\* One full entity identity, four observations (first is before shot start),
\* two-row budget. Full field values/handles and hashes use concrete tests.
\* A failed capture discards all partial tracks and disables further capture.
\* Actor callbacks continue; publication errors still fail main, no retry.
\* Animation/clocks/manager-particles are unchanged external interfaces.
CONSTANT InitiallyExists
VARIABLES phase, position, recording, removed, rows, needed, preSeen, preKept,
          faultSeen, available, exists, foreign, content, exit, accepted, actorRows
vars == <<phase,position,recording,removed,rows,needed,preSeen,preKept,
          faultSeen,available,exists,foreign,content,exit,accepted,actorRows>>
Init == /\ phase="preflight" /\ position=0 /\ recording=FALSE /\ removed=FALSE
        /\ rows=0 /\ needed=0 /\ preSeen=0 /\ preKept=0 /\ faultSeen=FALSE
        /\ available=FALSE /\ exists=InitiallyExists /\ foreign=InitiallyExists
        /\ content=IF InitiallyExists THEN "prior" ELSE "none"
        /\ exit="none" /\ accepted=FALSE /\ actorRows=0
Preflight == /\ phase="preflight"
             /\ phase'=IF exists THEN "closed" ELSE "scan"
             /\ exit'=IF exists THEN "failure" ELSE exit
             /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                            faultSeen,available,exists,foreign,content,accepted,actorRows>>
Observe ==
  \E present \in BOOLEAN, selected \in BOOLEAN :
    LET expected == present /\ (selected \/ recording)
        keep == expected /\ ~faultSeen
        legal == faultSeen \/ (~(present /\ recording /\ removed) /\ (~keep \/ rows<2))
    IN /\ phase="scan" /\ position<4 /\ (selected => present)
       /\ phase'="scan"
       /\ position'=position+1
       /\ recording'=IF faultSeen \/ ~legal THEN FALSE ELSE recording \/ selected
       /\ removed'=IF faultSeen \/ ~legal THEN FALSE ELSE removed \/ (recording /\ ~present)
       /\ rows'=IF legal THEN rows+IF keep THEN 1 ELSE 0 ELSE 0
       /\ needed'=IF faultSeen \/ ~legal THEN 0 ELSE needed+IF expected THEN 1 ELSE 0
       /\ preSeen'=IF faultSeen \/ ~legal THEN 0 ELSE preSeen+IF position=0 /\ expected THEN 1 ELSE 0
       /\ preKept'=IF faultSeen \/ ~legal THEN 0 ELSE preKept+IF position=0 /\ keep THEN 1 ELSE 0
       /\ faultSeen'=(faultSeen \/ ~legal)
       /\ actorRows'=actorRows+1
       /\ UNCHANGED exit
       /\ UNCHANGED <<available,exists,foreign,content,accepted>>
Finish == /\ phase="scan"
          /\ available'=(position=4 /\ ~faultSeen)
          /\ phase'="checked"
          /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                         faultSeen,exists,foreign,content,exit,accepted,actorRows>>
CompetingCreate == /\ phase \in {"scan","checked"} /\ ~exists
                   /\ exists'=TRUE /\ foreign'=TRUE /\ content'="prior"
                   /\ UNCHANGED <<phase,position,recording,removed,rows,needed,preSeen,
                                  preKept,faultSeen,available,exit,accepted,actorRows>>
BeginOutput ==
  \E fits \in BOOLEAN :
    LET fresh == ~exists
    IN /\ phase="checked" /\ (faultSeen => fits) /\ exists'=TRUE
       /\ phase'=IF fresh THEN "writing" ELSE "closed"
       /\ content'=IF fresh THEN "partial" ELSE content
       /\ exit'=IF fresh THEN exit ELSE "failure"
       /\ faultSeen'=(faultSeen \/ ~fits)
       /\ available'=(available /\ fits)
       /\ rows'=IF fits THEN rows ELSE 0
       /\ recording'=IF fits THEN recording ELSE FALSE
       /\ removed'=IF fits THEN removed ELSE FALSE
       /\ needed'=IF fits THEN needed ELSE 0
       /\ preSeen'=IF fits THEN preSeen ELSE 0
       /\ preKept'=IF fits THEN preKept ELSE 0
       /\ UNCHANGED <<position,foreign,accepted,actorRows>>
Serialize == /\ phase="writing" /\ phase'="flush" /\ content'="complete"
             /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                            faultSeen,available,exists,foreign,exit,accepted,actorRows>>
CloseSuccess == /\ phase="flush" /\ phase'="closed" /\ exit'="success"
                /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                               faultSeen,available,exists,foreign,content,accepted,actorRows>>
WriteFailure == /\ phase \in {"writing","flush"} /\ phase'="closed" /\ exit'="failure"
                /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                               faultSeen,available,exists,foreign,content,accepted,actorRows>>
ReadResult == /\ phase="closed"
              /\ accepted'=IF exit="success" /\ content="complete" /\ available THEN TRUE ELSE accepted
              /\ UNCHANGED <<phase,position,recording,removed,rows,needed,preSeen,preKept,
                             faultSeen,available,exists,foreign,content,exit,actorRows>>
Next == Preflight \/ Observe \/ Finish \/ CompetingCreate \/ BeginOutput \/ Serialize \/ CloseSuccess \/ WriteFailure \/ ReadResult
Spec == Init /\ [][Next]_vars
Bounded == rows<=2
Covered == available => (position=4 /\ ~faultSeen)
RetainOwnerChanges == available => needed=rows
HistoryPreserved == available => preSeen=preKept
PreservePrior == foreign => content="prior"
AcceptedSuccess == accepted => (exit="success" /\ content="complete" /\ available)
DiscardFailed == faultSeen => rows=0
CoreIndependent == actorRows=position
=======================================================================
