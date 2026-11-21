---- MODULE MediaDetachApi ----
\* Minimal caller observation: active until returning owner completion.
\* Allows stuttering; no whole-program refinement or real-I/O liveness claim.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
