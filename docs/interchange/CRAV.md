# Lightweight Dolly → RenderLock contract

Status: camera/replay contract candidate, retained as groundwork. The requested
captured-scene workflow is specified separately in
[CRAV-CAPTURED-SCENE.md](CRAV-CAPTURED-SCENE.md); this v1 profile does not implement
that workflow. Dedicated no-video camera capture has been exercised, while full
scene reconstruction is NOT implemented by this specification. The executable validator
is RenderLock's `shot_package.py`; the adjacent `minimal.crav` is synthetic, not a real
replay or visual-quality proof.

## One user-facing file

The extension is **.crav**, a UTF-8 JSON document with format
`renderlock-shot-collection`, version `1`, profile `replay-reconstruction-v1`.
The profile is mandatory for new Dolly exports. It is not another extension.
Earlier unprofiled research collections remain readable as camera-only artifacts;
they are not automatically promoted to this contract. Older strict readers reject
the new fields rather than silently ignoring reconstruction requirements.

No replay, mesh, texture, video, depth, audio, scene payload, archive or executable
code is embedded. `scene` and shot `media` fields are forbidden, even when empty.
No .rlscene file must be supplied by the user. RenderLock may use its own scene
format internally. Camera samples are embedded and need no sidecar. Camera source
paths, if supplied for provenance, are not required files or executable commands.

The receiving computer needs the exact replay and compatible installed game
assets. Paths are location hints, relative to the .crav directory when relative;
they are not identity. Missing files require user location, not automatic download
or arbitrary filesystem search. Replay SHA-256 must match before reconstruction.
The replay parser must also verify declared map, build, tick rate and tick count
against decoded metadata. Installed assets must pass RenderLock's existing source
qualification; a build number alone is not asset compatibility proof.

## Required data and units

All objects reject unknown keys. Duplicate JSON keys, non-finite numbers, invalid
types and unknown versions/profiles fail. Optional `created_with` has a name and
optional version; optional notes are informational, never executable settings.

| Object | Required fields / meaning |
| --- | --- |
| replay | path hint, lowercase 64-hex sha256, positive tick_rate, positive integer build, map name, positive integer total_ticks (exclusive tick bound) |
| shots | 1–256 shots; unique id, name, range, camera; overlapping shots allowed |
| range | integer start_tick; end_tick_exact is the final sampled replay tick; end_tick = floor(end_tick_exact + 1e-6) |
| camera | source, space=`source-world`, unit_scale=0.0254 metres, time_origin=`shot-start`, fps, integer width/height, frame_count, samples |
| source | kind=`dolly-recorded-camera` for the future direct exporter; existing recorded dolly-camera-track/bundle adapters also allowed; optional version/path/sha256/size_bytes/note |
| sample | zero-based frame, video_time, replay_time, position, right/up/forward, horizontal_fov in degrees, aspect, state=`complete` |

World axes: Source X forward, Y left, Z up, absolute Source positions. Basis vectors
are unit and orthogonal with right = forward × up. No origin rebasing is baked into
the file. FOV is strictly between 1 and 179 degrees; aspect matches width/height.

`video_time` means output-frame time, even without encoded video: frame / fps.
`replay_time` is elapsed replay seconds relative to range.start_tick, NOT absolute
match seconds. Absolute replay tick = start_tick + replay_time × tick_rate.
The first sample can have a nonzero offset. The final sample must exactly account
for end_tick_exact within 1e-6 ticks, below total_ticks. A one-frame shot is valid.
Reconstruction needs the ceiling of a fractional last tick for interpolation,
subject to replay bounds; end_tick is not permission to truncate that sample.

Profile v1 supports normal-speed playback only: replay_time minus its first-sample
offset equals frame / fps within 1e-6 seconds. Holds, slow motion, reverse playback
and cuts within a shot are rejected. Supporting them later requires synchronized
scene retiming, not merely changing camera keyframe timestamps. Separate shots
represent cuts. Different shot FPS values are allowed; the importer must process
shots individually or explicitly resolve output FPS rather than silently merge.

