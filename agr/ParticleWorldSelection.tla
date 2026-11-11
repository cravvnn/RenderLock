------------------------ MODULE ParticleWorldSelection ------------------------
EXTENDS Naturals, FiniteSets, TLC
\* compile_particle_timeline: two generations of one reused control index,
\* CREATE/UPDATE_ENT/RELEASE/DESTROY and final hero/world retention. Ordered
\* protobuf decoding and typed creation predicate are deterministic interfaces
\* exercised by Python tests and the actual69bat+2blink recorded inputs.
\* A world marker is CAPTURE ONLY: rendering still refuses unqualified providers.
\* scene_extensions enables this policy; slice_particle_instances retains the
\* source owner/events. No renderer/OS/native simulation or visibility modeled.
VARIABLES phase,active,born,worldRoot,handles,sourceOwner,released,oldBirth,
 oldImmediate,kept,worldKept,exportOwner
vars == <<phase,active,born,worldRoot,handles,sourceOwner,released,oldBirth,oldImmediate,kept,worldKept,exportOwner>>
Generations == 1..2
MutDropWorld == FALSE
MutIgnoreOtherBinding == FALSE
MutInventHero == FALSE
Init == /\ phase="events" /\ active=0 /\ born=0
 /\ worldRoot=[g \in Generations |-> FALSE]
 /\ handles=[g \in Generations |-> {}]
 /\ sourceOwner=[g \in Generations |-> "none"]
 /\ released=[g \in Generations |-> FALSE]
 /\ oldBirth \in [Generations -> BOOLEAN]
 /\ oldImmediate=[g \in Generations |-> FALSE]
 /\ kept={} /\ worldKept={} /\ exportOwner=[g \in Generations |-> "none"]
Create(kind) == /\ phase="events" /\ born<2 /\ kind \in {"hero","world","otherSerial"}
 /\ born'=born+1 /\ active'=born+1
 /\ worldRoot'=[worldRoot EXCEPT ![born+1]=(kind="world")]
 /\ handles'=[handles EXCEPT ![born+1]=(IF kind="world" THEN {} ELSE {kind})]
 /\ sourceOwner'=[sourceOwner EXCEPT ![born+1]=(IF kind="hero" THEN "hero" ELSE "none")]
 /\ UNCHANGED <<phase,released,oldBirth,oldImmediate,kept,worldKept,exportOwner>>
UpdateEnt(target,primary) == /\ phase="events" /\ active>0
 /\ target \in {"hero","otherSerial"} /\ primary \in BOOLEAN
 /\ handles'=[handles EXCEPT ![active]=@ \cup {target}]
 /\ sourceOwner'=[sourceOwner EXCEPT ![active]=(IF @="none" /\ target="hero" /\ primary THEN "hero" ELSE @)]
 /\ UNCHANGED <<phase,active,born,worldRoot,released,oldBirth,oldImmediate,kept,worldKept,exportOwner>>
Release == /\ phase="events" /\ active>0 /\ ~released[active]
 /\ released'=[released EXCEPT ![active]=TRUE]
 /\ UNCHANGED <<phase,active,born,worldRoot,handles,sourceOwner,oldBirth,oldImmediate,kept,worldKept,exportOwner>>
Destroy(immediate) == /\ phase="events" /\ active>0 /\ immediate \in BOOLEAN
 /\ oldImmediate'=[oldImmediate EXCEPT ![active]=(oldBirth[active] /\ immediate)] /\ active'=0
 /\ UNCHANGED <<phase,born,worldRoot,handles,sourceOwner,released,oldBirth,kept,worldKept,exportOwner>>
World(g) == worldRoot[g] /\ sourceOwner[g]="none" /\ handles[g]={}
Candidate(g) == worldRoot[g] /\ sourceOwner[g]="none" /\ (handles[g]={} \/ MutIgnoreOtherBinding)
Eligible == {g \in 1..born: ~oldImmediate[g] /\ ("hero" \in handles[g] \/ (Candidate(g) /\ ~MutDropWorld))}
Select == /\ phase="events" /\ phase'="done" /\ kept'=Eligible
 /\ worldKept'={g \in Eligible: Candidate(g)}
 /\ exportOwner'=[g \in Generations |-> IF g \in Eligible /\ Candidate(g) /\ MutInventHero THEN "hero" ELSE sourceOwner[g]]
 /\ UNCHANGED <<active,born,worldRoot,handles,sourceOwner,released,oldBirth,oldImmediate>>
Next == (\E k \in {"hero","world","otherSerial"}: Create(k))
 \/ (\E t \in {"hero","otherSerial"}, p \in BOOLEAN: UpdateEnt(t,p))
 \/ Release \/ (\E i \in BOOLEAN: Destroy(i)) \/ Select
Spec == Init /\ [][Next]_vars
NoInventedOwner == phase="done" => \A g \in kept: exportOwner[g]=sourceOwner[g]
OnlySelectedOrWorld == phase="done" => \A g \in kept: "hero" \in handles[g] \/ World(g)
PreserveWorld == phase="done" => {g \in 1..born: World(g) /\ ~oldImmediate[g]} \subseteq kept
PreserveSelected == phase="done" => {g \in 1..born: "hero" \in handles[g] /\ ~oldImmediate[g]} \subseteq kept
=============================================================================
