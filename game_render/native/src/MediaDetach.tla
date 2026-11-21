---- MODULE MediaDetach ----
EXTENDS Naturals
\* dolly_media_win.cpp media_worker_tick(session_name, connected), called only by
\* the bridge worker loop (bridge_win.cpp): every 5 ms with the mapping name and
\* the heartbeat; on editor exit with nullptr followed by video::shutdown(); and
\* in the bridge catch with nullptr and no shutdown. One action per tick: the
\* worker is serial and each branch reads video_active() once.
\*   TickConnected     connected branch: disconnected_since=0, attach, open the
\*                     mapping (success, failure, or the allocation throw that
\*                     reaches the catch path), then consume one command.
\*   TickDisconnected  !connected branch: CAS disconnected_since, then return
\*                     (grace) while video_active() and now-since < 30 s, else
\*                     clear since and stop video/audio/ReShade and close mapping.
\*   Exit/BridgeCatch  unnamed branch: unconditional cleanup, no grace.
\*   Shutdown          video::shutdown(): request_stop plus encoder join.
\* video::stop(false) only records a stop request (applied or deferred); the
\* encoder (VideoFinalize/VideoComplete, environment) finalizes later, and
\* video::start clears a stale deferred request. A reconnect or disconnect lasts
\* at least one tick (heartbeat gaps are seconds, ticks 5 ms). Time is abstracted:
\* since/outage are unset|fresh|old relative to the 30 s grace; outage is a ghost
\* of the real latest outage, result/obsVideo/obsOutage/graceStop record what a
\* tick observed. Not modeled: audio hooks, file flush, mapping contents and
\* command parsing beyond start video/audio, interprocess heartbeat timing, C++
\* weak memory, I/O failure, successful encoding, or encoder liveness.
UseFix == TRUE
ResetOnReconnect == TRUE
GraceNeedsVideo == TRUE
StopVideoOnDetach == TRUE
UnnamedCleans == TRUE
CatchStopsAudio == TRUE
ShutdownJoins == TRUE
VARIABLES pc,outage,observed,since,video,stopReq,audio,detached,mapped,
          result,obsVideo,obsOutage,graceStop
vars == <<pc,outage,observed,since,video,stopReq,audio,detached,mapped,
          result,obsVideo,obsOutage,graceStop>>
phase == IF pc \in {"stopped","abandoned"} THEN "done" ELSE "active"
API == INSTANCE MediaDetachApi WITH phase <- phase
VideoActive == video \in {"active","finalizing"}
\* Process start: session_detached=true, no mapping; video/audio start only by a
\* consumed command, so they are idle until a connected tick maps the session.
Init == /\ pc = "run" /\ outage \in {"none","short"} /\ observed = TRUE /\ since = "unset"
        /\ video = "idle" /\ stopReq = FALSE /\ audio = FALSE
        /\ detached = TRUE /\ mapped = FALSE /\ result = "none"
        /\ obsVideo = FALSE /\ obsOutage = "none" /\ graceStop = FALSE

\* Environment: heartbeat changes, time, encoder.
Disconnect == /\ pc = "run" /\ outage = "none" /\ observed
              /\ outage' = "short" /\ observed' = FALSE
              /\ UNCHANGED <<pc,since,video,stopReq,audio,detached,mapped,result,obsVideo,obsOutage,graceStop>>
Reconnect == /\ pc = "run" /\ outage # "none" /\ observed
             /\ outage' = "none" /\ observed' = FALSE
             /\ UNCHANGED <<pc,since,video,stopReq,audio,detached,mapped,result,obsVideo,obsOutage,graceStop>>
Elapse == /\ pc = "run" /\ (outage = "short" \/ since = "fresh")
          /\ outage' = (IF outage = "short" THEN "long" ELSE outage)
          /\ since' = (IF since = "fresh" THEN "old" ELSE since)
          /\ UNCHANGED <<pc,observed,video,stopReq,audio,detached,mapped,result,obsVideo,obsOutage,graceStop>>
VideoFinalize == /\ pc \in {"run","shutdown"} /\ video = "active" /\ video' = "finalizing"
                 /\ UNCHANGED <<pc,outage,observed,since,stopReq,audio,detached,mapped,result,obsVideo,obsOutage,graceStop>>
VideoComplete == /\ pc \in {"run","shutdown"} /\ video = "finalizing"
                 /\ video' = "completed" /\ stopReq' = FALSE
                 /\ UNCHANGED <<pc,outage,observed,since,audio,detached,mapped,result,obsVideo,obsOutage,graceStop>>

\* video::stop(false), sound_capture_stop, reshade_set_enabled(false), close_mapping.
CleanupTo(stopAudio) ==
    /\ stopReq' = (IF StopVideoOnDetach /\ VideoActive THEN TRUE ELSE stopReq)
    /\ audio' = (IF stopAudio THEN FALSE ELSE audio)
    /\ detached' = TRUE /\ mapped' = FALSE

