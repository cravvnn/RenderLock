---------------------- MODULE ModifierMaskPlan ----------------------
EXTENDS Naturals, TLC
\* modifier_mask_plan.compile_mask_plan is a synchronous, immutable projection.
\* Imports serial folding via ModifierSerialState; raw capture/provenance bounds
\* via native/ClientModifierWrites and ClientEffectOwnerStates. No game or IO.
\* All guards precede publication: same extraction, sustained pre-shot modifier,
\* complete owner identity/ticks, explicit known owner gate. Draw is gate request,
\* NOT actual client lifetime/endcap, model transforms, shaders or render support.
VARIABLES phase, qualified, fault, sampled, good, requested, actualGate, extractionOK, historyOK
vars == <<phase,qualified,fault,sampled,good,requested,actualGate,extractionOK,historyOK>>
Init == /\ phase="validate" /\ qualified=FALSE /\ fault=FALSE
        /\ sampled=FALSE /\ good=FALSE /\ requested=FALSE /\ actualGate=FALSE
        /\ extractionOK=FALSE /\ historyOK=FALSE
Validate ==
  \E valid \in BOOLEAN :
    /\ phase="validate" /\ extractionOK'=valid
    /\ phase'=IF valid THEN "history" ELSE "error"
    /\ UNCHANGED <<qualified,fault,sampled,good,requested,actualGate,historyOK>>
History ==
  \E valid \in BOOLEAN :
    /\ phase="history" /\ historyOK'=valid
    /\ qualified'=(extractionOK /\ valid)
    /\ phase'=IF valid THEN "sample" ELSE "error"
    /\ UNCHANGED <<fault,sampled,good,requested,actualGate,extractionOK>>
Sample ==
  \E known \in BOOLEAN, gate \in BOOLEAN :
    /\ phase="sample"
    /\ sampled'=TRUE /\ good'=known /\ actualGate'=gate
    /\ requested'=IF known THEN gate ELSE FALSE
    /\ phase'=IF known THEN "publish" ELSE "error"
    /\ fault'=~known
    /\ UNCHANGED <<qualified,extractionOK,historyOK>>
Publish == /\ phase="publish" /\ phase'="done"
           /\ UNCHANGED <<qualified,fault,sampled,good,requested,actualGate,extractionOK,historyOK>>
Next == Validate \/ History \/ Sample \/ Publish
Spec == Init /\ [][Next]_vars
NoUnqualifiedPlan == phase="done" => (qualified /\ extractionOK /\ historyOK /\ ~fault /\ sampled /\ good)
NoInventedGate == requested => (good /\ actualGate)
=======================================================================
