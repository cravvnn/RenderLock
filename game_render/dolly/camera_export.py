"""
Camera exports for After Effects (.jsx) and USD (.usda).

The camera track data is recorded to <video>.camera-track.json, with one
row per video frame. Without the recorded data, the shot's keyframes are sampled
instead. Player POV takes need the camera track since their keyframes are placeholders.

X forward, Y left, Z up. Positions are offsets from the first frame's camera.

After Effects (X right, Y down, Z into the screen)
pos:      (x, y, z) -> (x, -z, y)
rotation: X/Y/Z Rotation, R = Rx * Ry * Rz
zoom:     (comp_width / 2) / tan(hfov / 2)
dof:      Focus Distance = focus distance, Aperture = aperture diameter

USD (Z up, one unit is about an inch)
pos:      (x, y, z) -> (x, y, z)
rotation: rotateXYZ, R = Rz * Ry * Rx
focal:    (horizontalAperture / 2) / tan(hfov / 2)
dof:      focusDistance = focus distance, fStop = (focalLength / 10) / aperture diameter

DOF is derived from authored settings, not renderer lens readback. Native ranges are approximate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import NamedTuple

from .path import CURVE_CHANNELS, POSITION_CHANNELS, Project, _finite

DEFAULT_LENS = .75
DEFAULT_COMP = 1920
GIMBAL = 1 - 1e-9 # gimbal lock floating point rounding errors
MAX_CAMERA_FRAMES = 262144
MAX_CAMERA_BYTES = 128 * 1024 * 1024
CAMERA_FIELDS = ["frame", "video_time", "phase", "x", "y", "z", "pitch", "yaw", "roll", "horizontal_fov"]
CAMERA_FIELDS_V2 = CAMERA_FIELDS + ["capture_time"]


class Frame(NamedTuple):
    """One video frame's camera."""
    frame: int
    time: float  # seconds
    position: tuple[float, float, float]
    angles: tuple[float, float, float]  # pitch, yaw, roll
    fov: float  # horizontal, degrees
    phase: float
    basis: tuple[float, ...] | None = None  # right, up, -forward; full renderer orientation
    projection: tuple[float, ...] | None = None
    capture_time: float | None = None
    video_time: float | None = None


class Marker(NamedTuple):
    """Where the camera was when the shot reached one keyframe."""
    key: int  # keyframe
    time: float  # video seconds
    position: tuple[float, float, float]


class Take(NamedTuple):
    """Every sampled frame, the video length in frames, accuracy warnings and keyframe markers."""
    frames: list[Frame]
    video_frames: int
    warnings: list[str]
    markers: tuple[Marker, ...] = ()
    source: str = "authored"
    size: tuple[int, int] | None = None
    first_qpc: int | None = None
    fixed_step: bool | None = None
    depth: bool = False


def angle_vectors(pitch: float, yaw: float, roll: float):
    """Vectors: forward, right and up."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    forward = (cp * cy, cp * sy, -sp)
    right = (-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp)
    up = (cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp)
    return forward, right, up


def horizontal_fov(lens_scale: float, aspect_ratio: float) -> float:
    """Horizontal FOV in degrees"""
    fov = math.degrees(2 * math.atan(lens_scale * aspect_ratio))
    if not math.isfinite(fov) or not 1 < fov < 179:
        raise ValueError(f"Invalid FOV ({fov})")
    return fov


def comp_size(standard_aspect: float, width: int = DEFAULT_COMP) -> tuple[int, int]:
    """The shot's standard aspect, with an even height."""
    height = max(2, int(round(width / standard_aspect / 2)) * 2)
    return width, height


def sample_camera(project: Project, fps: float, speed: float = 1.0,
                  video_frames: int | None = None) -> Take:
    """Sample the camera data once per video frame."""
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"Invalid fps ({fps})")
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(f"Invalid export speed ({speed})")
    if video_frames is not None and video_frames < 1:
        raise ValueError(f"Invalid frame count ({video_frames})")

    project.validate()
    if not project.keyframes:
        raise ValueError("Add a camera keyframe before exporting the camera.")

    duration = project.duration / speed
    count = int(math.floor(duration * fps + 1e-9)) + 1
    if video_frames is not None:
        count = min(count, video_frames)
    has_lens = any(key.lens_scale is not None for key in project.keyframes)
    position = [project._camera_channel_samples(name) for name in POSITION_CHANNELS]
    angles = [project._camera_channel_samples(name) for name in CURVE_CHANNELS]
    aspect = project._camera_channel_samples("aspect_ratio")
    lens = project._camera_channel_samples("lens_scale") if has_lens else None
    frames = []

    for index in range(count):
        time = index / fps
        phase = time * speed
        lens_scale = lens.evaluate(phase) if lens is not None else DEFAULT_LENS
        frames.append(Frame(index, time, tuple(channel.evaluate(phase) for channel in position),
                            tuple(channel.evaluate(phase) for channel in angles),
                            horizontal_fov(lens_scale, aspect.evaluate(phase)), phase))

    warnings = []
    if any(key.source == "attach" for key in project.keyframes):
        warnings.append("Attach keys are currently unsupported; their markers are left out.")
    if not has_lens:
        warnings.append("No captured lens, assuming a standard 90-degree lens.")

    return Take(frames, video_frames or len(frames), warnings)


