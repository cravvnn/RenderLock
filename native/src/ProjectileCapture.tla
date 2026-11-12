---------------------- MODULE ProjectileCapture ----------------------
EXTENDS Integers, Naturals, TLC
\* Entire additive projectile feature: main from-signon callback, Capture
\* begin/observe/finish, summary/write_new and replay consumer admission.
\* One full entity identity, three observations (first is before shot start),
\* two-row budget. Full field values/handles and hashes use concrete tests.
\* A failed guard returns an error through main: no continued capture/retry.
\* Animation/clocks/manager-particles are unchanged external interfaces.
CONSTANT InitiallyExists
VARIABLES phase, position, recording, removed, rows, needed, preSeen, preKept,
          faultSeen, available, exists, foreign, content, exit, accepted
vars == <<phase,position,recording,removed,rows,needed,preSeen,preKept,
          faultSeen,available,exists,foreign,content,exit,accepted>>
Init == /\ phase="preflight" /\ position=0 /\ recording=FALSE /\ removed=FALSE
        /\ rows=0 /\ needed=0 /\ preSeen=0 /\ preKept=0 /\ faultSeen=FALSE
        /\ available=FALSE /\ exists=InitiallyExists /\ foreign=InitiallyExists
        /\ content=IF InitiallyExists THEN "prior" ELSE "none"
        /\ exit="none" /\ accepted=FALSE
Preflight == /\ phase="preflight"
             /\ phase'=IF exists THEN "closed" ELSE "scan"
             /\ exit'=IF exists THEN "failure" ELSE exit
             /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                            faultSeen,available,exists,foreign,content,accepted>>
Observe ==
  \E present \in BOOLEAN, selected \in BOOLEAN :
    LET expected == present /\ (selected \/ recording)
        keep == expected
        legal == ~(present /\ recording /\ removed) /\ (~keep \/ rows<2)
    IN /\ phase="scan" /\ position<3 /\ (selected => present)
       /\ phase'=IF legal THEN "scan" ELSE "closed"
       /\ position'=position+1
       /\ recording'=(recording \/ selected)
       /\ removed'=(removed \/ (recording /\ ~present))
       /\ rows'=rows+IF keep /\ legal THEN 1 ELSE 0
       /\ needed'=needed+IF expected /\ legal THEN 1 ELSE 0
       /\ preSeen'=preSeen+IF position=0 /\ expected /\ legal THEN 1 ELSE 0
       /\ preKept'=preKept+IF position=0 /\ keep /\ legal THEN 1 ELSE 0
       /\ faultSeen'=(faultSeen \/ ~legal)
       /\ exit'=IF legal THEN exit ELSE "failure"
       /\ UNCHANGED <<available,exists,foreign,content,accepted>>
Finish == /\ phase="scan"
          /\ available'=(position=3 /\ ~faultSeen)
          /\ phase'="checked"
          /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                         faultSeen,exists,foreign,content,exit,accepted>>
CompetingCreate == /\ phase \in {"scan","checked"} /\ ~exists
                   /\ exists'=TRUE /\ foreign'=TRUE /\ content'="prior"
                   /\ UNCHANGED <<phase,position,recording,removed,rows,needed,preSeen,
                                  preKept,faultSeen,available,exit,accepted>>
BeginOutput ==
  LET fresh == ~exists
  IN /\ phase="checked" /\ exists'=TRUE
     /\ phase'=IF fresh THEN "writing" ELSE "closed"
     /\ content'=IF fresh THEN "partial" ELSE content
     /\ exit'=IF fresh THEN exit ELSE "failure"
     /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                    faultSeen,available,foreign,accepted>>
Serialize == /\ phase="writing" /\ phase'="flush" /\ content'="complete"
             /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                            faultSeen,available,exists,foreign,exit,accepted>>
CloseSuccess == /\ phase="flush" /\ phase'="closed" /\ exit'="success"
                /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                               faultSeen,available,exists,foreign,content,accepted>>
WriteFailure == /\ phase \in {"writing","flush"} /\ phase'="closed" /\ exit'="failure"
                /\ UNCHANGED <<position,recording,removed,rows,needed,preSeen,preKept,
                               faultSeen,available,exists,foreign,content,accepted>>
ReadResult == /\ phase="closed"
              /\ accepted'=IF exit="success" /\ content="complete" /\ available THEN TRUE ELSE accepted
              /\ UNCHANGED <<phase,position,recording,removed,rows,needed,preSeen,preKept,
                             faultSeen,available,exists,foreign,content,exit>>
Next == Preflight \/ Observe \/ Finish \/ CompetingCreate \/ BeginOutput \/ Serialize \/ CloseSuccess \/ WriteFailure \/ ReadResult
Spec == Init /\ [][Next]_vars
Bounded == rows<=2
Covered == available => (position=3 /\ ~faultSeen)
RetainOwnerChanges == available => needed=rows
HistoryPreserved == available => preSeen=preKept
PreservePrior == foreign => content="prior"
AcceptedSuccess == accepted => (exit="success" /\ content="complete" /\ available)
=======================================================================
