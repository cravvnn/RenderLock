# Cloth / secondary motion

Enable **Cloth / secondary motion (experimental)** with **Installed game**. Map conversion can remain off.

RenderLock matches each character's compiled model against the version-matched prepared cloth definitions in the selected asset cache (`prepared-cloth-v1`). A matching definition reconstructs the authored proxy, pins and collision shapes, binds the FE-weighted garment in the rest pose and stores the resulting motion as shape keys. The coverage report lists which entities were baked and which were explicitly unsupported.

## Coverage and limits

- Definitions are validated per hero and per compiled model: the compiled-model hash, decoded physics, resource identity and definition integrity must match your installed game build. Game updates invalidate entries until they are re-verified.
- The validated development definitions cover Dynamo; Geist's skirt uses authored-strength pin approximations. Other heroes are not validated, and a cache miss reports the entity as unsupported rather than claiming a new layout.
- Reconstruction is approximate: it reproduces the authored proxy and collision shapes, not recorded Source 2 cloth playback. There is no recorded pre-shot cloth state, self-collision or map collision.
- Prepared cloth is tied to the body animation, range and FPS used to compute it. Regenerate the export after editing animation, range or FPS.

## Retired native resimulation

The experimental native Blender resimulation — Seven's waist surface cloth, its scene rebake helper (`Rebake_AGR_Cloth.py`) and the Haze hose prototype — was retired in 0.3.0b1 and is no longer part of the export or the package. The decompiled-proxy path above replaces it.
