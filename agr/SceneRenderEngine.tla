---------------------- MODULE SceneRenderEngine ----------------------
EXTENDS TLC
\* blender_scene.build_scene after factory reset: choose declared renderer,
\* build camera, then engine-dependent effects, then save/report that engine.
\* An engine changed only at save cannot repair an already built light graph.
\* Actor/camera math, Blender APIs and package allocation have separate tests
\* and ParticleModelAdmission; this model covers only stage ordering.
SkipEarlyEngine == FALSE
SkipCamera == FALSE
VARIABLES phase, target, engine, cameraReady, bakedEngine, published
vars == <<phase,target,engine,cameraReady,bakedEngine,published>>
Init == /\ phase="choose" /\ target \in {"CYCLES","EEVEE"}
        /\ engine \in {"CYCLES","EEVEE"} /\ cameraReady=FALSE
        /\ bakedEngine="none" /\ published=FALSE
Choose == /\ phase="choose" /\ phase'="camera"
          /\ engine'=(IF SkipEarlyEngine THEN engine ELSE target)
          /\ UNCHANGED <<target,cameraReady,bakedEngine,published>>
Camera == /\ phase="camera" /\ phase'="effects" /\ cameraReady'=~SkipCamera
          /\ UNCHANGED <<target,engine,bakedEngine,published>>
Effects == /\ phase="effects" /\ phase'="save" /\ bakedEngine'=engine
           /\ UNCHANGED <<target,engine,cameraReady,published>>
Save == /\ phase="save" /\ phase'="done" /\ published'=TRUE
        /\ UNCHANGED <<target,engine,cameraReady,bakedEngine>>
Next == Choose \/ Camera \/ Effects \/ Save
Spec == Init /\ [][Next]_vars
CorrectEngineAtConstruction == bakedEngine#"none" => bakedEngine=target
CameraBeforeEffects == bakedEngine#"none" => cameraReady
CorrectPublishedEngine == published => (engine=target /\ bakedEngine=target)
=======================================================================
