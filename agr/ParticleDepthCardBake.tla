------------------------- MODULE ParticleDepthCardBake -------------------------
EXTENDS Naturals,TLC
\* prepare_depth_cards validates all selected camera/quad projections before
\* installing live geometry/UV nodes. finally restores frame; build_package_effects owns removal
\* of all newly allocated package data on error. Per-evaluation geometry/UV math is stateless
\* and tested separately. This model does not prove Blender or Source internals.
VARIABLES phase,valid,writeFails,captured,dirty,restored,failed,published
vars==<<phase,valid,writeFails,captured,dirty,restored,failed,published>>
MutEarlyWrite==FALSE
MutSkipRestore==FALSE
MutSkipRollback==FALSE
Init== /\ phase="capture" /\ valid\in BOOLEAN /\ writeFails\in BOOLEAN
 /\ captured=FALSE /\ dirty=FALSE /\ restored=TRUE /\ failed=FALSE /\ published=FALSE
Capture== /\ phase="capture" /\ phase'="captured" /\ captured'=valid
 /\ restored'=FALSE /\ failed'=~valid /\ UNCHANGED<<valid,writeFails,dirty,published>>
Write== /\ phase="captured" /\ (captured\/MutEarlyWrite) /\ phase'="write"
 /\ dirty'=TRUE /\ failed'=(failed\/writeFails)
 /\ UNCHANGED<<valid,writeFails,captured,restored,published>>
Refuse== /\ phase="captured" /\ ~captured /\ ~MutEarlyWrite /\ phase'="finally"
 /\ UNCHANGED<<valid,writeFails,captured,dirty,restored,failed,published>>
Finish== /\ phase="write" /\ phase'="finally" /\ published'=~failed
 /\ UNCHANGED<<valid,writeFails,captured,dirty,restored,failed>>
Finally== /\ phase="finally" /\ phase'="cleanup" /\ restored'=~MutSkipRestore
 /\ UNCHANGED<<valid,writeFails,captured,dirty,failed,published>>
PackageCleanup== /\ phase="cleanup" /\ phase'="done"
 /\ dirty'=(dirty/\(~failed\/MutSkipRollback))
 /\ UNCHANGED<<valid,writeFails,captured,restored,failed,published>>
Next==Capture\/Write\/Refuse\/Finish\/Finally\/PackageCleanup
Spec==Init/\[][Next]_vars
CapturedBeforeWrite==dirty=>captured
RestoredOnReturn==phase="done"=>restored
FailureAtomic==phase="done"/\failed=>~dirty
TypeOK==phase\in{"capture","captured","write","finally","cleanup","done"}
=============================================================================
