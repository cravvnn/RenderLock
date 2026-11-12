---------------------- MODULE ClientEffectOwnerStates ----------------------
EXTENDS Integers, Naturals, TLC
\* client_effect_owner_states::{observe_tick,push,coverage,write_new}, main's
\* selected-owner loop, and shared client_modifier_writes::write_rows_new.
\* Whole additive capture: sticky identity/order/duplicate/budget failure,
\* raw missing fields retained, complete-scan availability, exclusive output,
\* flush failure and successful-process consumer admission. Python's pure
\* bounded schema/array/digest validation is concretely tested. No effect gate,
\* particle birth/lifetime or other exporter lifecycle is inferred here.
CONSTANT InitiallyExists
VARIABLES phase,pos,failed,faultSeen,rows,words,bytes,missing,
          available,exists,foreign,content,exit,accepted,actorRows,failurePrefix
vars == <<phase,pos,failed,faultSeen,rows,words,bytes,missing,
          available,exists,foreign,content,exit,accepted,actorRows,failurePrefix>>
Init == /\ phase="preflight" /\ pos=0 /\ failed=FALSE /\ faultSeen=FALSE
        /\ rows=0 /\ words=0 /\ bytes=0 /\ missing=0 /\ available=FALSE
        /\ exists=InitiallyExists /\ foreign=InitiallyExists
        /\ content=IF InitiallyExists THEN "prior" ELSE "none"
        /\ exit="none" /\ accepted=FALSE /\ actorRows=0 /\ failurePrefix = <<0,0,0>>
Preflight == /\ phase="preflight"
             /\ phase'=IF exists THEN "closed" ELSE "scan"
             /\ exit'=IF exists THEN "failure" ELSE exit
             /\ UNCHANGED <<pos,failed,faultSeen,rows,words,bytes,missing,
                            available,exists,foreign,content,accepted,actorRows,failurePrefix>>
Observe ==
  \E kind \in {"empty","good","missing","identity","duplicate","order"}, w \in {1,2}, b \in {1,2} :
    LET identityOK == kind#"identity"
        unique == kind#"duplicate"
        ordered == kind#"order"
        rowBudget == rows<2
        wordBudget == words+w<=3
        jsonBudget == bytes+b<=3
        legal == identityOK /\ unique /\ ordered /\ rowBudget /\ wordBudget /\ jsonBudget
        protocolFault == kind \in {"identity","duplicate","order"} \/ rows>=2 \/ words+w>3 \/ bytes+b>3
        event == kind#"empty"
        keep == event /\ ~failed /\ legal
    IN /\ phase="scan" /\ pos<3 /\ pos'=pos+1
       /\ failed'=(failed \/ (event /\ ~legal))
       /\ faultSeen'=(faultSeen \/ (event /\ protocolFault))
       /\ rows'=IF keep THEN rows+1 ELSE rows
       /\ words'=IF keep THEN words+w ELSE words
       /\ bytes'=IF keep THEN bytes+b ELSE bytes
       /\ missing'=IF keep /\ kind="missing" THEN missing+1 ELSE missing
       /\ actorRows'=actorRows+1
       /\ failurePrefix'=IF ~failed /\ event /\ ~legal THEN <<rows,words,bytes>> ELSE failurePrefix
       /\ UNCHANGED <<phase,available,exists,foreign,content,exit,accepted>>
Finish == /\ phase="scan" /\ phase'="checked"
          /\ available'=(pos=3 /\ ~failed)
          /\ UNCHANGED <<pos,failed,faultSeen,rows,words,bytes,missing,
                         exists,foreign,content,exit,accepted,actorRows,failurePrefix>>
CompetingCreate == /\ phase \in {"scan","checked"} /\ ~exists
                   /\ exists'=TRUE /\ foreign'=TRUE /\ content'="prior"
                   /\ UNCHANGED <<phase,pos,failed,faultSeen,rows,words,bytes,missing,
                                  available,exit,accepted,actorRows,failurePrefix>>
BeginOutput ==
  LET fresh == ~exists
  IN /\ phase="checked" /\ exists'=TRUE
     /\ phase'=IF fresh THEN "writing" ELSE "closed"
     /\ content'=IF fresh THEN "partial" ELSE content
     /\ exit'=IF fresh THEN exit ELSE "failure"
     /\ UNCHANGED <<pos,failed,faultSeen,rows,words,bytes,missing,available,foreign,accepted,actorRows,failurePrefix>>
Serialize == /\ phase="writing" /\ phase'="flush" /\ content'="complete"
             /\ UNCHANGED <<pos,failed,faultSeen,rows,words,bytes,missing,available,exists,foreign,exit,accepted,actorRows,failurePrefix>>
CloseSuccess == /\ phase="flush" /\ phase'="closed" /\ exit'="success"
                /\ UNCHANGED <<pos,failed,faultSeen,rows,words,bytes,missing,available,exists,foreign,content,accepted,actorRows,failurePrefix>>
WriteFailure == /\ phase \in {"writing","flush"} /\ phase'="closed" /\ exit'="failure"
                /\ UNCHANGED <<pos,failed,faultSeen,rows,words,bytes,missing,available,exists,foreign,content,accepted,actorRows,failurePrefix>>
ReadResult == /\ phase="closed"
              /\ accepted'=IF exit="success" /\ content="complete" /\ available THEN TRUE ELSE accepted
              /\ UNCHANGED <<phase,pos,failed,faultSeen,rows,words,bytes,missing,available,exists,foreign,content,exit,actorRows,failurePrefix>>
Next == Preflight \/ Observe \/ Finish \/ CompetingCreate \/ BeginOutput \/ Serialize \/ CloseSuccess \/ WriteFailure \/ ReadResult
Spec == Init /\ [][Next]_vars
RowBounded == rows<=2
WordBounded == words<=3
JsonBounded == bytes<=3
Covered == available => (pos=3 /\ ~failed /\ ~faultSeen)
MissingIsExplicit == missing<=rows
PreservePrior == foreign => content="prior"
AcceptedSuccess == accepted => (exit="success" /\ content="complete" /\ available)
CoreIndependent == actorRows=pos
FrozenFailure == failed => <<rows,words,bytes>> = failurePrefix
=======================================================================
