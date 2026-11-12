---------------------- MODULE SceneRenderEngine ----------------------
EXTENDS Naturals, TLC
\* blender_scene.build_scene after factory reset: choose declared renderer,
\* build camera, then engine-dependent effects, then configure particle
\* transparency before save/report. particle_transparency.py only mutates the
\* newly generated scene setting; package allocation/rollback is separate.
\* An engine changed only at save cannot repair an already built light graph.
\* Actor/camera math, Blender APIs and package allocation have separate tests
\* and ParticleModelAdmission; this model covers only stage ordering.
SkipEarlyEngine == FALSE
SkipCamera == FALSE
SkipTransparency == FALSE
ReduceHigherBudget == FALSE
ConfigureOtherScenes == FALSE
VARIABLES phase, target, engine, cameraReady, bakedEngine, published,
          hasGeometry, initialBounces, transparentBounces
vars == <<phase,target,engine,cameraReady,bakedEngine,published,
          hasGeometry,initialBounces,transparentBounces>>
Init == /\ phase="choose" /\ target \in {"CYCLES","EEVEE"}
        /\ engine \in {"CYCLES","EEVEE"} /\ cameraReady=FALSE
        /\ bakedEngine="none" /\ published=FALSE /\ hasGeometry \in BOOLEAN
        /\ initialBounces \in {8,128,256} /\ transparentBounces=initialBounces
Choose == /\ phase="choose" /\ phase'="camera"
          /\ engine'=(IF SkipEarlyEngine THEN engine ELSE target)
          /\ UNCHANGED <<target,cameraReady,bakedEngine,published,hasGeometry,initialBounces,transparentBounces>>
Camera == /\ phase="camera" /\ phase'="effects" /\ cameraReady'=~SkipCamera
          /\ UNCHANGED <<target,engine,bakedEngine,published,hasGeometry,initialBounces,transparentBounces>>
Effects == /\ phase="effects" /\ phase'="transparency" /\ bakedEngine'=engine
           /\ UNCHANGED <<target,engine,cameraReady,published,hasGeometry,initialBounces,transparentBounces>>
Transparency == /\ phase="transparency" /\ phase'="save"
                /\ transparentBounces'=(IF ~SkipTransparency /\
                     (ConfigureOtherScenes \/ (engine="CYCLES" /\ hasGeometry))
                     THEN IF ReduceHigherBudget \/ transparentBounces<128 THEN 128 ELSE transparentBounces
                     ELSE transparentBounces)
                /\ UNCHANGED <<target,engine,cameraReady,bakedEngine,published,hasGeometry,initialBounces>>
Save == /\ phase="save" /\ phase'="done" /\ published'=TRUE
        /\ UNCHANGED <<target,engine,cameraReady,bakedEngine,hasGeometry,initialBounces,transparentBounces>>
Next == Choose \/ Camera \/ Effects \/ Transparency \/ Save
Spec == Init /\ [][Next]_vars
CorrectEngineAtConstruction == bakedEngine#"none" => bakedEngine=target
CameraBeforeEffects == bakedEngine#"none" => cameraReady
CorrectPublishedEngine == published => (engine=target /\ bakedEngine=target)
ParticleTransparencyBudget == published /\ target="CYCLES" /\ hasGeometry => transparentBounces>=128
PreserveHigherBudget == transparentBounces>=initialBounces
PreserveOtherScenes == published /\ ~(target="CYCLES" /\ hasGeometry) => transparentBounces=initialBounces
=======================================================================
