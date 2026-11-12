--------------------- MODULE SnapshotRigEvaluation ---------------------
EXTENDS Integers, TLC
\* evaluated_snapshot_rig is called by capture_source_snapshot_provider and
\* recorded_crossbow_effects.capture_points, also during saved-rig recapture.
\* Validate complete ancestry first; borrow ONLY the exporter's cosmetic rig
\* disable flag; finally restore original frame then original visibility even
\* if restoring the frame itself fails. No timer, worker or persistent cache.
\* Matrix/pose identity and complete history remain independent caller gates.
VARIABLES phase, originalHidden, previewMarked, ancestorsOK, bound,
          hidden, originalFrame, frame, borrowed, failed, restorationFailed
vars == <<phase,originalHidden,previewMarked,ancestorsOK,bound,hidden,
          originalFrame,frame,borrowed,failed,restorationFailed>>
Init == /\ phase="enter" /\ originalHidden \in BOOLEAN /\ previewMarked \in BOOLEAN
        /\ ancestorsOK \in BOOLEAN /\ bound \in BOOLEAN
        /\ hidden=originalHidden /\ originalFrame=1 /\ frame=originalFrame
        /\ borrowed=FALSE /\ failed=FALSE /\ restorationFailed=FALSE
Enter == LET valid == ancestorsOK /\ bound /\ (~originalHidden \/ previewMarked)
         IN /\ phase="enter" /\ phase'=IF valid THEN "capture" ELSE "refused"
            /\ hidden'=IF valid THEN FALSE ELSE hidden
            /\ borrowed'=valid
            /\ UNCHANGED <<originalHidden,previewMarked,ancestorsOK,bound,originalFrame,frame,failed,restorationFailed>>
Capture == /\ phase="capture" /\ phase'="restore" /\ frame'=0
           /\ failed' \in BOOLEAN
           /\ UNCHANGED <<originalHidden,previewMarked,ancestorsOK,bound,hidden,originalFrame,borrowed,restorationFailed>>
RestoreFrame == /\ phase="restore" /\ phase'="visibility" /\ frame'=originalFrame
                /\ UNCHANGED <<originalHidden,previewMarked,ancestorsOK,bound,hidden,originalFrame,borrowed,failed,restorationFailed>>
RestoreFrameFailure == /\ phase="restore" /\ phase'="visibility" /\ restorationFailed'=TRUE
                       /\ UNCHANGED <<originalHidden,previewMarked,ancestorsOK,bound,hidden,originalFrame,frame,borrowed,failed>>
RestoreVisibility == /\ phase="visibility" /\ phase'=IF restorationFailed THEN "restore_failed" ELSE "done"
                     /\ hidden'=originalHidden
                     /\ UNCHANGED <<originalHidden,previewMarked,ancestorsOK,bound,originalFrame,frame,borrowed,failed,restorationFailed>>
Terminal == /\ phase \in {"done","refused","restore_failed"} /\ UNCHANGED vars
Next == Enter \/ Capture \/ RestoreFrame \/ RestoreFrameFailure \/ RestoreVisibility \/ Terminal
Spec == Init /\ [][Next]_vars
QualifiedBorrow == ~borrowed \/ (ancestorsOK /\ bound /\ (~originalHidden \/ previewMarked))
VisibilityRestored == phase \notin {"done","refused","restore_failed"} \/ hidden=originalHidden
FrameRestored == phase \notin {"done","refused"} \/ frame=originalFrame
=======================================================================
