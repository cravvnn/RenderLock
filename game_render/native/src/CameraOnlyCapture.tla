---- MODULE CameraOnlyCapture ----
EXTENDS Naturals
\* Camera-only branch of video::capture / finish_camera_only.
\* Existing caller: overlay clean_frame, serialized Present/Resize boundary.
\* One admitted callback can overlap worker stop/cancel. Closing ready precedes
\* waiting for that callback; no file publication on the render thread.
\* Two frames represent a bounded take. Valid abstracts actual native clock,
\* pose, fresh-view, dimensions and contiguous-cadence checks, tested in C++.
\* This is raw camera-trace publication, NOT final .crav success. Replay SHA
\* pin/recheck and final no-overwrite publication remain Python obligations.
\* Cancel may arrive until done. finish_camera_only takes one cancel decision
\* (decide_cancel) after quiesce_producers; publication and the terminal state
\* both follow it, so a cancel after the decision completes consistently
\* (OutcomeAgrees). Python's own cancel check still refuses the .crav.
\* The ready/producer_active store/load handshake is seq_cst on both sides; its
\* store-buffer race is modeled in VideoCompleteness.tla, so Enter stays atomic.
\* Excludes GPU behavior, Windows filesystem semantics and whole-program proof.
VARIABLES ready, stopping, cancelled, failed, callback, count, pc, published, decision, status
vars == <<ready, stopping, cancelled, failed, callback, count, pc, published, decision, status>>
frozen == <<decision, status>>
Init == /\ ready = TRUE /\ stopping = FALSE /\ cancelled = FALSE
        /\ failed = FALSE /\ callback = "none" /\ count = 0
        /\ pc = "running" /\ published = FALSE
        /\ decision = "none" /\ status = "active"
Enter == /\ ready /\ ~stopping /\ callback = "none" /\ count < 2
         /\ callback' \in {"valid", "invalid"}
         /\ UNCHANGED <<ready, stopping, cancelled, failed, count, pc, published>> /\ UNCHANGED frozen
FinishFrame == /\ callback # "none"
               /\ count' = IF callback = "valid" THEN count + 1 ELSE count
               /\ failed' = (failed \/ callback = "invalid")
               /\ stopping' = (stopping \/ callback = "invalid" \/ count' = 2)
               /\ callback' = "none"
               /\ UNCHANGED <<ready, cancelled, pc, published>> /\ UNCHANGED frozen
Stop == /\ pc = "running" /\ ~stopping /\ stopping' = TRUE
        /\ UNCHANGED <<ready, cancelled, failed, callback, count, pc, published>> /\ UNCHANGED frozen
Cancel == /\ pc # "done" /\ ~cancelled
          /\ cancelled' = TRUE /\ stopping' = TRUE
          /\ UNCHANGED <<ready, failed, callback, count, pc, published>> /\ UNCHANGED frozen
\* Worker-side fault while running (the 10 s no-progress timeout). Capture-
\* thread faults (GetBuffer loss, dimensions, clock, pose) happen inside the
\* producer gate since 2026-11-14 and are the "invalid" callback above.
Fault == /\ pc = "running" /\ ~failed
         /\ failed' = TRUE /\ stopping' = TRUE
         /\ UNCHANGED <<ready, cancelled, callback, count, pc, published>> /\ UNCHANGED frozen
Close == /\ pc = "running" /\ stopping
         /\ ready' = FALSE /\ pc' = "draining"
         /\ UNCHANGED <<stopping, cancelled, failed, callback, count, published>> /\ UNCHANGED frozen
Drain == /\ pc = "draining" /\ callback = "none" /\ pc' = "publishing"
         /\ decision' = IF cancelled THEN "cancel" ELSE "keep"
         /\ UNCHANGED <<ready, stopping, cancelled, failed, callback, count, published, status>>
Publish == /\ pc = "publishing"
           /\ published' = (decision = "keep" /\ ~failed /\ count = 2)
           /\ failed' = (failed \/ (decision = "keep" /\ count # 2))
           /\ pc' = "storing"
           /\ UNCHANGED <<ready, stopping, cancelled, callback, count>> /\ UNCHANGED frozen
StoreState == /\ pc = "storing"
              /\ status' = IF failed THEN "failed"
                            ELSE IF decision = "cancel" THEN "cancelled" ELSE "completed"
              /\ pc' = "done"
              /\ UNCHANGED <<ready, stopping, cancelled, failed, callback, count, published, decision>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Enter \/ FinishFrame \/ Stop \/ Cancel \/ Fault \/ Close \/ Drain \/ Publish \/ StoreState \/ Done
Spec == Init /\ [][Next]_vars
Bounded == count <= 2
QuiescentPublication == published => (~ready /\ callback = "none" /\ stopping)
CompletePublication == published => (count = 2 /\ decision = "keep" /\ ~failed)
OutcomeAgrees == pc = "done" => (published <=> status = "completed")
\* Refines the shared RecorderApi.
Recorder == INSTANCE RecorderApi WITH outcome <- status, kept <- published
RefinesRecorder == Recorder!Spec
QuiescentReader == (pc \in {"publishing", "done"}) => (~ready /\ callback = "none")
====
