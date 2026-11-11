-------------------------- MODULE RodProjectionGroups --------------------------
EXTENDS Naturals, Sequences, FiniteSets, TLC
\* prepare_compiled_rods groups consecutive blocks only while their node domains
\* are disjoint; _CompiledRodPlan.project reads each group once, then scatters
\* each node's LAST native endpoint write. CompiledRodPlan covers plan lifetime.
\* Public one-shot, animated-rod and repeated node-solver callers all use this
\* projection. Two adjacent blocks, four lanes/two endpoints, bounded identities.
\* Symbolic writes depend on every input node, so reading stale shared nodes is
\* observable. This is an ordering proof, not a floating point/native parity proof.
VARIABLES phase,a,b,actual
vars==<<phase,a,b,actual>>
Nodes=={1,2,3}
Patterns=={<<1,1,1,1,1,1,1,1>>,<<2,2,2,2,2,2,2,2>>,
           <<3,3,3,3,3,3,3,3>>,<<1,2,1,2,2,1,2,1>>}
Domain(s)=={s[i]:i\in 1..Len(s)}
Last(s,n)==CHOOSE i\in 1..Len(s):s[i]=n /\ \A j\in (i+1)..Len(s):s[j]#n
First(s,n)==CHOOSE i\in 1..Len(s):s[i]=n /\ \A j\in 1..(i-1):s[j]#n
Read(p,s)==p[s[1]]+p[s[2]]+p[s[3]]+p[s[4]]+p[s[5]]+p[s[6]]+p[s[7]]+p[s[8]]
MutSharedGroup==FALSE
MutFirstPadWrite==FALSE
InitialPoints==[n\in Nodes|->n]
Block(p,s,tag)==[n\in Nodes|->IF n\in Domain(s) THEN Read(p,s)*100+tag*10+Last(s,n) ELSE p[n]]
FastBlock(p,s,tag)==[n\in Nodes|->IF n\in Domain(s) THEN Read(p,s)*100+tag*10+
    (IF MutFirstPadWrite THEN First(s,n) ELSE Last(s,n)) ELSE p[n]]
Batch(p,x,y)==[n\in Nodes|->IF n\in Domain(y) THEN FastBlock(p,y,2)[n] ELSE FastBlock(p,x,1)[n]]
Init== /\ phase="ready" /\ a\in Patterns /\ b\in Patterns /\ actual=InitialPoints
Project== /\ phase="ready" /\ phase'="done"
 /\ actual'=(IF Domain(a)\cap Domain(b)={} \/ MutSharedGroup
             THEN Batch(InitialPoints,a,b)
             ELSE FastBlock(FastBlock(InitialPoints,a,1),b,2))
 /\ UNCHANGED <<a,b>>
Done== /\ phase="done" /\ UNCHANGED vars
Next==Project\/Done
Spec==Init/\[][Next]_vars
NativeOrder==phase="done"=>actual=Block(Block(InitialPoints,a,1),b,2)
=============================================================================
