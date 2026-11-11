------------------------ MODULE ParticleProjectedScene -------------------------
EXTENDS Naturals, TLC
\* Explicit empty world-layer admission in validate_renderer_classes and final
\* recheck in build_simulated_effects. no_world_receivers concretely rejects map,
\* unknown and instanced geometry, including hidden objects. Pure classification
\* is tested separately. Existing owned provider/Blender bake rollback are callers'
\* boundaries; no Source-world absence, shader or GPU equivalence is asserted.
\* pending abstracts maps scheduled by build_map_first or legacy external_map
\* after particle baking; an empty intermediate scene cannot admit that job.
VARIABLES phase,known,world,unknown,instances,pending,initialSafe,started,published,changed
vars == <<phase,known,world,unknown,instances,pending,initialSafe,started,published,changed>>
MutSkipInitial == FALSE
MutSkipFinal == FALSE
MutSkipProfile == FALSE
MutIgnorePending == FALSE
MutIgnoreWorld == FALSE
MutIgnoreUnknown == FALSE
MutIgnoreInstances == FALSE
Init == /\ phase="preflight" /\ known \in BOOLEAN /\ world \in BOOLEAN
 /\ unknown \in BOOLEAN /\ instances \in BOOLEAN /\ pending \in BOOLEAN
 /\ initialSafe=(~world /\ ~unknown /\ ~instances /\ ~pending)
 /\ started=FALSE /\ published=FALSE /\ changed=FALSE
Safe == ~world /\ ~unknown /\ ~instances /\ ~pending
CandidateSafe == (~world \/ MutIgnoreWorld) /\ (~unknown \/ MutIgnoreUnknown)
 /\ (~instances \/ MutIgnoreInstances) /\ (~pending \/ MutIgnorePending)
Preflight == /\ phase="preflight"
 /\ started'=((known \/ MutSkipProfile) /\ (CandidateSafe \/ MutSkipInitial))
 /\ phase'=(IF ((known \/ MutSkipProfile) /\ (CandidateSafe \/ MutSkipInitial)) THEN "bake" ELSE "failed")
 /\ UNCHANGED <<known,world,unknown,instances,pending,initialSafe,published,changed>>
GeometryAppears(kind) == /\ phase="bake" /\ ~changed /\ kind \in 1..3
 /\ world'=(world \/ kind=1) /\ unknown'=(unknown \/ kind=2) /\ instances'=(instances \/ kind=3)
 /\ changed'=TRUE /\ UNCHANGED <<phase,known,pending,initialSafe,started,published>>
Finish == /\ phase="bake" /\ published'=(CandidateSafe \/ MutSkipFinal)
 /\ phase'=(IF (CandidateSafe \/ MutSkipFinal) THEN "done" ELSE "failed")
 /\ UNCHANGED <<known,world,unknown,instances,pending,initialSafe,started,changed>>
Next == Preflight \/ (\E kind \in 1..3:GeometryAppears(kind)) \/ Finish
Spec == Init /\ [][Next]_vars
QualifiedStart == started => (known /\ initialSafe)
QualifiedFinish == published => (started /\ known /\ Safe)
TypeOK == /\ phase \in {"preflight","bake","done","failed"}
 /\ \A flag \in {known,world,unknown,instances,pending,initialSafe,started,published,changed}:flag \in BOOLEAN
=============================================================================
