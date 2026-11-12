---------------------- MODULE ModelTransformHierarchy ----------------------
EXTENDS Integers, Naturals, TLC
\* blender_particle_model_transform keyframe/linearize protocol after its owned
\* constructor returns. Create abstracts the established hierarchy handle;
\* parentsOK/axesOK describe call-time integrity, including changed caller input.
\* Constructor numeric/admission details are code/tests outside this model. Composed
\* with build_package_effects ownership rollback and the existing tick loop.
\* Native/Blender numerical checks prove matrix multiplication, not this model.
\* Parent links and axes must stay qualified through both frame/leaf writes.
\* No file/cache clock or particle lifecycle is introduced by this hierarchy.
VARIABLES phase, parentsOK, axesOK, frame, rootFrame, leafFrame, published
vars == <<phase,parentsOK,axesOK,frame,rootFrame,leafFrame,published>>
Init == /\ phase = "create" /\ parentsOK \in BOOLEAN /\ axesOK \in BOOLEAN
        /\ frame = -1 /\ rootFrame = -1 /\ leafFrame = -1 /\ published = FALSE
Create == /\ phase = "create" /\ phase' = "sample"
          /\ UNCHANGED <<parentsOK,axesOK,frame,rootFrame,leafFrame,published>>
Begin == /\ phase = "sample" /\ frame < 2
         /\ phase' = (IF parentsOK /\ axesOK THEN "root" ELSE "refused")
         /\ frame' = frame+1
         /\ UNCHANGED <<parentsOK,axesOK,rootFrame,leafFrame,published>>
Root == /\ phase = "root" /\ rootFrame' = frame /\ phase' = "leaf"
        /\ UNCHANGED <<parentsOK,axesOK,frame,leafFrame,published>>
Leaf == /\ phase = "leaf" /\ leafFrame' = frame /\ phase' = "sample"
        /\ UNCHANGED <<parentsOK,axesOK,frame,rootFrame,published>>
Publish == /\ phase = "sample" /\ frame = 2
           /\ published' = TRUE /\ phase' = "done"
           /\ UNCHANGED <<parentsOK,axesOK,frame,rootFrame,leafFrame>>
Terminal == /\ phase \in {"done","refused"} /\ UNCHANGED vars
Next == Create \/ Begin \/ Root \/ Leaf \/ Publish \/ Terminal
Spec == Init /\ [][Next]_vars
OwnedParents == ~published \/ parentsOK
FixedAxes == ~published \/ axesOK
CompleteRoot == ~published \/ rootFrame = 2
CompleteLeaf == ~published \/ leafFrame = 2
==========================================================================
