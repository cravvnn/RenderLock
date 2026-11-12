---------------------- MODULE ModifierSerialState ----------------------
EXTENDS Integers, Naturals, TLC
\* modifier_serial_state.fold_capture: complete pure serial-keyed fold of ordered
\* raw ActiveModifiers Delta writes. Callers: tests and forthcoming mask planner.
\* Protocol oracle: pinned Boon ModifierState::apply_delta; snapshots are not
\* events, slot reuse is not removal, omitted fields do not erase prior values.
\* Model covers order/merge/removal/error/return, NOT client effect activation,
\* duration/aura/view gates, protobuf parsing or digests (separately validated).
CONSTANT Unknown
Serials == {0,1}
Slots == {0,1}
Empty == [live |-> FALSE, value |-> Unknown]
VARIABLES phase, pos, ledger, oracle, slots, accepted, badInput
vars == <<phase,pos,ledger,oracle,slots,accepted,badInput>>
Init == /\ phase="fold" /\ pos=0
        /\ ledger=[s \in Serials |-> Empty] /\ oracle=ledger
        /\ slots=[s \in Slots |-> Unknown] /\ accepted=FALSE /\ badInput=FALSE
Observe ==
  \E kind \in {"delta","snapshot","remove"}, serial \in Serials,
     slot \in Slots, value \in {Unknown,0,1} :
    LET base == ledger
        target == serial
        skip == kind="snapshot"
        merged == IF value=Unknown THEN IF base[target].live THEN base[target].value ELSE Unknown ELSE value
        nextValue == IF kind="remove" THEN Empty ELSE [live |-> TRUE,value |-> merged]
        expected == IF kind="remove" THEN Empty ELSE
          [live |-> TRUE,value |-> IF value=Unknown THEN IF oracle[serial].live THEN oracle[serial].value ELSE Unknown ELSE value]
    IN /\ phase="fold" /\ pos<3 /\ pos'=pos+1
       /\ ledger'=IF skip THEN ledger ELSE [base EXCEPT ![target]=nextValue]
       /\ oracle'=IF kind="snapshot" THEN oracle ELSE [oracle EXCEPT ![serial]=expected]
       /\ slots'=IF kind="snapshot" THEN slots ELSE [slots EXCEPT ![slot]=serial]
       /\ UNCHANGED <<phase,accepted,badInput>>
MissingSerial == /\ phase="fold" /\ pos<3 /\ phase'="error" /\ badInput'=TRUE
                 /\ UNCHANGED <<pos,ledger,oracle,slots,accepted>>
Finish == /\ phase="fold" /\ pos=3 /\ phase'="done" /\ accepted'=TRUE
          /\ UNCHANGED <<pos,ledger,oracle,slots,badInput>>
Next == Observe \/ MissingSerial \/ Finish
Spec == Init /\ [][Next]_vars
RawConsistency == ledger=oracle
NoUnqualifiedResult == accepted => (~badInput /\ pos=3 /\ phase="done")
=======================================================================