TickConnected ==
    /\ pc = "run" /\ outage = "none" /\ observed' = TRUE
    /\ since' = (IF ResetOnReconnect THEN "unset" ELSE since)
    /\ obsVideo' = VideoActive /\ obsOutage' = outage /\ graceStop' = FALSE
    /\ UNCHANGED <<pc,outage>>
    /\ \/ \* Mapping open or reopened (or already open), then one command.
          /\ mapped' = TRUE /\ detached' = FALSE /\ result' = "attached"
          /\ \/ UNCHANGED <<video,stopReq,audio>>
             \/ /\ video \in {"idle","completed"} /\ video' = "active" /\ stopReq' = FALSE
                /\ UNCHANGED audio
             \/ audio' = TRUE /\ UNCHANGED <<video,stopReq>>
       \/ \* OpenFileMappingW/MapViewOfFile failed; retried 100 ms later.
          /\ ~mapped /\ mapped' = FALSE /\ detached' = FALSE /\ result' = "attached"
          /\ UNCHANGED <<video,stopReq,audio>>
       \/ \* mapping_name allocation throws: catch(...) cleanup.
          /\ ~mapped /\ result' = "fault" /\ UNCHANGED video
          /\ CleanupTo(CatchStopsAudio)

TickDisconnected ==
    LET set == IF since = "unset" THEN "fresh" ELSE since
    IN /\ pc = "run" /\ outage # "none" /\ observed' = TRUE
       /\ obsVideo' = VideoActive /\ obsOutage' = outage
       /\ UNCHANGED <<pc,outage,video>>
       /\ IF (VideoActive \/ ~GraceNeedsVideo) /\ set = "fresh"
          THEN /\ since' = set /\ result' = "grace"
               /\ audio' = audio /\ UNCHANGED <<stopReq,detached,mapped>>
               /\ graceStop' = (audio' # audio)
          ELSE /\ since' = "unset" /\ result' = "detached" /\ graceStop' = FALSE
               /\ CleanupTo(UseFix)

\* Unnamed branch: editor exit then video::shutdown, or bridge catch without it.
UnnamedTick(next) ==
    /\ pc = "run" /\ pc' = next /\ observed' = TRUE
    /\ obsVideo' = VideoActive /\ obsOutage' = outage /\ graceStop' = FALSE
    /\ UNCHANGED <<outage,video>>
    /\ IF UnnamedCleans \/ ~VideoActive
       THEN since' = "unset" /\ result' = "detached" /\ CleanupTo(TRUE)
       ELSE UNCHANGED <<since,result,stopReq,audio,detached,mapped>>
Exit == UnnamedTick("shutdown")
BridgeCatch == UnnamedTick("abandoned")
Shutdown == /\ pc = "shutdown" /\ pc' = "stopped"
            /\ video' = (IF ShutdownJoins /\ VideoActive THEN "completed" ELSE video)
            /\ stopReq' = (IF ShutdownJoins THEN FALSE ELSE VideoActive \/ stopReq)
            /\ UNCHANGED <<outage,observed,since,audio,detached,mapped,result,obsVideo,obsOutage,graceStop>>
Done == /\ pc \in {"stopped","abandoned"} /\ UNCHANGED vars
Next == \/ Disconnect \/ Reconnect \/ Elapse \/ VideoFinalize \/ VideoComplete
        \/ TickConnected \/ TickDisconnected \/ Exit \/ BridgeCatch \/ Shutdown \/ Done

TypeOK == /\ pc \in {"run","shutdown","stopped","abandoned"}
          /\ outage \in {"none","short","long"} /\ observed \in BOOLEAN
          /\ since \in {"unset","fresh","old"}
          /\ video \in {"idle","active","finalizing","completed"}
          /\ stopReq \in BOOLEAN /\ audio \in BOOLEAN /\ detached \in BOOLEAN
          /\ mapped \in BOOLEAN /\ obsVideo \in BOOLEAN /\ graceStop \in BOOLEAN
          /\ result \in {"none","attached","grace","detached","fault"}
          /\ obsOutage \in {"none","short","long"}
\* Detach always retires source audio and asks an active video to stop. The
\* catch path's sound_capture_stop is defensive: its only throw site runs with no
\* mapping, when no command can have started audio (surviving probe, not a mutant).
StoppedOnDetach == detached => ~audio
StopRequested == (detached /\ video = "active") => stopReq
\* A grace tick has no side effects.
KeepGrace == ~graceStop
\* Grace is measured from the latest observed disconnect: a heartbeat detach of
\* an active video needs a long outage. A finished video detaches at once.
NoEarlyDetach == (result = "detached" /\ pc = "run" /\ obsVideo) => obsOutage = "long"
GraceOnlyWhileVideo == result = "grace" => obsVideo
\* Exit skips grace; only video::shutdown finalizes the encoder.
ExitDetached == pc \in {"shutdown","stopped","abandoned"} => detached /\ ~audio
Finalized == pc = "stopped" => ~VideoActive
Refines == API!Spec
Spec == Init /\ [][Next]_vars
====
