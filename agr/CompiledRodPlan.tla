-------------------------- MODULE CompiledRodPlan --------------------------
EXTENDS Naturals, TLC
\* prepare_compiled_rods copies/validates metadata into immutable byte arrays;
\* _CompiledRodPlan.project validates fresh positions/scale dimensions each pass.
\* Callers: project_compiled_rods and project_animated_rods (one-shot),
\* simulate_node_motion (call-local plan reused for 16 iterations/substep).
\* Two calls cover different hero identities; no shared/global plan survives.
\* Source edits model private-plan clients editing their original metadata after
\* preparation. Copies must keep the plan stable. No threads or persistent cache.
\* Floating arithmetic, compiled block/lane order and collision history are NOT
\* modeled: those loops are unchanged and covered by numerical equivalence tests.
CONSTANT Iterations
VARIABLES phase, call, valid, dimensions, plan, sourceChanged, iteration,
          acceptedInvalid, staleOutput
vars == <<phase,call,valid,dimensions,plan,sourceChanged,iteration,
          acceptedInvalid,staleOutput>>
MutSkipValidation == FALSE
MutSkipDimensions == FALSE
MutSharedPlan == FALSE
MutAliasSource == FALSE
Init == /\ phase="prepare" /\ call=1 /\ valid\in BOOLEAN
 /\ dimensions\in BOOLEAN /\ plan=0 /\ sourceChanged=FALSE /\ iteration=0
 /\ acceptedInvalid=FALSE /\ staleOutput=FALSE
Prepare == /\ phase="prepare" /\ (valid \/ MutSkipValidation)
 /\ plan'=(IF MutSharedPlan /\ plan#0 THEN plan ELSE call)
 /\ acceptedInvalid'=(acceptedInvalid \/ ~valid) /\ phase'="ready"
 /\ UNCHANGED <<call,valid,dimensions,sourceChanged,iteration,staleOutput>>
RefuseMetadata == /\ phase="prepare" /\ ~valid /\ ~MutSkipValidation
 /\ phase'="finished"
 /\ UNCHANGED <<call,valid,dimensions,plan,sourceChanged,iteration,acceptedInvalid,staleOutput>>
EditCallerMetadata == /\ phase="ready" /\ ~sourceChanged
 /\ sourceChanged'=TRUE /\ plan'=(IF MutAliasSource THEN 3-call ELSE plan)
 /\ UNCHANGED <<phase,call,valid,dimensions,iteration,acceptedInvalid,staleOutput>>
Project == /\ phase="ready" /\ (dimensions \/ MutSkipDimensions)
 /\ acceptedInvalid'=(acceptedInvalid \/ ~dimensions)
 /\ staleOutput'=(staleOutput \/ (plan#call))
 /\ iteration'=iteration+1
 /\ phase'=(IF iteration+1=Iterations THEN "finished" ELSE "ready")
 /\ UNCHANGED <<call,valid,dimensions,plan,sourceChanged>>
RefusePositions == /\ phase="ready" /\ ~dimensions /\ ~MutSkipDimensions
 /\ phase'="finished"
 /\ UNCHANGED <<call,valid,dimensions,plan,sourceChanged,iteration,acceptedInvalid,staleOutput>>
NextCall == /\ phase="finished" /\ call=1 /\ call'=2 /\ phase'="prepare"
 /\ valid'\in BOOLEAN /\ dimensions'\in BOOLEAN /\ sourceChanged'=FALSE
 /\ iteration'=0 /\ plan'=(IF MutSharedPlan THEN plan ELSE 0)
 /\ UNCHANGED <<acceptedInvalid,staleOutput>>
Done == /\ phase="finished" /\ call=2 /\ UNCHANGED vars
Next == Prepare \/ RefuseMetadata \/ EditCallerMetadata \/ Project \/ RefusePositions \/ NextCall \/ Done
Spec == Init /\ [][Next]_vars
ValidatedBeforeUse == ~acceptedInvalid
CallLocalSnapshot == ~staleOutput
TypeOK == /\ call\in 1..2 /\ iteration\in 0..Iterations
 /\ plan\in 0..2 /\ phase\in {"prepare","ready","finished"}
=============================================================================
