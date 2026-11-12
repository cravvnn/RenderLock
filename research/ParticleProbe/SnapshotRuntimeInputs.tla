---------------------- MODULE SnapshotRuntimeInputs ----------------------
EXTENDS Integers, TLC
\* Program.Parse -> Parse/constructor validates every model/frame binding;
\* Program's single tick loop calls Advance before Start/Update. Init and rigid
\* operator inherit the exact root provider and Query by CP+bone. Complete
\* requires end tick and all declared bindings actually queried before output.
\* SnapshotSkinningRuntime covers per-particle storage/compaction, separate
\* SnapshotRowCursor covers row context; matrix math/JSON checks use code tests.
\* Bounds: ticks0..1, one declared (CP0,boneA) key, wrong key B, immutable input.
VARIABLES bindingOK, framesOK, phase, active, used, response, lastRead, skipped
vars == <<bindingOK,framesOK,phase,active,used,response,lastRead,skipped>>
Init == /\ bindingOK \in BOOLEAN /\ framesOK \in BOOLEAN
        /\ phase = "parse" /\ active = -1 /\ used = FALSE /\ response = FALSE
        /\ lastRead = "none" /\ skipped = FALSE
Parse == /\ phase = "parse"
         /\ phase' = IF bindingOK /\ framesOK THEN "ready" ELSE "rejected"
         /\ UNCHANGED <<bindingOK,framesOK,active,used,response,lastRead,skipped>>
Advance(tick) ==
  LET valid == tick = active+1 /\ tick<=1
  IN /\ phase = "ready" /\ tick \in 0..2
     /\ active' = IF valid THEN tick ELSE active
     /\ phase' = IF valid THEN "ready" ELSE "rejected"
     /\ skipped' = (skipped \/ (valid /\ tick # (active+1)))
     /\ UNCHANGED <<bindingOK,framesOK,used,response,lastRead>>
Query(key) ==
  LET valid == active>=0 /\ key="A"
  IN /\ phase = "ready" /\ key \in {"A","B"}
     /\ used' = IF valid THEN TRUE ELSE used
     /\ lastRead' = IF valid THEN key ELSE lastRead
     /\ phase' = IF valid THEN "ready" ELSE "rejected"
     /\ UNCHANGED <<bindingOK,framesOK,active,response,skipped>>
Complete == /\ phase = "ready"
            /\ response' = (active=1 /\ used)
            /\ phase' = IF active=1 /\ used THEN "published" ELSE "rejected"
            /\ UNCHANGED <<bindingOK,framesOK,active,used,lastRead,skipped>>
Next == Parse \/ (\E tick \in 0..2: Advance(tick))
        \/ (\E key \in {"A","B"}: Query(key)) \/ Complete
Spec == Init /\ [][Next]_vars
Qualified == phase \notin {"ready","published"} \/ (bindingOK /\ framesOK)
Completed == ~response \/ (active=1 /\ used /\ bindingOK /\ framesOK)
NoEarlyRead == ~used \/ active>=0
ExactBone == lastRead \in {"none","A"}
Sequential == ~skipped
============================================================================
