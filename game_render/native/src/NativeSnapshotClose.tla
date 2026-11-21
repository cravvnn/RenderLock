---- MODULE NativeSnapshotClose ----
EXTENDS Naturals, FiniteSets
\* bridge_win worker atomic_store_release(gCommand) vs setup callback's
\* atomic_load_acquire shared_ptr. Desktop close unmaps its OWN view only;
\* native gMemory/module/hook remain resident until game-process exit.
\* Two command objects: A camera, B Release. Two engine threads P may call
\* setup_hook -> on_view for the verified main view at once; whether the engine
\* ever does is unknown, so the callback is not assumed serialized.
\* Enter: thread_local reentry guard, gHookCalls, prop_runtime::tick and the
\* caller/vtable main-view filter (non-main views return there; not modeled).
\* Admit: gMainViewBusy.compare_exchange_strong(false,true,acquire); a failed
\* CAS counts gMainViewOverlaps and returns before any function static.
\* Acquire/Use: command snapshot, statics (seen, phase, frames, lens_pinned,
\* fault, ...), gEffects, view writes and write_status. Return: shared_ptr drop
\* and the admission release store. A callback may hold A through B
\* publication; Release ack2 means finish(Stopped) with successful effects
\* restoration. No claim for engine thread behavior, C++ seqlock/data-race
\* legality, weak-memory semantics, status try-lock loss, effects failure or GPU.
P == {1, 2}
UseGuard == TRUE
VARIABLES pc,busy,head,local,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite
vars == <<pc,busy,head,local,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite>>
Holds(q) == local[q]=1
Init == /\ pc=[p \in P |-> "idle"] /\ busy=0 /\ head=1 /\ local=[p \in P |-> 0]
        /\ published=FALSE /\ desktopMapped=TRUE /\ nativeMapped=TRUE /\ oldAlive=TRUE
        /\ ack=0 /\ badWrite=FALSE
Enter(p) == /\ pc[p]="idle" /\ pc'=[pc EXCEPT ![p]="gate"]
            /\ UNCHANGED <<busy,head,local,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite>>
Admit(p) == /\ pc[p]="gate"
            /\ IF busy=0 \/ ~UseGuard
                  THEN /\ pc'=[pc EXCEPT ![p]="acquire"] /\ busy'=p
                  ELSE /\ pc'=[pc EXCEPT ![p]="idle"] /\ UNCHANGED busy
            /\ UNCHANGED <<head,local,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite>>
Acquire(p) == /\ pc[p]="acquire" /\ local'=[local EXCEPT ![p]=head]
              /\ pc'=[pc EXCEPT ![p]="use"]
              /\ UNCHANGED <<busy,head,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite>>
PublishRelease == /\ ~published /\ head'=2 /\ published'=TRUE
                  /\ oldAlive' = (\E q \in P : Holds(q)) /\ UNCHANGED <<pc,busy,local,desktopMapped,nativeMapped,ack,badWrite>>
DesktopClose == /\ published /\ desktopMapped /\ desktopMapped'=FALSE
                /\ nativeMapped'=TRUE /\ UNCHANGED <<pc,busy,head,local,published,oldAlive,ack,badWrite>>
Use(p) == /\ pc[p]="use" /\ pc'=[pc EXCEPT ![p]="return"]
          /\ ack'= IF local[p]=2 THEN 2 ELSE ack
          /\ badWrite'=(badWrite \/ (local[p]=1 /\ ack=2))
          /\ UNCHANGED <<busy,head,local,published,desktopMapped,nativeMapped,oldAlive>>
Return(p) == /\ pc[p]="return" /\ pc'=[pc EXCEPT ![p]="idle"] /\ local'=[local EXCEPT ![p]=0]
             /\ busy'=IF busy=p THEN 0 ELSE busy
             /\ oldAlive' = (~published \/ \E q \in P \ {p} : Holds(q))
             /\ UNCHANGED <<head,published,desktopMapped,nativeMapped,ack,badWrite>>
Callback(p) == Enter(p) \/ Admit(p) \/ Acquire(p) \/ Use(p) \/ Return(p)
Run == PublishRelease \/ DesktopClose \/ \E p \in P : Callback(p)
Spec == Init /\ [][Run]_vars /\ WF_vars(PublishRelease) /\ \A p \in P : WF_vars(Callback(p))
TypeOK == /\ pc \in [P -> {"idle","gate","acquire","use","return"}] /\ busy \in {0} \cup P
          /\ head \in {1,2} /\ local \in [P -> {0,1,2}]
          /\ published \in BOOLEAN /\ desktopMapped \in BOOLEAN /\ nativeMapped \in BOOLEAN
          /\ oldAlive \in BOOLEAN /\ ack \in {0,2} /\ badWrite \in BOOLEAN
SnapshotAlive == \A p \in P : pc[p] \in {"use","return"} /\ local[p]=1 => oldAlive
NativeViewAlive == nativeMapped
NoWriteAfterSuccessfulRelease == ~badWrite
\* Function statics are touched only between admission and the release store.
ExclusiveStatics == Cardinality({p \in P : pc[p] \in {"acquire","use","return"}}) <= 1
\* Required success: some main view is admitted and acknowledges Release.
ReleaseAcknowledged == <>(ack=2)
====