def _read_camera_json(path):
    # Bound the actual read as well as the declared size (including replaced files).
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_CAMERA_BYTES + 1)
    if not raw or len(raw) > MAX_CAMERA_BYTES:
        raise ValueError("Camera data is empty or exceeds the 128 MiB limit")
    return json.loads(raw)


def sample_camera_data(path: str | Path, fps: float, *, expected_status=None) -> Take:
    """Validate complete encoded-frame coverage; preserve both capture and conform clocks."""
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"Invalid fps ({fps})")
    data = _read_camera_json(path)
    if (not isinstance(data, dict) or data.get("format") != "deadlock-dolly-camera-track"
            or data.get("version") not in (1, 2) or data.get("fields") != (CAMERA_FIELDS_V2 if data.get("version") == 2 else CAMERA_FIELDS)):
        raise ValueError("Not a supported camera track")
    recorded = data.get("fps")
    if (isinstance(recorded, bool) or not isinstance(recorded, (int, float))
            or not math.isfinite(recorded) or recorded <= 0 or not math.isclose(recorded, fps)):
        raise ValueError(f"The camera data was recorded at {recorded} fps, not {number(float(fps))} fps")
    if type(data.get("fixed_step")) is not bool:
        raise ValueError("The camera data has no valid time policy")
    written, rows = data.get("frames_written"), data.get("frames")
    if data["version"] == 2 and data.get("complete") is not True:
        raise ValueError("Incomplete camera data: " + str(data.get("partial_reason") or "unknown reason"))
    if (type(written) is not int or not 0 < written <= MAX_CAMERA_FRAMES
            or not isinstance(rows, list) or len(rows) != written):
        raise ValueError("Incomplete camera data: every encoded frame needs a camera row")
    size = None
    if data["version"] == 2:
        for field in ("width", "height", "first_qpc", "qpc_frequency"):
            if type(data.get(field)) is not int or data[field] <= 0:
                raise ValueError("Camera data has no valid " + field)
        if data.get("source") != "native-view-hook" or type(data.get("depth")) is not bool:
            raise ValueError("Camera data has no valid source")
        size = (data["width"], data["height"])
    if expected_status is not None:
        # New auto-exports require the current recording identity, not a matching filename.
        if data["version"] != 2:
            raise ValueError("Legacy camera data cannot identify this recording")
        for field in ("frames_written", "first_qpc", "qpc_frequency", "width", "height", "fps"):
            expected = expected_status.get("video_first_qpc" if field == "first_qpc" else field)
            if type(expected) is not int or expected != data[field]:
                raise ValueError("Camera data belongs to a different recording (" + field + ")")
    frames = []
    last_time = -1.0
    for index, row in enumerate(rows):
        if (not isinstance(row, list) or len(row) != (len(CAMERA_FIELDS_V2) if data["version"] == 2 else len(CAMERA_FIELDS))
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in row)):
            raise ValueError("The camera data has an invalid frame")
        frame, video_time, phase, x, y, z, pitch, yaw, roll, fov = row[:10]
        capture_time = row[10] if data["version"] == 2 else video_time
        if frame != index or video_time < 0 or video_time < last_time or phase < 0 or capture_time < 0 or not 1 < fov < 179:
            raise ValueError("The camera data has an invalid or missing frame")
        last_time = video_time
        frames.append(Frame(index, index / fps, (x, y, z), (pitch, yaw, roll), fov, phase,
                            capture_time=capture_time, video_time=video_time))
    return Take(frames, written, [], source="native-view-hook", size=size,
                first_qpc=data.get("first_qpc"), fixed_step=data["fixed_step"], depth=data.get("depth", False))


