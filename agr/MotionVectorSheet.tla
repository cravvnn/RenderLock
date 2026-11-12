------------------------ MODULE MotionVectorSheet ------------------------
EXTENDS Integers, Naturals, TLC
\* particle_motion_vectors qualification and synchronous shader construction.
\* Caller: blender_simulated_effects.validate_renderer_classes before allocation,
\* then material/mapped_texture before build_layer_stack. Sprite profiles only.
\* Existing source-sheet parser/cache and parent particle clock are interfaces.
\* Numerical signed flow/UV/color and saved GPU pixels are checked separately.
VARIABLES phase, paired, metadata, raw, current, next, warpedCurrent,
          warpedNext, flowInColor, published
vars == <<phase,paired,metadata,raw,current,next,warpedCurrent,warpedNext,flowInColor,published>>
Init == /\ phase = "qualify" /\ paired \in BOOLEAN /\ metadata \in BOOLEAN
        /\ raw = FALSE /\ current = -1 /\ next = -1
        /\ warpedCurrent = FALSE /\ warpedNext = FALSE
        /\ flowInColor = FALSE /\ published = FALSE
Qualify == /\ phase = "qualify"
           /\ phase' = (IF paired /\ metadata THEN "flow" ELSE "refused")
           /\ UNCHANGED <<paired,metadata,raw,current,next,warpedCurrent,warpedNext,flowInColor,published>>
Flow == /\ phase = "flow" /\ raw' = TRUE /\ current' = 0 /\ next' = 1
        /\ phase' = "current"
        /\ UNCHANGED <<paired,metadata,warpedCurrent,warpedNext,flowInColor,published>>
Current == /\ phase = "current" /\ warpedCurrent' = raw /\ current = 0
           /\ phase' = "next"
           /\ UNCHANGED <<paired,metadata,raw,current,next,warpedNext,flowInColor,published>>
NextColor == /\ phase = "next" /\ warpedNext' = raw /\ next = 1
             /\ phase' = "compose"
             /\ UNCHANGED <<paired,metadata,raw,current,next,warpedCurrent,flowInColor,published>>
Compose == /\ phase = "compose" /\ flowInColor' = FALSE /\ phase' = "publish"
           /\ UNCHANGED <<paired,metadata,raw,current,next,warpedCurrent,warpedNext,published>>
Publish == /\ phase = "publish" /\ published' = TRUE /\ phase' = "done"
           /\ UNCHANGED <<paired,metadata,raw,current,next,warpedCurrent,warpedNext,flowInColor>>
Terminal == /\ phase \in {"done","refused"} /\ UNCHANGED vars
Next == Qualify \/ Flow \/ Current \/ NextColor \/ Compose \/ Publish \/ Terminal
Spec == Init /\ [][Next]_vars
Qualified == ~published \/ (paired /\ metadata)
RawSeparate == phase \notin {"current","next","compose","publish","done"} \/ (raw /\ current = 0 /\ next = 1)
BothFramesWarped == ~published \/ (warpedCurrent /\ warpedNext)
FlowIsData == ~published \/ ~flowInColor
==========================================================================
