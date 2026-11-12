---------------------- MODULE SilenceBirthAdmission ----------------------
EXTENDS Naturals, Integers, TLC
\* Only the newly wired Silence path: immutable SilenceBirthInputs.Qualify,
\* ReplayEventTimeline.Parse/ValidateControls, Program's separate hitbox parse,
\* inclusive simulation loop and final cloned birth acknowledgement.
\* Ordinary/Crossbow/world paths keep their separate admission contracts.
\* Pure header predicates stand for actual compiled-case validation. Hitbox
\* cursor/consumer history composes HitboxRuntimeInputs/HitboxFrameHistory.
\* This model claims neither root CP3/4 admission nor valid full-effect output:
\* warnings/renderer/package coverage remain independent downstream guards.
\* A fatal error produces no successful helper result; no retry/reset/recovery
\* state, game reader or concurrent file publication is introduced here.
VARIABLES headerOK, transformOK, fallbackOK, modelOK, otherProvider,
          creationOK, literalOK, hitboxesOK, phase, frame, ack
vars == <<headerOK,transformOK,fallbackOK,modelOK,otherProvider,
          creationOK,literalOK,hitboxesOK,phase,frame,ack>>
inputs == <<headerOK,transformOK,fallbackOK,modelOK,otherProvider,
            creationOK,literalOK,hitboxesOK>>
Qualified == headerOK /\ transformOK /\ fallbackOK /\ modelOK /\ ~otherProvider
Init == /\ headerOK \in BOOLEAN /\ transformOK \in BOOLEAN
        /\ fallbackOK \in BOOLEAN /\ modelOK \in BOOLEAN
        /\ otherProvider \in BOOLEAN /\ creationOK \in BOOLEAN
        /\ literalOK \in BOOLEAN /\ hitboxesOK \in BOOLEAN
        /\ phase = "parse" /\ frame = -1 /\ ack = 0
Qualify == /\ phase = "parse"
           /\ phase' = IF Qualified THEN "timeline" ELSE "refused"
           /\ UNCHANGED <<inputs,frame,ack>>
ValidateTimeline == /\ phase = "timeline"
                    /\ phase' = IF creationOK /\ literalOK /\ hitboxesOK THEN "simulate" ELSE "refused"
                    /\ UNCHANGED <<inputs,frame,ack>>
Simulate == /\ phase = "simulate" /\ frame < 2 /\ frame' = frame+1
            /\ UNCHANGED <<inputs,phase,ack>>
Respond == /\ phase = "simulate" /\ frame = 2
           /\ phase' = "done" /\ ack' = 1
           /\ UNCHANGED <<inputs,frame>>
Fail == /\ phase \in {"timeline","simulate"} /\ phase' = "refused"
        /\ UNCHANGED <<inputs,frame,ack>>
Terminal == /\ phase \in {"done","refused"} /\ UNCHANGED vars
Next == Qualify \/ ValidateTimeline \/ Simulate \/ Respond \/ Fail \/ Terminal
Spec == Init /\ [][Next]_vars
ObservedHeader == phase \in {"parse","refused"} \/ headerOK
EquivalentTransform == phase \in {"parse","refused"} \/ transformOK
FallbackPreserved == phase \in {"parse","refused"} \/ fallbackOK
ModelIndependent == phase \in {"parse","refused"} \/ modelOK
NoCombined == phase \in {"parse","refused"} \/ ~otherProvider
OriginalEvents == phase \notin {"simulate","done"} \/ (creationOK /\ literalOK)
IndependentHistory == phase \notin {"simulate","done"} \/ hitboxesOK
Complete == phase # "done" \/ frame = 2
Acknowledged == (phase = "done") = (ack = 1)
==========================================================================
