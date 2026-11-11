-------------------------- MODULE UpdateCheckCooldown --------------------------
EXTENDS Naturals,TLC
\* UpdateUI.check admits one metadata worker only when its server cooldown is
\* over; poll ignores stale request IDs before accepting a rate-limit response.
\* The worker queues UpdateRateLimited, not an install/download operation.
\* The existing 150ms UI poll re-enables a user check; no automatic retry starts.
\* Scope: one connection/session, two clock instants and current/stale replies.
\* updater.rate_limit_error is stateless header parsing, covered by unit tests.
\* Download hashes, extract/install rollback and explicit consent are unchanged,
\* covered by test_updater.py and the prior disposable upgrade qualification.
VARIABLES clock,deadline,running,requests,blockedRequest,installed
vars==<<clock,deadline,running,requests,blockedRequest,installed>>
MutIgnoreCooldown==FALSE
MutAcceptStale==FALSE
MutAutoInstall==FALSE
Init== /\ clock=0 /\ deadline=0 /\ running=FALSE /\ requests=0
 /\ blockedRequest=FALSE /\ installed=FALSE
Check== /\ ~running /\ requests<2 /\ (clock>=deadline \/ MutIgnoreCooldown)
 /\ requests'=requests+1 /\ running'=TRUE
 /\ blockedRequest'=(blockedRequest \/ clock<deadline)
 /\ UNCHANGED <<clock,deadline,installed>>
Limit== /\ running /\ running'=FALSE /\ deadline'=1
 /\ installed'=MutAutoInstall
 /\ UNCHANGED <<clock,requests,blockedRequest>>
StaleLimit== /\ ~running /\ requests=0 /\ clock=0
 /\ deadline'=(IF MutAcceptStale THEN 1 ELSE deadline)
 /\ UNCHANGED <<clock,running,requests,blockedRequest,installed>>
Tick== /\ clock=0 /\ clock'=1
 /\ UNCHANGED <<deadline,running,requests,blockedRequest,installed>>
Done== /\ clock=1 /\ requests=2 /\ ~running /\ UNCHANGED vars
Next==Check\/Limit\/StaleLimit\/Tick\/Done
Spec==Init/\[][Next]_vars
NoRequestDuringCooldown==~blockedRequest
NoStaleCooldown==requests=0=>deadline=0
MetadataNeverInstalls==~installed
=============================================================================
