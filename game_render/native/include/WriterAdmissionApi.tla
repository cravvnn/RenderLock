---- MODULE WriterAdmissionApi ----
\* Local completion shape only; caller workflows are not refined here.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