def sample_camera_bundle(path: str | Path, fps: float) -> Take:
    """Adapt validated renderer data without changing the depth format or reader."""
    from .camera_bundle import load_bundle, validate_bundle
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Invalid camera frame rate")
    bundle = load_bundle(path)
    issues = validate_bundle(bundle, require_complete=True)
    if issues:
        raise ValueError("Camera bundle cannot provide a complete camera: " + "; ".join(issues[:3]))
    if not 0 < bundle.frames <= MAX_CAMERA_FRAMES:
        raise ValueError("Camera bundle frame limit exceeded")
    frames, jitter = [], False
    for index, record in enumerate(bundle.records):
        if record.frame != index or record.view is None:
            raise ValueError("Camera bundle is missing an encoded frame or full view")
        view, projection = record.view, record.projection
        forward, up = view.forward, view.up
        right = tuple(view.matrix[i * 4] for i in range(3))
        pitch = math.degrees(math.atan2(-forward[2], math.hypot(*forward[:2])))
        yaw = math.degrees(math.atan2(forward[1], forward[0]))
        sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
        sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        zero_right, zero_up = (sy, -cy, 0), (sp * cy, sp * sy, cp)
        roll = math.degrees(math.atan2(-sum(a*b for a,b in zip(right, zero_up)),
                                       sum(a*b for a,b in zip(right, zero_right))))
        fov = math.degrees(2 * math.atan(abs(projection[14] / projection[0])))
        if not math.isclose(projection[5] / projection[0], bundle.color_width / bundle.color_height,
                            rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError("Camera bundle projection and output aspect ratio disagree")
        jitter |= projection[2] != 0 or projection[6] != 0
        basis = (*right, *up, *(-v for v in forward))
        frames.append(Frame(index, index / fps, view.position, (pitch, yaw, roll), fov,
                            record.replay_time, basis, projection, record.capture_pts_100ns / 1e7))
    warnings = (["External camera uses the stable projection; per-frame temporal jitter is retained in the depth camera bundle."]
                if jitter else [])
    return Take(frames, bundle.frames, warnings, source="renderer-bundle",
                size=(bundle.color_width, bundle.color_height), depth=True)


def _compare_camera_sources(track: Take, bundle: Take):
    if track.video_frames != bundle.video_frames or track.size != bundle.size:
        raise ValueError("Recorded camera sources disagree on frame count or dimensions")
    for a, b in zip(track.frames, bundle.frames):
        af, ar, au = angle_vectors(*a.angles)
        axes = (*ar, *au, *(-v for v in af))
        if (abs(a.phase - b.phase) > 1e-5 or abs(a.capture_time - b.capture_time) > 2e-6
                or max(abs(x-y) for x,y in zip(a.position,b.position)) > .05
                or max(abs(x-y) for x,y in zip(axes,b.basis)) > 1e-4
                or abs(a.fov - b.fov) > .01):
            raise ValueError("Recorded camera sources disagree at encoded frame " + str(a.frame))


def load_take(project: Project, fps: float, camera_data: str | Path | None = None,
              speed: float = 1.0, video_frames: int | None = None, pov: bool = False,
              *, expected_status=None, camera_bundle=None, require_recorded=False) -> Take:
    """The shared input for both exporters. Auto-export never invents missing motion."""
    if camera_data is not None:
        take = sample_camera_data(camera_data, fps, expected_status=expected_status)
        if camera_bundle is None and take.depth and expected_status is not None:
            camera_bundle = Path(camera_data).parent / "depth" / "camera.json"
        if camera_bundle is not None:
            calibrated = sample_camera_bundle(camera_bundle, fps)
            _compare_camera_sources(take, calibrated)
            take = calibrated._replace(first_qpc=take.first_qpc, fixed_step=take.fixed_step)
    elif camera_bundle is not None and expected_status is None:
        take = sample_camera_bundle(camera_bundle, fps)
    elif pov or require_recorded:
        raise ValueError("Recording saved no camera data; video is preserved")
    else:
        if any(key.source == "attach" for key in project.keyframes):
            raise ValueError("Attached camera export needs recorded camera data")
        take = sample_camera(project, fps, speed, video_frames)
        take.warnings.append("Camera sampled from authored keys, not a recorded view.")
    if video_frames is not None and take.video_frames != video_frames:
        raise ValueError("Camera and video frame counts disagree")
    return take if pov else take._replace(markers=key_markers(project, take.frames, take.source != "authored"))


def key_markers(project: Project, frames: list[Frame], recorded: bool) -> tuple[Marker, ...]:
    """A marker for every keyframe in the take."""
    markers = []
    for number, key in enumerate(project.keyframes, start=1):
        if recorded:
            arrival = arrival_at(frames, float(key.time))
            if arrival is not None:
                markers.append(Marker(number, *arrival))
        elif key.source != "attach":
            arrival = arrival_at(frames, float(key.time))
            if arrival is not None:  # An early finish never reaches the later keys.
                markers.append(Marker(number, arrival[0], (key.x, key.y, key.z)))
    return tuple(markers)


def arrival_at(frames: list[Frame], phase: float):
    """Time and camera position when the shot reached a certain phase, or None if it never did."""
    from itertools import pairwise
    step = abs(frames[-1].phase - frames[-2].phase) if len(frames) > 1 else 0.0
    if frames[0].phase - step <= phase < frames[0].phase:
        return frames[0].time, frames[0].position
    for before, after in pairwise(frames):
        if before.phase <= phase <= after.phase:
            span = after.phase - before.phase
            weight = (phase - before.phase) / span if span > 0 else 0.0
            return (before.time + (after.time - before.time) * weight,
                    tuple(a + (b - a) * weight for a, b in zip(before.position, after.position)))
    if frames[-1].phase <= phase <= frames[-1].phase + step:
        return frames[-1].time, frames[-1].position
    return None


# Depth of field
CITADEL_SWITCHES = ("r_depth_of_field", "r_citadel_depthoffield_enable")
CITADEL_FOCUS = "r_citadel_depthoffield_focus_distance"
CITADEL_APERTURE = "r_citadel_depthoffield_aperture_diameter"
CITADEL_FOCUS_DEFAULT = 200.0  # Dolly's Citadel default when no focus is authored
NATIVE_RANGES = "r_dof_override_ranges"
NATIVE_DISTANCES = ("r_dof_override_near_blurry", "r_dof_override_near_crisp",
                    "r_dof_override_far_crisp", "r_dof_override_far_blurry")
NATIVE_DEFAULTS = (-100.0, 0.0, 180.0, 2000.0)  # The game's defaults
NATIVE_CRISP_BLUR = 1 / 1500
NATIVE_FULL_BLUR = 1 / 50


def native_lens(ranges, fov: float):
    """A lens approximating Native DOF's ranges: (focus distance, aperture) in inches, or None.

    A lens blurs in proportion to |1/focus - 1/distance|. With both sides, the
    focus sits between the crisp edges and both are just noticeably blurred.
    With one side, its crisp-to-blurry ramp sets the rate and the focus sits
    just inside its crisp edge.
    """
    near_blurry, near_crisp, far_crisp, far_blurry = ranges
    near = 0 < near_blurry < near_crisp
    far = 0 < far_crisp < far_blurry
    if not (near or far):
        return None
    spread = NATIVE_FULL_BLUR - NATIVE_CRISP_BLUR
    if near and far and near_crisp < far_crisp:
        inverse = (1 / near_crisp + 1 / far_crisp) / 2
        rate = NATIVE_CRISP_BLUR / (1 / near_crisp - inverse)
    elif far:
        rate = spread / (1 / far_crisp - 1 / far_blurry)
        inverse = 1 / far_crisp + NATIVE_CRISP_BLUR / rate
    else:
        rate = spread / (1 / near_blurry - 1 / near_crisp)
        inverse = 1 / near_crisp - NATIVE_CRISP_BLUR / rate
    focus = min(1e6, max(1.0, 1 / inverse if inverse > 0 else 1e6))
    # Blur as a fraction of the width is aperture / (2 * tan(hfov / 2)) * |1/focus - 1/distance|.
    return focus, rate * 2 * math.tan(math.radians(fov) / 2)


def depth_of_field(project: Project, frames: list[Frame]):
    """Depth of field for every frame, or None when no frame uses it.

    Each entry is (focus distance, aperture diameter) in inches, with aperture 0
    where the lens is off. Values come from the shot at the time each frame
    rendered. These are authored lens conversions rather than effective
    renderer readback; Native DOF ranges are approximate.
    """
    if not project.keyframes or not frames:
        return None, []

    project.validate()
    lenses, notes = [], set()
    for frame in frames:
        cvars = project._evaluate_cvars(_finite(max(0.0, frame.phase), "Evaluation time"))
        aperture = float(cvars.get(CITADEL_APERTURE, 0))  # The game's default aperture has no blur.
        if all(cvars.get(name, 0) for name in CITADEL_SWITCHES) and aperture > 0:
            if CITADEL_FOCUS not in cvars:
                notes.add(f"No Citadel focus distance is authored - exported at {CITADEL_FOCUS_DEFAULT:g} inches.")
            if cvars.get("r_citadel_depthoffield_mode", 0):
                notes.add("Citadel near-only and far-only modes are exported as normal depth of field.")
            lenses.append((max(1.0, float(cvars.get(CITADEL_FOCUS, CITADEL_FOCUS_DEFAULT))), aperture))
        elif cvars.get("r_dof_override", 0) and cvars.get("r_depth_of_field", 0):
            # Four zeros in the combined setting leave the separate distances in charge.
            ranges = tuple(float(v) for v in cvars.get(NATIVE_RANGES, (0, 0, 0, 0)))
            if not any(ranges):
                ranges = tuple(float(cvars.get(name, default))
                               for name, default in zip(NATIVE_DISTANCES, NATIVE_DEFAULTS))
            lens = native_lens(ranges, frame.fov)
            if lens:
                notes.add("Native DOF is approximated by a lens: focus and the sharp range follow it, "
                          "but its blur ramp and ground tilt don't.")
            else:
                notes.add("Native DOF has no blurry range beyond its crisp range, so it isn't exported.")
            lenses.append(lens)
        else:
            lenses.append(None)

    if not any(lenses):
        return None, sorted(notes)

    # A frame with the lens off keeps the nearest focus distance, so focus never jumps.
    focus = next(lens[0] for lens in lenses if lens)
    filled = []
    for lens in lenses:
        focus = lens[0] if lens else focus
        filled.append(lens or (focus, 0.0))
    return filled, sorted(notes)


# After Effects (.jsx)
def ae_vector(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Source vector to After Effects vector."""
    return (x, -z, y)


def ae_rotation(pitch: float, yaw: float, roll: float) -> list[tuple[float, float, float]]:
    """Source to AE camera rotation"""
    forward, right, up = angle_vectors(pitch, yaw, roll)
    columns = (ae_vector(*right), tuple(-v for v in ae_vector(*up)), ae_vector(*forward))
    matrix = [[columns[col][row] for col in range(3)] for row in range(3)]
    return euler_candidates(matrix)


def euler_candidates(matrix) -> list[tuple[float, float, float]]:
    """Returns both solutions of R = Rx(a) * Ry(b) * Rz(c), in degrees."""
    r02 = max(-1.0, min(1.0, matrix[0][2]))
    if abs(r02) >= GIMBAL: # Gimbal lock
        b = math.copysign(90.0, r02)
        a = math.degrees(math.atan2(matrix[2][1], matrix[1][1]))
        return [(a, b, 0.0)]
    b = math.asin(r02)
    a = math.atan2(-matrix[1][2], matrix[2][2])
    c = math.atan2(-matrix[0][1], matrix[0][0])
    first = (math.degrees(a), math.degrees(b), math.degrees(c))
    second = (first[0] + 180.0, 180.0 - first[1], first[2] + 180.0)
    return [first, second]


def nearest(value: float, previous: float) -> float:
    return value - 360.0 * round((value - previous) / 360.0)


def continuous(candidates, previous):
    """Pick the euler candidate closest to the previous frame."""
    if previous is None:
        return candidates[0]
    best = None
    for candidate in candidates:
        unwrapped = tuple(nearest(v, p) for v, p in zip(candidate, previous))
        distance = sum((v - p) ** 2 for v, p in zip(unwrapped, previous))
        if best is None or distance < best[0]:
            best = (distance, unwrapped)
    return best[1]


def ae_rotations(frames: list[Frame]) -> list[tuple[float, float, float]]:
    """AE x,y,z Rotation for every frame, continuous from frame to frame."""
    rotations = []
    previous = None
    for frame in frames:
        previous = continuous(ae_rotation(*frame.angles), previous)
        rotations.append(previous)
    return rotations


def build_jsx(project: Project, fps: float, *, camera_data: str | Path | None = None,
              width: int | None = None, height: int | None = None,
              speed: float = 1.0, video_frames: int | None = None,
              video: str | None = None, pov: bool = False, take: Take | None = None) -> tuple[str, list[str]]:
    """Return the JSX source and accuracy warnings for a shot.

    With the native camera available, every frame is sampled; otherwise the
    path is sampled from its keyframes. Video is the recorded file's name,
    which the script imports from its own folder.
    """
    take = take if take is not None else load_take(project, fps, camera_data, speed, video_frames, pov)
    frames = take.frames
    default_width, default_height = comp_size(project.standard_aspect)
    width, height = width or (take.size[0] if take.size else default_width), height or (take.size[1] if take.size else default_height)

    # Positions are offsets from the first frame's camera,
    origin = ae_vector(*frames[0].position)
    def offset(position):
        return tuple(value - base for value, base in zip(ae_vector(*position), origin))

    keys = [(marker.key, marker.time, offset(marker.position)) for marker in take.markers]
    lenses, dof_warnings = depth_of_field(project, frames)
    data = {
        "frame_numbers": array(str(f.frame) for f in frames),
        "positions": array(array(map(number, offset(f.position))) for f in frames),
        "rotations": array(array(map(number, r)) for r in ae_rotations(frames)),
        "fovs": array(number(f.fov) for f in frames),
        "keys": array(array([str(n), number(t), *map(number, p)]) for n, t, p in keys),
        "origin": array(map(number, origin)),
        "dof": array(array(map(number, lens)) for lens in lenses) if lenses else "null",
    }
    name = json.dumps(project.name, ensure_ascii=True)
    video_name = json.dumps(video, ensure_ascii=True) if video else "null"
    script = f"""// Dolly camera for After Effects.
// Camera source: {take.source}; timing: encoded-frame conform; projection: stable camera.
// Run with File > Scripts > Run Script File...
(function () {{
    var COMP_WIDTH = {int(width)};
    var COMP_HEIGHT = {int(height)};
    var SCALE = 1.0;
    var ADD_KEY_MARKERS = true;
    var IMPORT_VIDEO = true;

    var SHOT_NAME = {name};
    var VIDEO_FILE = {video_name};
    var SCRIPT_FOLDER = $.fileName ? new File($.fileName).parent : null;
    var FPS = {number(float(fps))};
    var FRAMES = {take.video_frames};
    var ORIGIN = {data["origin"]};
    var frameNumbers = {data["frame_numbers"]};
    var positions = {data["positions"]};
    var rotations = {data["rotations"]};
    var fovs = {data["fovs"]};
    var dollyKeys = {data["keys"]};
    var dof = {data["dof"]}; // null, or per frame [focus distance, aperture]; aperture 0 is off

    function importVideo() {{
        var file = SCRIPT_FOLDER ? new File(SCRIPT_FOLDER.fsName + "/" + VIDEO_FILE) : null;
        if (!file || !file.exists) {{
            file = File.openDialog("Locate " + VIDEO_FILE + " for " + SHOT_NAME);
            if (!file) return null;
        }}
        var item = null;
        for (var n = 1; n <= app.project.numItems && !item; n++) {{
            var candidate = app.project.item(n);
            if (candidate instanceof FootageItem && candidate.file && candidate.file.fsName === file.fsName)
                item = candidate;
        }}
        item = item || app.project.importFile(new ImportOptions(file));
        // One video frame per comp frame, so each frame meets its camera key.
        item.mainSource.conformFrameRate = FPS;
        return item;
    }}

    app.beginUndoGroup("Import Dolly camera");
    try {{
        if (!app.project) app.newProject();
        var footage = null, footageError = null;
        if (IMPORT_VIDEO && VIDEO_FILE) {{
            try {{ footage = importVideo(); }}
            catch (error) {{ footageError = error.toString(); }}
        }}
        if (footage) {{
            COMP_WIDTH = footage.width;
            COMP_HEIGHT = footage.height;
        }}
        var comp = app.project.items.addComp(SHOT_NAME + " (Dolly Camera)", COMP_WIDTH, COMP_HEIGHT,
                                             1, FRAMES / FPS, FPS);
        // Camera, markers, and video layers
        if (footage) comp.layers.add(footage);
        if (ADD_KEY_MARKERS) {{
            for (var k = 0; k < dollyKeys.length; k++) {{
                var key = dollyKeys[k]; // [key number, video seconds, x, y, z]
                var marker = comp.layers.addNull();
                marker.name = "Dolly key " + key[0] + " @ " + key[1].toFixed(3) + "s";
                marker.threeDLayer = true;
                marker.guideLayer = true;
                marker.property("ADBE Transform Group").property("ADBE Position")
                    .setValue([key[2] * SCALE, key[3] * SCALE, key[4] * SCALE]);
            }}
        }}
        var camera = comp.layers.addCamera("Dolly camera", [COMP_WIDTH / 2, COMP_HEIGHT / 2]);
        camera.autoOrient = AutoOrientType.NO_AUTO_ORIENT;
        var times = [], scaled = [], xs = [], ys = [], zs = [], zooms = [];
        for (var i = 0; i < positions.length; i++) {{
            var p = positions[i], r = rotations[i];
            times.push(frameNumbers[i] / FPS);
            scaled.push([p[0] * SCALE, p[1] * SCALE, p[2] * SCALE]);
            xs.push(r[0]); ys.push(r[1]); zs.push(r[2]);
            zooms.push((COMP_WIDTH / 2) / Math.tan(fovs[i] * Math.PI / 360));
        }}
        var transform = camera.property("ADBE Transform Group");
        transform.property("ADBE Position").setValuesAtTimes(times, scaled);
        transform.property("ADBE Orientation").setValue([0, 0, 0]);
        transform.property("ADBE Rotate X").setValuesAtTimes(times, xs);
        transform.property("ADBE Rotate Y").setValuesAtTimes(times, ys);
        transform.property("ADBE Rotate Z").setValuesAtTimes(times, zs);
        var options = camera.property("ADBE Camera Options Group");
        options.property("ADBE Camera Zoom").setValuesAtTimes(times, zooms);
        // Always set it: some renderers turn depth of field on for a new camera.
        options.property("ADBE Camera Depth of Field").setValue(dof ? 1 : 0);
        if (dof) {{
            var focuses = [], apertures = [];
            for (var d = 0; d < dof.length; d++) {{
                focuses.push(dof[d][0] * SCALE);
                apertures.push(dof[d][1] * SCALE);
            }}
            options.property("ADBE Camera Blur Level").setValue(100);
            options.property("ADBE Camera Focus Distance").setValuesAtTimes(times, focuses);
            options.property("ADBE Camera Aperture").setValuesAtTimes(times, apertures);
        }}
        comp.openInViewer();
        if (footageError) {{
            var message = "Dolly camera imported, but After Effects could not import " + VIDEO_FILE + ".\\n\\n" +
                "Convert the video to an After Effects-supported format, keeping its dimensions, frame count and " +
                FPS + " FPS, then add it to this composition starting at frame 0.\\n\\n" + footageError;
            comp.comment = message;
            alert(message);
        }}
    }} catch (error) {{
        alert("Dolly camera import failed: " + error.toString());
    }} finally {{
        app.endUndoGroup();
    }}
}})();
"""
    return script, take.warnings + dof_warnings


def export_jsx(project: Project, path: str | Path, fps: float, **options) -> list[str]:
    """Write the script"""
    script, warnings = build_jsx(project, fps, **options)
    write_script(path, script)
    return warnings


# USD (.usda)
USD_APERTURE = 36.0 # horizontal aperture
USD_METERS_PER_UNIT = 0.0254 # one unit is about an inch


def usd_rotation(pitch: float, yaw: float, roll: float) -> list[tuple[float, float, float]]:
    """Both rotatation solutions (R = Rz * Ry * Rx, degrees)"""
    forward, right, up = angle_vectors(pitch, yaw, roll)
    columns = (right, up, tuple(-v for v in forward)) # USD cameras look down -Z
    m = [[columns[col][row] for col in range(3)] for row in range(3)]
    r20 = max(-1.0, min(1.0, m[2][0]))
    if abs(r20) >= GIMBAL: # Gimbal lock
        b = -math.copysign(90.0, r20)
        a = math.degrees(math.atan2(-m[1][2], m[1][1]))
        return [(a, b, 0.0)]
    b = -math.asin(r20)
    a = math.atan2(m[2][1], m[2][2])
    c = math.atan2(m[1][0], m[0][0])
    first = (math.degrees(a), math.degrees(b), math.degrees(c))
    second = (first[0] + 180.0, 180.0 - first[1], first[2] + 180.0)
    return [first, second]


def usd_rotations(frames: list[Frame]) -> list[tuple[float, float, float]]:
    """Applies USD rotation conversion for eahc frame"""
    rotations = []
    previous = None
    for frame in frames:
        previous = continuous(usd_rotation(*frame.angles), previous)
        rotations.append(previous)
    return rotations


def usd_string(text: str) -> str:
    # Keep valid Unicode and replace control characters and lone surrogates.
    safe = "".join(c if c >= " " and not "\x7f" <= c <= "\x9f"
                   and not "\ud800" <= c <= "\udfff" else "?" for c in text)
    return '"' + safe.replace("\\", "\\\\").replace('"', '\\"') + '"'


def usd_tuple(values) -> str:
    return "(" + ", ".join(map(number, values)) + ")"


def usd_samples(frames: list[Frame], values) -> str:
    """Samples keyed by video frame."""
    rows = "".join(f"            {f.frame}: {v},\n" for f, v in zip(frames, values))
    return "{\n" + rows + "        }"


def build_usda(project: Project, fps: float, *, camera_data: str | Path | None = None,
               width: int | None = None, height: int | None = None,
               speed: float = 1.0, video_frames: int | None = None,
               pov: bool = False, take: Take | None = None) -> tuple[str, list[str]]:
    """Return the USD stage and accuracy warnings for a shot."""
    take = take if take is not None else load_take(project, fps, camera_data, speed, video_frames, pov)
    frames = take.frames
    default_width, default_height = comp_size(project.standard_aspect)
    width, height = width or (take.size[0] if take.size else default_width), height or (take.size[1] if take.size else default_height)
    vertical_aperture = USD_APERTURE * height / width

    # Positions are offsets from the first frame's camera.
    origin = frames[0].position
    def offset(position):
        return tuple(value - base for value, base in zip(position, origin))

    translations = (usd_tuple(offset(f.position)) for f in frames)
    rotations = (usd_tuple(r) for r in usd_rotations(frames))
    focals = [USD_APERTURE / 2 / math.tan(math.radians(f.fov) / 2) for f in frames]
    lenses, dof_warnings = depth_of_field(project, frames)
    dof = ""
    if lenses:
        # focalLength is in tenths of a unit, so fStop = focal / 10 / aperture.
        # fStop 0 turns depth of field off.
        stops = (number(focal / 10 / aperture) if aperture else "0"
                 for focal, (_focus, aperture) in zip(focals, lenses))
        dof = (f"        float focusDistance.timeSamples = {usd_samples(frames, (number(focus) for focus, _ in lenses))}\n"
               f"        float fStop.timeSamples = {usd_samples(frames, stops)}\n")
    keys = "".join(f'''
        def Xform "Key_{marker.key:02d}" (
            doc = "Dolly key {marker.key} @ {marker.time:.3f}s"
        )
        {{
            double3 xformOp:translate = {usd_tuple(offset(marker.position))}
            uniform token[] xformOpOrder = ["xformOp:translate"]
        }}
''' for marker in take.markers)
    transform = (f"        double3 xformOp:translate.timeSamples = {usd_samples(frames, translations)}\n"
                 f"        float3 xformOp:rotateXYZ.timeSamples = {usd_samples(frames, rotations)}\n"
                 '        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]')
    if all(frame.basis is not None for frame in frames):
        def matrix(frame):
            basis = frame.basis
            rows = [(*basis[i:i+3], 0) for i in (0, 3, 6)] + [(*offset(frame.position), 1)]
            return "(" + ", ".join(usd_tuple(row) for row in rows) + ")"
        transform = (f"        matrix4d xformOp:transform.timeSamples = {usd_samples(frames, map(matrix, frames))}\n"
                     '        uniform token[] xformOpOrder = ["xformOp:transform"]')
    layer = f"""#usda 1.0
(
    defaultPrim = "Dolly"
    doc = "Deadlock Dolly Camera"
    customLayerData = {{
        string shot = {usd_string(project.name)}
        string cameraSource = {usd_string(take.source)}
        string timing = "encoded-frame-conform"
        string projectionPolicy = "stable-camera"
        double3 origin = {usd_tuple(origin)}
    }}
    metersPerUnit = {number(USD_METERS_PER_UNIT)}
    upAxis = "Z"
    framesPerSecond = {number(float(fps))}
    timeCodesPerSecond = {number(float(fps))}
    startTimeCode = 0
    endTimeCode = {max(0, take.video_frames - 1)}
)

def Xform "Dolly"
{{
    def Camera "Camera"
    {{
        token projection = "perspective"
        float2 clippingRange = (1, 1000000)
        float horizontalAperture = {number(USD_APERTURE)}
        float verticalAperture = {number(vertical_aperture)}
        float focalLength.timeSamples = {usd_samples(frames, map(number, focals))}
{dof}{transform}
    }}

    def Xform "Keys"
    {{{keys}    }}
}}
"""
    return layer, take.warnings + dof_warnings


def export_usda(project: Project, path: str | Path, fps: float, **options) -> list[str]:
    """Write the USD stage"""
    stage, warnings = build_usda(project, fps, **options)
    write_script(path, stage)
    return warnings


# Helpers
def write_script(path: str | Path, script: str) -> None:
    import os
    import tempfile

    destination = Path(path)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(script.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "nt":
            os.rename(temporary, destination)  # Windows refuses an existing destination.
        else:
            os.link(temporary, destination)  # Same-directory atomic no-replace publication.
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def number(value: float) -> str:
    """Rounding numbers to 6 decimals"""
    return format(round(value, 6), ".10g")


def array(values) -> str:
    return "[" + ",".join(values) + "]"
