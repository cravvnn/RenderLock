---- MODULE WriterAdmission ----
EXTENDS Naturals
\* Maintained production contract; private baseline comparisons are not shipped.
\* SequenceWriter.open called by arm_capture noexcept; two start attempts on an
\* existing writer. Open is the real stream/CreateFile admission; Start/Refuse
\* model std::thread construction or std::system_error, then output retirement.
\* Returning close succeeds; normal worker finish drains then closes. Constructor
\* allocation, bad_alloc elsewhere, concurrent open/finish, GPU and weak memory
\* are outside this projection. Native scratch/worker-start tests own real I/O.
\* Weak fairness assumes returning steps. Done stutters; completion API is local.
\* SequenceWriter.open from arm_capture noexcept. Existing writer, two attempts.
\* Returning file open/close and std::thread success/system_error; no constructor,
\* allocation failure elsewhere, GPU, concurrent open/finish or worker I/O modeled.
VARIABLES pc,file,worker,escaped,attempt
vars == <<pc,file,worker,escaped,attempt>>
Retire == TRUE
CatchStart == TRUE
phase == IF (pc = "done" \/ (pc = "refused" /\ (attempt = 1 \/ escaped))) THEN "done" ELSE "active"
API == INSTANCE WriterAdmissionApi WITH phase <- phase

Init == /\ pc = "open" /\ file = FALSE /\ worker = FALSE /\ escaped = FALSE /\ attempt = 0
Open == /\ pc = "open" /\ file' = TRUE /\ pc' = "start" /\ UNCHANGED <<worker,escaped,attempt>>
Start == /\ pc = "start" /\ worker' = TRUE /\ pc' = "finish" /\ UNCHANGED <<file,escaped,attempt>>
Refuse == /\ pc = "start" /\ escaped' = ~CatchStart
          /\ file' = (IF Retire THEN FALSE ELSE file) /\ pc' = "refused" /\ UNCHANGED <<worker,attempt>>
Finish == /\ pc = "finish" /\ file' = FALSE /\ worker' = FALSE /\ pc' = "done" /\ UNCHANGED <<escaped,attempt>>
Retry == /\ pc = "refused" /\ attempt = 0 /\ ~escaped /\ attempt' = 1 /\ pc' = "open" /\ UNCHANGED <<file,worker,escaped>>
Done == /\ (pc = "done" \/ (pc = "refused" /\ (attempt = 1 \/ escaped))) /\ UNCHANGED vars
Next == Open \/ Start \/ Refuse \/ Finish \/ Retry \/ Done
TypeOK == /\ pc \in {"open","start","finish","refused","done"} /\ file \in BOOLEAN
          /\ worker \in BOOLEAN /\ escaped \in BOOLEAN /\ attempt \in 0..1
NoEscape == ~escaped
Retired == pc = "refused" => ~file /\ ~worker
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
Returns == <>(phase = "done")
Refines == API!Spec

====