Optional sample projection is 16 row-major values with column-vector semantics
and requires camera.projection_convention=`row-major-column-vector`. Existing
inverse_z, viewport, near/far and capture_time/sample provenance remain available.
Projection preservation is not a claim that arbitrary jitter, asymmetric lens,
depth reconstruction or postprocessing is supported by Blender. Import capability
checks must reject unsupported projection behavior rather than silently substitute
a FOV-only camera. The minimal producer should omit projection unless qualified.

## Reconstruction request

Required `reconstruction` contains:

```json
{
  "map_selection": {
    "mode": "camera-path",
    "margin_source_units": 128,
    "include_lighting_dependencies": true
  },
  "missing_content": "error"
}
```

128 units is an example editing margin (3.2512 m), not a universal quality proof.
The allowed margin is 0–65536 Source units. The receiver must select the union of
needed map sections over all selected shots and the full interpolated camera path,
including temporal/subframe coverage where needed. One start-frame frustum is not
sufficient. Expand for the requested margin; retain geometry/materials needed for
shadows, reflections, lighting probes and other qualified visible dependencies.

Frustum selection and true occlusion are different. Hidden geometry can be omitted
only when safely established across the shot. Moving occluders, transparency,
reflections and shadow contributors prevent simplistic depth-based deletion.
Conservative extra sections are preferable to missing content. There is no promise
of a minimum mesh count or fixed scene size. If bounded selection cannot satisfy
the request, report the limitation; do not silently import the whole map or remove
necessary content. A larger/full-map fallback requires the user's explicit choice.

RenderLock uses its normal character, animation, material, lighting and effects
pipeline. No alternate lower-quality scene generator. Camera-dependent stages must
receive the shot camera before generating content. Unsupported content stops a
strict export with an actionable report; a partial result requires explicit consent
and must stay labelled partial. Successful JSON validation proves none of these
rendering capabilities. The existing camera-only importer deliberately refuses this
profile until the reconstruction workflow can honor its requirements.

## Dolly step 2 obligations

Write directly from a complete recorded camera sampling pass; video encoding is
not required. Do not turn authored keyframes into claimed recorded samples. Pin
the replay identity at capture time and verify it stays the same through the pass.
An interrupted or incomplete pass must not produce a successful final .crav.
Compute SHA-256 over replay bytes, not a filename; do not read the whole replay
into memory. Keep identity checks and output publication bounded and cancellable.

All shots must share the same replay. Export only the requested shots. Avoid
absolute personal paths when a portable location hint works. All image dimensions
and camera samples describe the intended output framing, not an unrelated preview.

There is deliberately no arbitrary cvar/command/override field in v1. Non-camera
Dolly changes (placed objects, visibility controls, environment changes, DOF,
postprocessing, etc.) are not reproduced by this contract. The producer must detect
active relevant overrides and report/refuse an unsupported faithful export; it
must not silently drop them. Camera-only export after explicit user agreement can
be a separate UI choice. Add typed, versioned support only with receiver evidence.

Use the conformance example and negative tests in both repositories. Required
acceptance for step 2: no-video production capture → validator round trip; replay
mismatch, interrupted capture and unsupported overrides refused; measured bounded
file size. Real scene/map/visual parity acceptance is step 3, not step 2.

## Size and verification

Hard limits: 64 MiB encoded document, 262144 samples across the whole collection,
256 shots. The writer checks encoded size before replacing an output. No lossy
camera simplification or compression is required for v1. Size grows with sample
count, not map size, texture quality or output pixel count. Longer takes can hit
the limit; report it rather than silently decimating. The limits are ceilings,
not typical file sizes. Keep synthetic size measurements separate from real take
measurements and visual fidelity claims.

Step 1 checks cover strict validation, replay byte identity, portable camera
round-trip, coordinate/time arithmetic, unsupported-request refusal and sample
sizes. They do not launch Dolly, render a game scene or prove map culling.
