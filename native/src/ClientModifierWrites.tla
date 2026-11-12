---------------------- MODULE ClientModifierWrites ----------------------
EXTENDS Integers, Naturals, TLC
\* client_modifier_writes.rs Capture::{observe_tick,observe_write,coverage,
\* summary,write_new}; main calls observation before its actor-output crop.
\* Entire additive feature: signon scan, sticky raw coverage, bounded retention,
\* fresh-file publication and consumer admission after successful extraction.
\* Python validation is synchronous; byte/protobuf/hash correctness is tested
\* concretely, not proved here. Existing animation/projectile lifecycles remain
\* outside this additive feature. No modifier-state merging or lifetime claim.
\* Bad pre-copy guards retain no new row; malformed admitted bytes retain one
\* diagnostic row but make capture unavailable. Later callbacks freeze the prefix.
CONSTANT InitiallyExists
VARIABLES phase, position, tableSeen, failed, rows, faultSeen, preSeen, preKept,
          available, exists, foreign, content, exit, accepted, actorRows, failureRows
vars == <<phase,position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
          available,exists,foreign,content,exit,accepted,actorRows,failureRows>>
Init == /\ phase="preflight" /\ position=0 /\ tableSeen=FALSE
        /\ failed=FALSE /\ faultSeen=FALSE /\ rows=0 /\ preSeen=0 /\ preKept=0
        /\ available=FALSE /\ exists=InitiallyExists /\ foreign=InitiallyExists
        /\ content=IF InitiallyExists THEN "prior" ELSE "none"
        /\ exit="none" /\ accepted=FALSE /\ actorRows=0 /\ failureRows=0
Preflight == /\ phase="preflight"
             /\ phase'=IF exists THEN "closed" ELSE "scan"
             /\ exit'=IF exists THEN "failure" ELSE exit
             /\ UNCHANGED <<position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                            available,exists,foreign,content,accepted,actorRows,failureRows>>
Observe ==
  \E table \in BOOLEAN, event \in BOOLEAN, good \in BOOLEAN, malformed \in BOOLEAN :
    LET withinBudget == rows<1
        legal == good /\ withinBudget
        keep == event /\ ~failed /\ legal
        fault == event /\ (~legal \/ malformed)
    IN /\ phase="scan" /\ position<2 /\ (event => table) /\ (malformed => event /\ good)
       /\ position'=position+1 /\ tableSeen'=(tableSeen \/ table)
       /\ failed'=(failed \/ fault)
       /\ faultSeen'=(faultSeen \/ fault)
       /\ rows'=IF keep THEN rows+1 ELSE rows
       /\ preSeen'=preSeen+IF position=0 /\ event /\ good THEN 1 ELSE 0
       /\ preKept'=preKept+IF position=0 /\ keep THEN 1 ELSE 0
       /\ actorRows'=actorRows+1
       /\ failureRows'=IF ~failed /\ fault THEN rows+IF keep THEN 1 ELSE 0 ELSE failureRows
       /\ UNCHANGED <<phase,available,exists,foreign,content,exit,accepted>>
Finish == /\ phase="scan"
          /\ available'=(position=2 /\ tableSeen /\ ~failed)
          /\ phase'="checked"
          /\ UNCHANGED <<position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                         exists,foreign,content,exit,accepted,actorRows,failureRows>>
CompetingCreate == /\ phase \in {"scan","checked"} /\ ~exists
                   /\ exists'=TRUE /\ foreign'=TRUE /\ content'="prior"
                   /\ UNCHANGED <<phase,position,tableSeen,failed,rows,faultSeen,preSeen,
                                  preKept,available,exit,accepted,actorRows,failureRows>>
BeginOutput ==
  LET fresh == ~exists
  IN /\ phase="checked" /\ exists'=TRUE
     /\ phase'=IF fresh THEN "writing" ELSE "closed"
     /\ content'=IF fresh THEN "partial" ELSE content
     /\ exit'=IF fresh THEN exit ELSE "failure"
     /\ UNCHANGED <<position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                    available,foreign,accepted,actorRows,failureRows>>
Serialize == /\ phase="writing" /\ phase'="flush" /\ content'="complete"
             /\ UNCHANGED <<position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                            available,exists,foreign,exit,accepted,actorRows,failureRows>>
CloseSuccess == /\ phase="flush" /\ phase'="closed" /\ exit'="success"
                /\ UNCHANGED <<position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                               available,exists,foreign,content,accepted,actorRows,failureRows>>
WriteFailure == /\ phase \in {"writing","flush"} /\ phase'="closed" /\ exit'="failure"
                /\ UNCHANGED <<position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                               available,exists,foreign,content,accepted,actorRows,failureRows>>
ReadResult == /\ phase="closed"
              /\ accepted'=IF exit="success" /\ content="complete" /\ available THEN TRUE ELSE accepted
              /\ UNCHANGED <<phase,position,tableSeen,failed,rows,faultSeen,preSeen,preKept,
                             available,exists,foreign,content,exit,actorRows,failureRows>>
Next == Preflight \/ Observe \/ Finish \/ CompetingCreate \/ BeginOutput \/ Serialize \/ CloseSuccess \/ WriteFailure \/ ReadResult
Spec == Init /\ [][Next]_vars
Bounded == rows<=1
Covered == available => (position=2 /\ tableSeen /\ ~failed /\ ~faultSeen)
HistoryPreserved == available => preSeen=preKept
PreservePrior == foreign => content="prior"
AcceptedSuccess == accepted => (exit="success" /\ content="complete" /\ available)
CoreIndependent == actorRows=position
FrozenFailure == failed => rows=failureRows
=======================================================================
