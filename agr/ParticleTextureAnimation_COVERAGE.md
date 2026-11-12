# Original flat particle sheets (pending integration qualification)

`particle_texture_animation` validates immutable atlas timing and selects two
authored rectangles plus a blend value from existing particle age/lifetime/manual
frame inputs. It adds no clock, emitter, captured birth or cross-call state.
Fixed-rate, fit-lifetime and manual modes use the reference VRF sheet convention.
Timing arithmetic follows float32 steps, including the minimum normalized-age
denominator. Sprite behavior versions below 10 retain the reference constructor's
fixed-rate mode; trails retain their explicit animation mode. This is reference
CPU behavior, not a new claim of native Source render-clock equivalence.
The comparison source is ValveResourceFormat revision
`42743fb93030c552f834fabb30708dad84d0cbf5`,
`Renderer/Particles/Renderers/ParticleFunctionRenderer.cs` (`GetSheetFrame`),
`RenderSprites.cs` (constructor gate), `RenderTrails.cs`, and
`ValveResourceFormat/Resource/ResourceTypes/Texture.cs` (frame selection).
`Particle.NormalizedAge` supplies the reference minimum denominator.
Typed original array order is authoritative; crop-file IDs do not reorder it.
Split-channel rectangles, alpha cropping, animated rope textures, luminance
blending and unsupported layer modes remain refusals. Motion-vector metadata may
be retained by the helper but its paired-flow renderer is not enabled here.

`EffectAssetAdapter.texture_sheet` uses the existing `AssetConverter._entry`
transaction unchanged. Source/helper/parser/validator hashes are cache inputs;
source identity, bounded timing, PNG bytes/dimensions and relative member path
are checked both after conversion and when reading the result. No new cache
protocol, retry or persistent owner is introduced.

`TextureSheetExport.tla`, beside the helper, models source/layout admission,
exclusive image/descriptor writes and descriptor success. It includes competing
creation after preflight and partial image/serialization failure. Its 45-state model
and five guard mutants do not establish decoding arithmetic, PNG/JSON contents,
filesystem crash durability or the existing conversion-cache protocol. Python
negative tests independently exercise source/image/dimension/path/exit rejection.
The helper snapshots bounded source bytes once and uses that same immutable
MemoryStream for lazy decoding and the reported digest. It does not reopen a
mutable path between hashing and decoding. The adapter separately binds that
digest to the provider's expected compiled texture identity.

Blender nodes are owned by the existing whole-instance package transaction.
Atlas rectangles and crossfade values are held at replay samples to avoid
interpolating across unrelated atlas regions. Other material controls retain
their existing interpolation. This is not engine subframe animation or filtering
parity. Static assets retain their existing texture path. Shader UV bypass still
requires the installed shader proof and now binds original sheet source identity.

Simplification reuses existing samples, layer composition, image packing and
rollback. It adds no new renderer queue, frame handler or simulation clock.
The copied optional motion inputs do not admit paired motion-vector layers.

Status: bounded normal-export source candidate qualified on the measured Windows
build. A fresh 60 FPS normal CLI export imports original flashbang2042:1 with
158 effect objects, one light, 33 simulator frames and its entire 13-definition
tree. Six existing mask objects are retained. Other historical/parent layers
remain explicitly unsupported; no entire-replay or full-map completion claim.

Independent reopen checks 29 actual fireball samples, 56 between-tick holds and
25 distinct rectangles from its 30-frame sheet. The saved hero passes 4,950 bone
and 33 root checks. All 23 preexisting object bindings, 21 Actions and 16 meshes
match the earlier target-refused scene exactly; images are packed, libraries=0.
Actual late shader-key failure rolls back every newly allocated data block.

The normal hero-follow camera looks away from this explosion. Its four completed
CPU Cycles frames therefore establish scene stability, not effect visibility.
A separately rebuilt inspection camera uses the exact fresh normal package,
requalifies depth before construction, then independently reopens and renders
four visible effect frames. Its 66 ring meshes pass 57 held-visibility checks at
24/30/60 FPS; the atlas Action remains exact to the normal export. Only 60 FPS
is a fresh complete CLI export. Atlas sampling is held at replay ticks; native
subframe/derivative/filtering behavior and Source lighting are not established.

The managed geometry helper retains the qualified geometry parser and freshly
reproduces earlier smoke/color+flow atlas metadata and PNG bytes, plus bat morph
bytes. The simulation helper separately adds typed shader fields39/40 and their
policy. Material parsing stays isolated and unchanged. See the owned-literal,
static-geometry and rope coverage notes. Application rebuild/release is separate.
