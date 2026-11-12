--------------------------- MODULE GeometryExport ---------------------------
EXTENDS TLC
\* Program.cs top-level command, called by CLI/build qualification. Every
\* combination of request/path, destination, model, export and size outcomes.
\* descriptor denotes an admitted successful result, not mere file existence.
\* Morph and static commands are explicit; no failed-morph fallback is allowed.
\* Typed metadata uses the same prepare/bounded-write/success admission steps.
\* Export/metadata I/O errors may leave unaccepted partial files for diagnosis.
\* Native decoding/GLB arithmetic, filesystem races and crash durability are not
\* modeled. No cache publication or retry exists in this helper. Future callers
\* must hash+validate results inside their own conversion-cache transaction.
VARIABLES phase, argsOK, emptyOutput, modelOK, exportOK, geometryOK, descriptor
vars == <<phase,argsOK,emptyOutput,modelOK,exportOK,geometryOK,descriptor>>
SkipArguments == FALSE
SkipEmptyOutput == FALSE
SkipModel == FALSE
SkipExport == FALSE
SkipGeometryBound == FALSE
Init == /\ phase="arguments" /\ descriptor=FALSE
 /\ argsOK \in BOOLEAN /\ emptyOutput \in BOOLEAN /\ modelOK \in BOOLEAN
 /\ exportOK \in BOOLEAN /\ geometryOK \in BOOLEAN
Step(from,to,valid) == /\ phase=from
 /\ phase'=(IF valid THEN to ELSE "failed")
 /\ descriptor'=(valid /\ to="done")
 /\ UNCHANGED <<argsOK,emptyOutput,modelOK,exportOK,geometryOK>>
Next == Step("arguments","destination",argsOK \/ SkipArguments)
 \/ Step("destination","model",emptyOutput \/ SkipEmptyOutput)
 \/ Step("model","export",modelOK \/ SkipModel)
 \/ Step("export","bound",exportOK \/ SkipExport)
 \/ Step("bound","done",geometryOK \/ SkipGeometryBound)
Spec == Init /\ [][Next]_vars
ValidatedResult == descriptor => argsOK /\ modelOK /\ exportOK /\ geometryOK
PreserveExistingOutput == descriptor => emptyOutput
NoPrematureSuccess == descriptor => phase="done"
=============================================================================
