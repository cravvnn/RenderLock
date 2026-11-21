---- MODULE CaptureFailure ----
EXTENDS Naturals
\* dolly_player_capture.cpp: draw/present_boundary -> gpu_poll/retire_failed_images -> tick's
\* sequence_readbacks_drained -> CaptureDrainOwnership.try_close -> writer flush.
\* One failed prepared ring, one callback, one image and one proof readback.
\* Other rings repeat the same barrier. Returning callbacks/GPU only; watchdog,
\* driver hangs, identities and pixels excluded (DrawProof covers proof reuse).
VARIABLES phase, image, proof, prepared, callback, closed, flushed, terminal, published
vars == <<phase, image, proof, prepared, callback, closed, flushed, terminal, published>>
RetireFailures == TRUE
RequireImage == TRUE
RequireProof == TRUE
RequireCallback == TRUE
RequireFlush == TRUE
PublishTerminal == TRUE
Init == /\ phase = "poll" /\ image \in BOOLEAN /\ proof \in BOOLEAN
        /\ prepared = TRUE /\ callback = TRUE /\ closed = FALSE
        /\ flushed = FALSE /\ terminal = FALSE /\ published = FALSE
Poll == /\ phase = "poll"
        /\ prepared' = IF RetireFailures /\ (~RequireImage \/ ~image) /\ (~RequireProof \/ ~proof)
                         THEN FALSE ELSE prepared
        /\ phase' = "checked"
        /\ UNCHANGED <<image, proof, callback, closed, flushed, terminal, published>>
Close == /\ phase = "checked"
         /\ closed' = (~prepared /\ (~RequireCallback \/ ~callback))
         /\ phase' = "return"
         /\ UNCHANGED <<image, proof, prepared, callback, flushed, terminal, published>>
Return == /\ phase = "return" /\ phase' = "poll"
          /\ image' = FALSE /\ proof' = FALSE /\ callback' = FALSE
          /\ UNCHANGED <<prepared, closed, flushed, terminal, published>>
Finish == /\ closed /\ phase = "return" /\ phase' = "finish"
          /\ flushed' \in BOOLEAN
          /\ UNCHANGED <<image, proof, prepared, callback, closed, terminal, published>>
Report == /\ phase = "finish" /\ phase' = "done"
          /\ terminal' = (~RequireFlush \/ flushed)
          /\ published' = PublishTerminal
          /\ UNCHANGED <<image, proof, prepared, callback, closed, flushed>>
Done == /\ phase = "done" /\ UNCHANGED vars
Next == Poll \/ Close \/ Return \/ Finish \/ Report \/ Done
Spec == Init /\ [][Next]_vars
NoEarlyRetirement == ~prepared => (~image /\ ~proof)
PromptRetirement == (phase = "checked" /\ ~image /\ ~proof) => ~prepared
NoEarlyClose == closed => (~prepared /\ ~callback /\ ~image /\ ~proof)
FlushBeforeReport == terminal => (closed /\ flushed)
TerminalVisible == terminal => published
====
