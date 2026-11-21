---- MODULE VideoCompleteness ----
EXTENDS Naturals
\* encode/encode_ffmpeg via worker start; capture via Present; stop via media.
\* Bounded projection: one admitted GPU frame, returning drain, cancellation,
\* fixed-step queue timeout, late GPU loss and final publication. No game clock,
\* pixel correctness or encoder reliability. Existing MediaFinalize/MediaAdmission
\* cover transport ownership.
\*
\* Stop barrier (quiesce_producers + capture's ProducerScope): ClearReady is
\* ready.store(false); ObserveIdle is the producer_active wait followed by the
\* gpu_pending exchange and CPU drain (a frame published before it is counted:
\* drained or added to dropped). Enter is producer_active.fetch_add, CheckReady
\* is capture's ready load and Publish is produced/gpu_pending publication.
\* `buffered` is the encoder's store-buffer entry for ready=false: with
\* release/acquire ordering x86-TSO may let the encoder's producer_active load
\* complete before that store is visible (FlushReady). The seq_cst store (xchg)
\* is visible before the next load. The producer's lock-prefixed RMW is never
\* reordered on x86, so only the encoder store is buffered. This models x86-TSO
\* store->load reordering only; it is not a proof of the C++ memory model.
\* One producer cycles; the duplicate exception-path barrier is idempotent.
\*
\* Finalization: Decide is the single `cancelled` snapshot taken after the
\* barrier (after one apply_deferred_stop). Depth/Color/Sidecar/State model the
\* depth discard, color DeleteFileW, sidecar writes and state store. Cancel
\* (stop(true) or capture's apply_deferred_stop) may land at any step until done;
\* one after Decide is too late and the take completes. Process termination is
\* not modeled. CaptureFault is a capture-thread fail() (GetBuffer loss,
\* resolution/swapchain change): since 2026-11-14 capture reads the backbuffer
\* only inside the producer gate after the ready check, so it is enabled only
\* for an admitted producer and is counted before the decision. Startup
\* configuration failures happen before any output exists and are excluded.
VARIABLES pc,fixed,ffmpeg,lost,error,cancelled,pending,overflow,published,
          ready,buffered,active,ppc,late,decided,depthKept,kept,sidecar
vars == <<pc,fixed,ffmpeg,lost,error,cancelled,pending,overflow,published,
          ready,buffered,active,ppc,late,decided,depthKept,kept,sidecar>>
take == <<fixed,ffmpeg,lost,error,overflow>>
out == <<decided,depthKept,kept,sidecar,published>>
prod == <<ready,buffered,active,ppc,late>>
RejectLoss == TRUE
StopOnOverflow == TRUE
SeqCstBarrier == TRUE
ReReadCancel == FALSE
Init == /\ pc = "capture" /\ fixed \in BOOLEAN /\ ffmpeg \in BOOLEAN
        /\ lost = FALSE /\ error = FALSE /\ cancelled = FALSE
        /\ pending = TRUE /\ overflow = FALSE /\ published = FALSE
        /\ ready = TRUE /\ buffered = FALSE /\ active = 0 /\ ppc = "idle"
        /\ late = FALSE /\ decided = FALSE /\ depthKept = FALSE
        /\ kept = FALSE /\ sidecar = FALSE
Drain == /\ pc = "capture" /\ pending /\ pending' = FALSE
         /\ UNCHANGED <<pc,take,cancelled,out,prod>>
Overflow == /\ pc = "capture" /\ fixed /\ ~overflow
            /\ overflow' = TRUE /\ error' = StopOnOverflow
            /\ lost' = ~StopOnOverflow
            /\ UNCHANGED <<pc,fixed,ffmpeg,cancelled,pending,out,prod>>
Cancel == /\ pc # "done" /\ ~cancelled /\ cancelled' = TRUE
          /\ UNCHANGED <<pc,take,pending,out,prod>>
ClearReady == /\ pc = "capture" /\ pc' = "quiesce"
              /\ IF SeqCstBarrier THEN ready' = FALSE /\ UNCHANGED buffered
                                  ELSE buffered' = TRUE /\ UNCHANGED ready
              /\ UNCHANGED <<take,cancelled,pending,out,active,ppc,late>>
FlushReady == /\ buffered /\ buffered' = FALSE /\ ready' = FALSE
              /\ UNCHANGED <<pc,take,cancelled,pending,out,active,ppc,late>>
ObserveIdle == /\ pc = "quiesce" /\ active = 0 /\ pc' = "decide"
               /\ lost' = (lost \/ pending) /\ pending' = FALSE
               /\ UNCHANGED <<fixed,ffmpeg,error,overflow,cancelled,out,prod>>
Enter == /\ pc # "done" /\ ppc = "idle" /\ ppc' = "entered" /\ active' = active + 1
         /\ UNCHANGED <<pc,take,cancelled,pending,out,ready,buffered,late>>
CheckReady == /\ ppc = "entered"
              /\ IF ready THEN ppc' = "admitted" /\ UNCHANGED active
                          ELSE ppc' = "idle" /\ active' = active - 1
              /\ UNCHANGED <<pc,take,cancelled,pending,out,ready,buffered,late>>
CaptureFault == /\ ppc = "admitted" /\ ~error /\ error' = TRUE
                /\ UNCHANGED <<pc,fixed,ffmpeg,lost,overflow,cancelled,pending,out,prod>>
Publish == /\ ppc = "admitted" /\ ppc' = "idle" /\ active' = active - 1
           /\ IF pc \in {"capture","quiesce"} THEN pending' = TRUE /\ UNCHANGED late
                                              ELSE late' = TRUE /\ UNCHANGED pending
           /\ UNCHANGED <<pc,take,cancelled,out,ready,buffered>>
Ok == ~error /\ ~(RejectLoss /\ (fixed \/ ffmpeg) /\ lost)
CancelSeen == IF ReReadCancel THEN cancelled ELSE decided
Decide == /\ pc = "decide" /\ pc' = "depth" /\ decided' = cancelled
          /\ UNCHANGED <<take,cancelled,pending,depthKept,kept,sidecar,published,prod>>
DepthStep == /\ pc = "depth" /\ pc' = "color" /\ depthKept' = (Ok /\ ~CancelSeen)
             /\ UNCHANGED <<take,cancelled,pending,decided,kept,sidecar,published,prod>>
ColorStep == /\ pc = "color" /\ pc' = "sidecar" /\ kept' = (Ok /\ ~CancelSeen)
             /\ UNCHANGED <<take,cancelled,pending,decided,depthKept,sidecar,published,prod>>
SidecarStep == /\ pc = "sidecar" /\ pc' = "state" /\ sidecar' = (Ok /\ ~CancelSeen)
               /\ UNCHANGED <<take,cancelled,pending,decided,depthKept,kept,published,prod>>
StoreState == /\ pc = "state" /\ pc' = "done" /\ published' = (Ok /\ ~CancelSeen)
              /\ UNCHANGED <<take,cancelled,pending,decided,depthKept,kept,sidecar,prod>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == \/ Drain \/ Overflow \/ Cancel \/ ClearReady \/ FlushReady \/ ObserveIdle
        \/ Enter \/ CheckReady \/ Publish \/ CaptureFault
        \/ Decide \/ DepthStep \/ ColorStep \/ SidecarStep \/ StoreState \/ Done
Spec == Init /\ [][Next]_vars
TypeOK == /\ pc \in {"capture","quiesce","decide","depth","color","sidecar","state","done"}
          /\ fixed \in BOOLEAN /\ ffmpeg \in BOOLEAN /\ lost \in BOOLEAN
          /\ error \in BOOLEAN /\ cancelled \in BOOLEAN /\ pending \in BOOLEAN
          /\ overflow \in BOOLEAN /\ published \in BOOLEAN
          /\ ready \in BOOLEAN /\ buffered \in BOOLEAN /\ active \in 0..1
          /\ ppc \in {"idle","entered","admitted"} /\ late \in BOOLEAN
          /\ decided \in BOOLEAN /\ depthKept \in BOOLEAN /\ kept \in BOOLEAN
          /\ sidecar \in BOOLEAN
\* Refines the shared RecorderApi. The terminal state is published,
\* else cancelled when the single decision saw a cancel, else failed; kept is
\* the color file.
recState == IF pc # "done" THEN "active"
            ELSE IF published THEN "completed"
            ELSE IF decided THEN "cancelled" ELSE "failed"
Recorder == INSTANCE RecorderApi WITH outcome <- recState, kept <- kept
RefinesRecorder == Recorder!Spec
NoIncompleteSuccess == published /\ (fixed \/ ffmpeg) => ~lost
\* A cancel observed at the decision point never yields a completed take.
NoCancelledSuccess == published => ~decided
FixedTimeoutStops == overflow => error
\* No frame is published after the encoder's drain/gpu_pending exchange.
NoUncountedFrame == ~late
\* State, color file, depth layer and sidecars follow one decision.
OutcomeAgrees == pc = "done" => /\ kept = published /\ depthKept = published
                                /\ sidecar = published
====
