# Cloth / secondary motion

Enable **Cloth / secondary motion (experimental)** with **Installed game**. Map conversion can remain off.

RenderLock first checks the version-matched definitions in the selected asset cache (`prepared-cloth-v1`). On a cache miss, the bundled model extractor reconstructs supported painted proxies automatically from the installed model. No separate proxy download or developer cache is required for those layouts. RenderLock binds the FE-weighted garment in the rest pose and stores its approximate motion as shape keys. The coverage report lists which entities were baked and which remain unsupported.

## Coverage and limits

- Definitions are validated per hero and per compiled model: the compiled-model hash, decoded physics, resource identity and definition integrity must match your installed game build. Game updates invalidate entries until they are re-verified.
- Automatic reconstruction and saved-scene checks cover the current McGinnis and Fencer garments, including replay `119690843.dem`, ticks 1000–1300. Existing manually prepared Dynamo/Geist definitions remain supported. Multi-proxy and imported bone-cloth layouts remain unsupported; finding FE nodes alone is not enough to claim a garment was baked.
- Reconstruction is approximate: it reproduces the authored proxy and collision shapes, not recorded Source 2 cloth playback. There is no recorded pre-shot cloth state, self-collision or map collision.
- Prepared cloth is tied to the body animation, range and FPS used to compute it. Regenerate the export after editing animation, range or FPS.

## Retired native resimulation

The experimental native Blender resimulation — Seven's waist surface cloth, its scene rebake helper (`Rebake_AGR_Cloth.py`) and the Haze hose prototype — was retired in 0.3.0b1 and is no longer part of the export or the package. The decompiled-proxy path above replaces it.
