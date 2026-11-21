---- MODULE DrawProof ----
EXTENDS Naturals, FiniteSets
\* available_draw_slot / release_draw_slot join gpu_poll and redirect_indexed.
\* Two slots and four draws in one image; each completion can succeed or fail.
\* GPU verdict publication and pending-clear are separate steps as in gpu_poll.
\* Submit retains events; Retire releases them only after both users finish.
\* Production immediate-context thread serializes these calls; this model allows
\* either completion order, but not concurrent accesses to the same D3D context.
\* Identity checks, pixels, storage and outer capture drain are outside this
\* projection. Native actual-owner/WARP test and capture lifecycle test cover
\* their respective boundaries. This is not whole-program refinement.
VARIABLES busy, pending, gpu, redirect, submitted, retired, unsafe
vars == <<busy,pending,gpu,redirect,submitted,retired,unsafe>>
Slots == 1..2
RequirePending == TRUE
RequireGpu == TRUE
RequireRedirect == TRUE
Init == /\ busy = [s \in Slots |-> FALSE]
        /\ pending = [s \in Slots |-> FALSE]
        /\ gpu = [s \in Slots |-> "waiting"]
        /\ redirect = [s \in Slots |-> "waiting"]
        /\ submitted = 0 /\ retired = 0 /\ unsafe = FALSE
Submit(s) == /\ ~busy[s] /\ submitted < 4
             /\ busy' = [busy EXCEPT ![s] = TRUE]
             /\ pending' = [pending EXCEPT ![s] = TRUE]
             /\ gpu' = [gpu EXCEPT ![s] = "waiting"]
             /\ redirect' = [redirect EXCEPT ![s] = "waiting"]
             /\ submitted' = submitted + 1 /\ UNCHANGED <<retired,unsafe>>
Gpu(s,v) == /\ busy[s] /\ gpu[s] = "waiting"
            /\ gpu' = [gpu EXCEPT ![s] = v]
            /\ UNCHANGED <<busy,pending,redirect,submitted,retired,unsafe>>
PollReturn(s) == /\ busy[s] /\ pending[s] /\ gpu[s] # "waiting"
                 /\ pending' = [pending EXCEPT ![s] = FALSE]
                 /\ UNCHANGED <<busy,gpu,redirect,submitted,retired,unsafe>>
Redirect(s,v) == /\ busy[s] /\ redirect[s] = "waiting"
                 /\ redirect' = [redirect EXCEPT ![s] = v]
                 /\ UNCHANGED <<busy,pending,gpu,submitted,retired,unsafe>>
Safe(s) == ~pending[s] /\ gpu[s] = "pass" /\ redirect[s] = "pass"
Retire(s) == /\ busy[s]
             /\ (~RequirePending \/ ~pending[s])
             /\ (~RequireGpu \/ gpu[s] = "pass")
             /\ (~RequireRedirect \/ redirect[s] = "pass")
             /\ busy' = [busy EXCEPT ![s] = FALSE]
             /\ retired' = retired + 1
             /\ unsafe' = (unsafe \/ ~Safe(s))
             /\ UNCHANGED <<pending,gpu,redirect,submitted>>
Step == \E s \in Slots : Submit(s) \/ PollReturn(s) \/ Retire(s) \/
                         (\E v \in {"pass","fail"} : Gpu(s,v) \/ Redirect(s,v))
\* Failed proof stays pinned for outer failed-take cleanup; terminal stutter is
\* intentional. No successful export or liveness claim for a failing renderer.
Done == /\ ~ENABLED Step /\ UNCHANGED vars
Next == Step \/ Done
TypeOK == /\ busy \in [Slots -> BOOLEAN] /\ pending \in [Slots -> BOOLEAN]
          /\ gpu \in [Slots -> {"waiting","pass","fail"}]
          /\ redirect \in [Slots -> {"waiting","pass","fail"}]
          /\ submitted \in 0..4 /\ retired \in 0..4 /\ unsafe \in BOOLEAN
NoEarlyReuse == ~unsafe
BoundedOwnership == retired + Cardinality({s \in Slots : busy[s]}) = submitted
Spec == Init /\ [][Next]_vars
====

