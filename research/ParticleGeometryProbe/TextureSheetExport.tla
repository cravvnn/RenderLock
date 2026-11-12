---------------------- MODULE TextureSheetExport ----------------------
EXTENDS TLC
\* TextureSheetCommand.Run: source/layout bounds, fresh atlas and descriptor.
\* Called by EffectAssetAdapter.texture_sheet in an existing private cache temp.
\* AssetConverter owns cache admission; this command never retries/publishes it.
\* Read snapshots bounded bytes once; hash and lazy decoding share that immutable
\* MemoryStream. inputOK abstracts snapshot admission and caller hash validation,
\* not a second filesystem read or a claim that the source path cannot change.
\* Competing creation between preflight and either FileMode.CreateNew is modeled.
\* Decoding, PNG/JSON arithmetic, OS crashes and cache protocol are excluded.
SkipInput == FALSE
SkipLayout == FALSE
OverwriteImage == FALSE
OverwriteDescriptor == FALSE
PublishFailedWrite == FALSE
VARIABLES phase, inputOK, layoutOK, image, descriptor, published, overwritten
vars == <<phase,inputOK,layoutOK,image,descriptor,published,overwritten>>
Init == /\ phase="preflight" /\ inputOK \in BOOLEAN /\ layoutOK \in BOOLEAN
        /\ image \in {"none","other"} /\ descriptor \in {"none","other"}
        /\ published=FALSE /\ overwritten=FALSE
Check == /\ phase="preflight"
         /\ phase'=(IF image="none" /\ descriptor="none" THEN "read" ELSE "error")
         /\ UNCHANGED <<inputOK,layoutOK,image,descriptor,published,overwritten>>
Read == /\ phase="read" /\ phase'=(IF inputOK \/ SkipInput THEN "decode" ELSE "error")
        /\ UNCHANGED <<inputOK,layoutOK,image,descriptor,published,overwritten>>
Decode == /\ phase="decode" /\ phase'=(IF layoutOK \/ SkipLayout THEN "image" ELSE "error")
          /\ UNCHANGED <<inputOK,layoutOK,image,descriptor,published,overwritten>>
OtherImage == /\ phase="image" /\ image="none" /\ image'="other"
              /\ UNCHANGED <<phase,inputOK,layoutOK,descriptor,published,overwritten>>
OtherDescriptor == /\ phase="descriptor" /\ descriptor="none" /\ descriptor'="other"
                   /\ UNCHANGED <<phase,inputOK,layoutOK,image,published,overwritten>>
WriteImage == \E success \in BOOLEAN:
 /\ phase="image"
 /\ phase'=(IF image="none" \/ OverwriteImage THEN (IF success THEN "descriptor" ELSE "error") ELSE "error")
 /\ image'=(IF image="none" \/ OverwriteImage THEN "ours" ELSE image)
 /\ overwritten'=(overwritten \/ (image="other" /\ OverwriteImage))
 /\ UNCHANGED <<inputOK,layoutOK,descriptor,published>>
WriteDescriptor == \E success \in BOOLEAN:
 /\ phase="descriptor"
 /\ phase'=(IF descriptor="none" \/ OverwriteDescriptor THEN (IF success THEN "done" ELSE "error") ELSE "error")
 /\ descriptor'=(IF descriptor="none" \/ OverwriteDescriptor THEN "ours" ELSE descriptor)
 /\ published'=((descriptor="none" \/ OverwriteDescriptor) /\ (success \/ PublishFailedWrite))
 /\ overwritten'=(overwritten \/ (descriptor="other" /\ OverwriteDescriptor))
 /\ UNCHANGED <<inputOK,layoutOK,image>>
Next == Check \/ Read \/ Decode \/ OtherImage \/ OtherDescriptor \/ WriteImage \/ WriteDescriptor
Spec == Init /\ [][Next]_vars
QualifiedOutput == published => (inputOK /\ layoutOK /\ image="ours" /\ descriptor="ours" /\ phase="done")
PreserveOthers == ~overwritten
=============================================================================
