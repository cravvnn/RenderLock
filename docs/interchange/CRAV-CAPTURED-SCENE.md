# Captured scene direction

Status: design and qualification plan, not an implemented profile. This records
the requested Game -> Dolly -> .crav -> RenderLock -> Blender workflow. The
existing `replay-reconstruction-v1` file carries camera observations and replay
identity; it does not carry the captured scene and cannot satisfy this goal.

## What the user exports

One `.crav` contains the observed changing scene state for the selected shots.
RenderLock resolves exact static assets and assembles an editable Blender scene
from those observations. It must not substitute replay-evaluated animation for
missing captured poses. A replay can remain capture provenance and a diagnostic
input; importing a complete captured scene must not require replay simulation.

The package is not a renamed `.blend`. Blender creates the `.blend` after import.
Embedding all mesh and texture bytes would make the handoff much larger, so the
default design references content-identified local assets. This is consequently
not a self-contained portable scene: the receiving machine needs matching assets.
Missing or incompatible assets stop import with a location/identity report.

Preserve the one extension. A new version/profile must distinguish captured
scenes from existing camera/reconstruction files. Do not add scene fields to v1,
reinterpret old files, or let older readers silently ignore the new payload.
Container encoding, profile name and version are not frozen by this document.
Choose them after a real bounded sample establishes the required fields and size.

## Required observations

| Domain | Data and acceptance requirement |
| --- | --- |
| Capture identity | Exact replay digest, runtime/module qualification, capture session and shot identity, declared capture features and completeness. Build numbers alone are insufficient. |
| Frame join | Explicit capture sample identity linking camera, replay tick plus fractional phase, engine view/scene provenance and each observed component. Equal floating-point time or adjacent scene-frame numbers alone are insufficient. |
| Camera | Actual final rendered camera basis, position, lens and output dimensions, with the existing strict cadence and projection checks. |
| Entities | Full handle including serial/generation, lifetime, model and rig identity, observed transforms and visibility, model changes and parenting where applicable. Never join by recycled entity index alone. |
| Poses | Actual evaluated bone transforms, qualified bone ordering and transform convention, source origin/scale and any deformation required for the supported model. Preserve captured state; no root rebasing borrowed from the bone-picker UI. |
| Effects | Observed particle/trail/model/light state and lifetimes, with the particle backend's real coverage limits. Separate runs cannot be relabelled as observations of the same captured frame. |
| World | Runtime world identities and transforms, exact consumed resource/dependency identity, and qualified shot-path map selection. A replay header's map name is not the complete runtime world list. |
| Overrides | Typed captured object/visibility/environment changes, or an explicit unsupported result. Do not silently drop Dolly changes that affect the scene. |

Missing, conflicting, late or truncated required observations invalidate a strict
capture. A component that never received a callback is not automatically absent
or invisible. Establish completeness at a qualified frame boundary and account
for culled entities, shadows, reflections and other dependent content. Any partial
study must remain explicitly identified as partial, never presented as a finished
scene export. Repeated identical draws are not extra animation frames.

## Keep the handoff bounded

Store changing numeric state once per qualified sample; deduplicate unchanged
static descriptions and reference meshes/textures by identity. Use bounded typed
binary chunks if measurements justify them. Compression must preserve the captured
values; there is no approval for lossy bone/camera simplification. Enforce limits
on decoded data as well as compressed bytes, counts, chunks and dependency depth.
No archive paths, embedded scripts or external commands may control extraction.

The current raw pose buffer uses 32 bytes per bone. As an illustration, not a
portable format commitment, one 120-bone actor over 31 samples is 119,040 bytes.
Twelve 256-bone actors over 601 samples are 59,080,704 bytes (56.34 MiB) before
camera, effects, identities or container overhead. Therefore a small handoff
cannot mean a fixed tiny size for every take. Measure the actual data and provide
a bounded estimate before capture. Refuse a budget overrun without silently
decimating or publishing an incomplete file. Do not reuse v1's JSON limit without
measuring the new representation.

Map selection must cover the whole shot and needed shadow/reflection/lighting
dependencies. Frustum selection does not establish occlusion. Do not silently
import the entire map or delete hidden geometry that contributes to the image.

## Native acquisition findings

The current Dolly source provides useful pieces but not a scene exporter:

- `bridge_win.cpp::sample_picker` reads the bone buffer, then subtracts its root
  translation and adds the current motion anchor to every bone position. That is
  a camera-preview correction, not an unchanged capture of the submitted pose.
- `dolly_attach_runtime.hpp::sample` guards entity/model/buffer identity but reads
  and rebases selected positions for attachment. Its successful result does not
  validate every bone's rotation/scale or atomicity of a full pose-buffer read.
- `dolly_player_capture.cpp::hook_common` invokes the pose observer **before**
  forwarding the original mesh producer. Its location alone proves neither
  post-evaluation pose readiness nor that the CPU pose matches submitted skinning.
- `set_pose_observer` owns one callback, already used by attached-camera history.
  Replacing it with an exporter would displace that consumer. Integrate at the
  existing owner with explicit lifetime and bounded work; do not install a second
  competing hook or perform file I/O inside the render callback.
- Attached-camera history uses a `source.scene + 1 == scene` relation and a view
  serial. This is a consumer-specific association, not a general scene-frame join.
- Camera and player-capture code both receive authored replay phase. A common
  phase value is necessary evidence but does not prove a shared sampled instant.

These findings are source-level only. They do not qualify a new native layout,
CPU-to-GPU pose interpretation or renderer output. Pin relevant modules and source
inputs for the actual instrumented experiment.

## Next bounded experiment

Prepare one-character, 31-sample instrumentation using the already proven
camera-only capture owner. This is a private pose/camera study, not a full scene
export. Before a game run:

1. Qualify the pose buffer schema, coordinate convention, rig order, producer
   timing and an independent submitted-pose comparison on the installed build.
   Retain raw bytes and source observations; do not infer unobserved semantics.
2. Define a shared sample identifier and frame seal, required component set,
   duplicate handling, identity changes and cancellation. Extend adjacent state
   models before implementing the new capture lifecycle. Check missing/late
   observations, overflow, handle reuse and cancellation; preserve attachment and
   normal export ownership.
3. Use preallocated bounded buffers; derive byte caps from qualified bone count
   and sample budget. Validate counts and all numeric values. Bracket identity
   reads, but do not describe matching identities as proof against in-place pose
   writes. Resolve that concurrency question at the qualified producer boundary.
4. Establish an independent numerical oracle from the same run and frame, such as
   qualified submitted deformation for known vertices. A Blender re-open compared
   only with the exporter's own arrays is useful persistence proof, not evidence
   that the arrays match the game.
5. Obtain explicit live scheduling handoff, then run the fresh normal DollyApp
   preflight, storage/process/config/source checks and one bounded owned replay.
   No repeated launch until the prior run is fully audited and restored. A missing
   reservation is not scheduling permission.

After the run, require exact sample/identity joins, bounded output, cleanup audit
and the independent pose comparison before testing Blender transforms and saved
scene persistence. Report visual comparison separately. Only then freeze the
character chunk contract and extend to world selection and common-frame effects.
The existing particle work is evidence to integrate, not a license to claim those
features are already captured by `.crav`.
