"""Build RenderLock shot collections from Deadlock Dolly exports.

This is the producer-side adapter for ``agr.shot_package``. It reads Dolly's
saved project, take sidecar, recorded camera track and paired-depth camera
bundle, then embeds one normalized camera track per shot. Large media files stay
external and are referenced by the collection.

Dolly camera data is recorded in Source game units (1 unit = 0.0254 m) with
X forward, Y left and Z up. The collection stores that source space explicitly;
RenderLock applies the unit scale during scene import.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from . import __version__
from .shot_package import (
    FORMAT_NAME, VERSION, ShotCollectionError, collection_summary,
    read_shot_collection, write_shot_collection,
)

MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_SIDECAR_BYTES = 8 * 1024 * 1024
MAX_CAMERA_BYTES = 128 * 1024 * 1024
MAX_CAMERA_FRAMES = 262_144
TRACK_FIELDS_V1 = [
    "frame", "video_time", "phase", "x", "y", "z", "pitch", "yaw", "roll",
    "horizontal_fov",
]
TRACK_FIELDS_V2 = TRACK_FIELDS_V1 + ["capture_time"]
TAKE_STATUSES = ("deadlock-dolly-shot",)
PROJECT_NAME = "deadlock-dolly"


class DollyShotError(ValueError):
    """The Dolly input is missing, inconsistent or unsupported."""


def _fail(message: str) -> None:
    raise DollyShotError(message)


def _read_json(path: Path, maximum: int) -> dict:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DollyShotError(f"Cannot read {path}: {exc}") from exc
    if not path.is_file() or size <= 0 or size > maximum:
        raise DollyShotError(f"{path.name}: file must be non-empty and at most {maximum} bytes")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DollyShotError(f"Cannot read {path}: {exc}") from exc
    if len(raw) > maximum:
        raise DollyShotError(f"{path.name}: file exceeds the {maximum} byte limit")

    def reject_constant(value):
        raise DollyShotError(f"{path.name}: non-finite JSON number: {value}")

    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise DollyShotError(f"{path.name}: duplicate JSON field: {name}")
            result[name] = value
        return result

    try:
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant,
                          object_pairs_hook=unique_object)
    except DollyShotError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DollyShotError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DollyShotError(f"{path.name}: top level must be an object")
    return data


def _number(value, label: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    if minimum is not None and number < minimum:
        _fail(f"{label} must be at least {minimum:g}")
    if maximum is not None and number > maximum:
        _fail(f"{label} must be at most {maximum:g}")
    return number


def _integer(value, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        _fail(f"{label} must be at least {minimum}")
    return value


def _vector(value, length: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        _fail(f"{label} must contain exactly {length} numbers")
    return tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(value))


def _normalise(vector: tuple[float, ...], label: str) -> tuple[float, ...]:
    length = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(length) or length <= 1e-12:
        _fail(f"{label} has zero length")
    return tuple(value / length for value in vector)


def _cross(left, right):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def angle_vectors(pitch: float, yaw: float, roll: float):
    """Forward, right and up, matching Dolly's camera convention."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    forward = (cp * cy, cp * sy, -sp)
    right = (-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp)
    up = (cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp)
    return forward, right, up


def read_dolly_project(path) -> dict:
    """Read the small identity/time fields from a Dolly project."""
    path = Path(path)
    data = _read_json(path, MAX_PROJECT_BYTES)
    if data.get("format") != PROJECT_NAME:
        _fail("This is not a Deadlock Dolly project")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 13:
        _fail(f"Unsupported Dolly project version: {version!r}")
    name = data.get("name", "Untitled shot")
    if not isinstance(name, str) or not name:
        _fail("Dolly project name is invalid")
    return {
        "path": path,
        "version": version,
        "name": name,
        "start_tick": _integer(data.get("start_tick", 0), "Dolly project start_tick", minimum=0),
        "tick_rate": _number(data.get("tick_rate", 64.0), "Dolly project tick_rate",
                             minimum=0.000_001),
    }


def read_dolly_take(path) -> dict:
    """Read Dolly's take sidecar (``deadlock-dolly-shot`` version 1)."""
    path = Path(path)
    data = _read_json(path, MAX_SIDECAR_BYTES)
    if data.get("format") not in TAKE_STATUSES or data.get("version") != 1:
        _fail("Not a supported Dolly take sidecar")
    video = data.get("video_file")
    if not isinstance(video, str) or not video:
        _fail("Dolly take sidecar has no video_file")
    fps = _number(data.get("fps"), "Dolly take fps", minimum=0.001, maximum=1_000)
    frames = _integer(data.get("frames_written"), "Dolly take frames_written", minimum=1)
    if frames > MAX_CAMERA_FRAMES:
        _fail("Dolly take exceeds the camera frame limit")
    fixed_step = data.get("fixed_step")
    if not isinstance(fixed_step, bool):
        _fail("Dolly take fixed_step must be true or false")
    first_frame = _integer(data.get("first_frame", 0), "Dolly take first_frame", minimum=0)
    last_frame = _integer(data.get("last_frame", frames - 1),
                          "Dolly take last_frame", minimum=0)
    if first_frame != 0 or last_frame != frames - 1:
        _fail("Dolly take frame range does not match frames_written")
    first_replay = data.get("first_replay_time")
    last_replay = data.get("last_replay_time")
    if (first_replay is None) != (last_replay is None):
        _fail("Dolly take must provide both first_replay_time and last_replay_time")
    if first_replay is not None:
        first_replay = _number(first_replay, "Dolly take first_replay_time", minimum=0)
        last_replay = _number(last_replay, "Dolly take last_replay_time", minimum=0)
        if last_replay < first_replay:
            _fail("Dolly take replay range is reversed")
    return {
        "path": path,
        "version": 1,
        "video_file": video,
        "fps": fps,
        "frames": frames,
        "fixed_step": fixed_step,
        "first_replay_time": first_replay,
        "last_replay_time": last_replay,
    }


def read_dolly_camera_track(path, *, width: int | None = None,
                            height: int | None = None) -> dict:
    """Read a recorded Dolly camera track (version 1 or 2)."""
    path = Path(path)
    data = _read_json(path, MAX_CAMERA_BYTES)
    if data.get("format") != "deadlock-dolly-camera-track":
        _fail("Not a Dolly camera track")
    version = data.get("version")
    if version not in (1, 2) or isinstance(version, bool):
        _fail(f"Unsupported Dolly camera track version: {version!r}")
    fields = TRACK_FIELDS_V2 if version == 2 else TRACK_FIELDS_V1
    if data.get("fields") != fields:
        _fail("Dolly camera track fields do not match its version")
    fps = _number(data.get("fps"), "Camera track fps", minimum=0.001, maximum=1_000)
    fixed_step = data.get("fixed_step")
    if not isinstance(fixed_step, bool):
        _fail("Camera track fixed_step must be true or false")
    frames = _integer(data.get("frames_written"), "Camera track frames_written", minimum=1)
    if frames > MAX_CAMERA_FRAMES:
        _fail("Camera track exceeds the camera frame limit")
    if version == 2:
        if data.get("complete") is not True:
            _fail("Incomplete Dolly camera track cannot be imported")
        if data.get("source") != "native-view-hook":
            _fail("Dolly camera track has an unsupported source")
        track_width = _integer(data.get("width"), "Camera track width", minimum=1)
        track_height = _integer(data.get("height"), "Camera track height", minimum=1)
        if width is None:
            width = track_width
        elif width != track_width:
            _fail("Camera track width does not match the take")
        if height is None:
            height = track_height
        elif height != track_height:
            _fail("Camera track height does not match the take")
    if width is None or height is None:
        _fail("Camera track version 1 needs the output width and height")
    rows = data.get("frames")
    if not isinstance(rows, list) or len(rows) != frames:
        _fail("Camera track frames do not match frames_written")
    samples = []
    previous_video = -1.0
    previous_replay = -1.0
    for index, row in enumerate(rows):
        label = f"Camera track frame {index}"
        if not isinstance(row, list) or len(row) != len(fields):
            _fail(f"{label} has the wrong field count")
        values = [_number(value, f"{label} field {position}")
                  for position, value in enumerate(row)]
        frame, video_time, phase, x, y, z, pitch, yaw, roll, fov = values[:10]
        if frame != index:
            _fail(f"{label} is not in encoded-frame order")
        if video_time < 0 or video_time < previous_video:
            _fail(f"{label} has an invalid video time")
        if phase < 0 or phase < previous_replay:
            _fail(f"{label} has an invalid replay time")
        if not 1 < fov < 179:
            _fail(f"{label} has an invalid horizontal FOV")
        previous_video = video_time
        previous_replay = phase
        forward, right, up = angle_vectors(pitch, yaw, roll)
        sample = {
            "frame": index,
            "video_time": video_time,
            "replay_time": phase,
            "position": [x, y, z],
            "right": list(right),
            "up": list(up),
            "forward": list(forward),
            "horizontal_fov": fov,
            "aspect": width / height,
            "state": "complete",
        }
        if version == 2:
            capture_time = values[10]
            if capture_time < 0:
                _fail(f"{label} has an invalid capture time")
            sample["capture_time"] = capture_time
        samples.append(sample)
    return {
        "path": path,
        "version": version,
        "fps": fps,
        "frames": frames,
        "width": width,
        "height": height,
        "samples": samples,
        "fixed_step": fixed_step,
    }


def read_dolly_camera_bundle(path, *, fps: float) -> dict:
    """Read a paired-depth camera bundle and adapt its complete records."""
    path = Path(path)
    data = _read_json(path, MAX_CAMERA_BYTES)
    if data.get("format") != "deadlock-dolly-camera" or data.get("version") != 1:
        _fail("Not a supported Dolly camera bundle")
    if data.get("complete") is not True:
        _fail("Incomplete Dolly camera bundle cannot be imported")
    frames = _integer(data.get("frames"), "Camera bundle frames", minimum=1)
    if frames > MAX_CAMERA_FRAMES:
        _fail("Camera bundle exceeds the camera frame limit")
    width = _integer(data.get("color_width"), "Camera bundle color_width", minimum=1)
    height = _integer(data.get("color_height"), "Camera bundle color_height", minimum=1)
    records = data.get("records")
    if not isinstance(records, list) or len(records) != frames:
        _fail("Camera bundle records do not match frames")
    samples = []
    previous_replay = -1.0
    for index, record in enumerate(records):
        label = f"Camera bundle record {index}"
        if not isinstance(record, dict):
            _fail(f"{label} is not an object")
        if record.get("frame") != index:
            _fail(f"{label} is not in encoded-frame order")
        if record.get("state") != "complete":
            _fail(f"{label} does not carry a complete view")
        replay_time = _number(record.get("replay_time"), f"{label} replay_time", minimum=0)
        if replay_time < previous_replay:
            _fail(f"{label} replay_time decreases")
        previous_replay = replay_time
        position = _vector(record.get("position"), 3, f"{label} position")
        up = _normalise(_vector(record.get("up"), 3, f"{label} up"), f"{label} up")
        forward = _normalise(_vector(record.get("forward"), 3, f"{label} forward"),
                             f"{label} forward")
        view = _vector(record.get("view"), 16, f"{label} view")
        right = _normalise((view[0], view[4], view[8]), f"{label} right")
        expected = _cross(forward, up)
        if max(abs(a - b) for a, b in zip(right, expected)) > 2e-3:
            _fail(f"{label} view does not agree with forward x up")
        projection = _vector(record.get("projection"), 16, f"{label} projection")
        if projection[0] == 0 or projection[14] == 0:
            _fail(f"{label} projection cannot provide a horizontal FOV")
        fov = math.degrees(2 * math.atan(abs(projection[14] / projection[0])))
        if not 1 < fov < 179:
            _fail(f"{label} has an invalid horizontal FOV")
        capture = _integer(record.get("capture_pts_100ns"),
                           f"{label} capture_pts_100ns", minimum=0)
        sample = {
            "frame": index,
            "video_time": index / fps,
            "replay_time": replay_time,
            "position": list(position),
            "right": list(right),
            "up": list(up),
            "forward": list(forward),
            "horizontal_fov": fov,
            "aspect": width / height,
            "projection": list(projection),
            "capture_time": capture / 1e7,
            "sample": _integer(record.get("sample", index), f"{label} sample", minimum=0),
            "state": "complete",
        }
        if "inverse_z" in record:
            sample["inverse_z"] = list(_vector(record["inverse_z"], 4, f"{label} inverse_z"))
        if "viewport" in record:
            viewport = _vector(record["viewport"], 2, f"{label} viewport")
            if not 0.0 <= viewport[0] < viewport[1] <= 1.0:
                _fail(f"{label} has an invalid viewport")
            sample["viewport"] = list(viewport)
        if "near" in record:
            sample["near"] = _number(record["near"], f"{label} near", minimum=0)
        if "far" in record:
            if record["far"] is not None:
                far = _number(record["far"], f"{label} far", minimum=0)
                if "near" in sample and far <= sample["near"]:
                    _fail(f"{label} far plane must be greater than near")
                sample["far"] = far
            else:
                sample["far"] = None
        samples.append(sample)
    return {
        "path": path,
        "version": 1,
        "fps": fps,
        "frames": frames,
        "width": width,
        "height": height,
        "samples": samples,
    }


def _compare_camera_sources(track: dict, bundle: dict) -> None:
    if track["frames"] != bundle["frames"]:
        _fail("Recorded camera sources disagree on frame count")
    for left, right in zip(track["samples"], bundle["samples"]):
        if abs(left["replay_time"] - right["replay_time"]) > 2e-3:
            _fail(f"Camera sources disagree on replay time at frame {left['frame']}")
        if max(abs(a - b) for a, b in zip(left["position"], right["position"])) > 0.1:
            _fail(f"Camera sources disagree on position at frame {left['frame']}")
        for name in ("right", "up", "forward"):
            if max(abs(a - b) for a, b in zip(left[name], right[name])) > 2e-3:
                _fail(f"Camera sources disagree on {name} at frame {left['frame']}")
        if abs(left["horizontal_fov"] - right["horizontal_fov"]) > 0.02:
            _fail(f"Camera sources disagree on FOV at frame {left['frame']}")
        if ("capture_time" in left and "capture_time" in right
                and abs(left["capture_time"] - right["capture_time"]) > 2e-3):
            _fail(f"Camera sources disagree on capture time at frame {left['frame']}")


def _locate_inputs(take) -> dict:
    path = Path(take)
    if path.is_dir():
        folder = path
        tracks = sorted(folder.glob("*.camera-track.json"))
        if len(tracks) > 1:
            _fail("Take folder has more than one camera track; pass the exact track file")
        sidecars = sorted(folder.glob("*.shot.json"))
        sidecar = folder / "shot.json" if (folder / "shot.json").is_file() else None
        if sidecar is None and len(sidecars) > 1:
            _fail("Take folder has more than one shot sidecar; pass the exact sidecar file")
        if sidecar is None and len(sidecars) == 1:
            sidecar = sidecars[0]
        return {
            "folder": folder,
            "sidecar": sidecar,
            "track": tracks[0] if tracks else None,
            "bundle": folder / "depth" / "camera.json" if (folder / "depth" / "camera.json").is_file() else None,
        }
    if not path.is_file():
        _fail(f"Dolly take is missing: {path}")
    name = path.name
    if name.endswith(".camera-track.json"):
        folder = path.parent
        video_name = name[:-len(".camera-track.json")]
        sidecar = folder / (video_name + ".shot.json")
        if not sidecar.is_file():
            sidecar = folder / "shot.json"
        return {
            "folder": folder,
            "sidecar": sidecar if sidecar.is_file() else None,
            "track": path,
            "bundle": (folder / "depth" / "camera.json"
                       if (folder / "depth" / "camera.json").is_file() else None),
        }
    if name == "shot.json" or name.endswith(".shot.json"):
        folder = path.parent
        return {
            "folder": folder,
            "sidecar": path,
            "track": None,
            "bundle": (folder / "depth" / "camera.json"
                       if (folder / "depth" / "camera.json").is_file() else None),
        }
    if name == "camera.json":
        folder = path.parent.parent if path.parent.name == "depth" else path.parent
        return {
            "folder": folder,
            "sidecar": (folder / "shot.json" if (folder / "shot.json").is_file() else None),
            "track": None,
            "bundle": path,
        }
    _fail("Pass a Dolly take folder, shot.json, *.camera-track.json or depth/camera.json")


def _video_candidates(folder: Path) -> list[Path]:
    """Prefer encoded camera masters over DCC intermediates when no sidecar exists."""
    priority = {".mkv": 0, ".mp4": 1, ".mov": 2}
    candidates = [item for item in folder.iterdir()
                  if item.is_file() and item.suffix.lower() in priority
                  and "_ae_prores" not in item.stem.lower()
                  and not item.stem.lower().startswith("verify")]
    return sorted(candidates, key=lambda item: (priority[item.suffix.lower()], item.name.lower()))


def _find_video(folder: Path, sidecar: dict | None) -> Path | None:
    if sidecar:
        candidate = folder / sidecar["video_file"]
        if candidate.is_file():
            return candidate
    candidates = _video_candidates(folder)
    return candidates[0] if candidates else None


def _file_ref(path: Path) -> dict:
    return {"path": str(path), "size_bytes": path.stat().st_size}


def _pick_layer(folder: Path, name: str) -> Path | None:
    directory = folder / name
    if not directory.is_dir():
        return None
    candidates = _video_candidates(directory)
    return candidates[0] if candidates else None


def _slug(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._").lower()
    if not value or not value[0].isalnum():
        value = "shot-" + value
    value = value[:64].rstrip("-._")
    return value or "shot"


def build_shot(*, take, project=None, start_tick=None, tick_rate=None,
               width=None, height=None, fps=None, name=None, ident=None,
               notes=None) -> dict:
    """Build one shot entry from a Dolly take and optional saved project."""
    inputs = _locate_inputs(take)
    folder = inputs["folder"]
    sidecar = read_dolly_take(inputs["sidecar"]) if inputs["sidecar"] else None
    track_path = inputs["track"]
    bundle_path = inputs["bundle"]
    video = _find_video(folder, sidecar)
    if track_path is None and video is not None:
        candidate = folder / (video.name + ".camera-track.json")
        if candidate.is_file():
            track_path = candidate
    project_data = read_dolly_project(project) if project else None

    take_fps = sidecar["fps"] if sidecar else None
    if fps is not None:
        fps = _number(fps, "Output FPS", minimum=0.001, maximum=1_000)
        if take_fps is not None and abs(fps - take_fps) > 1e-6:
            _fail("Output FPS disagrees with the Dolly take")
    elif take_fps is not None:
        fps = take_fps

    track_data = None
    if track_path is not None:
        track_data = read_dolly_camera_track(track_path, width=width, height=height)
        if fps is None:
            fps = track_data["fps"]
        elif abs(fps - track_data["fps"]) > 1e-6:
            _fail("Output FPS disagrees with the camera track")
        if width is None:
            width = track_data["width"]
        if height is None:
            height = track_data["height"]

    if bundle_path is not None:
        if fps is None:
            _fail("Paired-depth camera needs the take FPS or --fps")
        bundle_data = read_dolly_camera_bundle(bundle_path, fps=fps)
        if width is None:
            width = bundle_data["width"]
        if height is None:
            height = bundle_data["height"]
        if width != bundle_data["width"] or height != bundle_data["height"]:
            _fail("Camera bundle dimensions disagree with the requested output")
        if track_data is not None:
            _compare_camera_sources(track_data, bundle_data)
        camera = bundle_data
        source_kind = "dolly-camera-bundle"
        source_version = 1
        source_path = bundle_path
    elif track_data is not None:
        camera = track_data
        source_kind = "dolly-camera-track"
        source_version = track_data["version"]
        source_path = track_path
    else:
        _fail("No recorded camera found. Enable Camera track or paired depth in Dolly.")

    if width is None or height is None:
        _fail("Output width and height are required for this take")
    if fps is None:
        _fail("Output FPS is required for this take")

    frames = camera["frames"]
    if sidecar and sidecar["frames"] != frames:
        _fail("Dolly take and camera source frame counts disagree")

    if project_data:
        if start_tick is not None and start_tick != project_data["start_tick"]:
            _fail("Start tick disagrees with the Dolly project")
        if tick_rate is not None and abs(tick_rate - project_data["tick_rate"]) > 1e-9:
            _fail("Tick rate disagrees with the Dolly project")
        start_tick = project_data["start_tick"]
        tick_rate = project_data["tick_rate"]
        if name is None:
            name = project_data["name"]
    if start_tick is None:
        _fail("Start tick is required when no Dolly project is supplied")
    if tick_rate is None:
        _fail("Tick rate is required when no Dolly project is supplied")
    start_tick = _integer(start_tick, "Start tick", minimum=0)
    tick_rate = _number(tick_rate, "Tick rate", minimum=0.000_001)
    if name is None:
        name = video.stem if video is not None else folder.name
    if ident is None:
        ident = _slug(name)

    samples = camera["samples"]
    first = samples[0]["replay_time"]
    if first > 1e-6:
        _fail("Camera does not start at shot-relative time 0")
    last = samples[-1]["replay_time"]
    if sidecar and sidecar["last_replay_time"] is not None:
        if abs(sidecar["last_replay_time"] - last) > 5e-3:
            _fail("Dolly take and camera source replay ranges disagree")
    end_tick_exact = start_tick + last * tick_rate
    end_tick = math.floor(end_tick_exact + 1e-6)

    media = {}
    if video is not None:
        media["color"] = _file_ref(video)
    if sidecar:
        media["take"] = _file_ref(sidecar["path"])
    if project_data:
        media["project"] = _file_ref(project_data["path"])
    if bundle_path is not None:
        depth = {"camera_bundle": _file_ref(bundle_path)}
        manifest = folder / "depth" / "manifest.json"
        if manifest.is_file():
            depth["manifest"] = _file_ref(manifest)
        master = next((path for path in (folder / "depth" / "depth.mov",
                                          folder / "depth" / "depth.mkv")
                       if path.is_file()), None)
        if master is not None:
            depth["master"] = _file_ref(master)
        exr = folder / "depth" / "exr"
        if exr.is_dir():
            depth["exr_directory"] = {"path": str(exr)}
        media["depth"] = depth
    layers = {}
    for layer in ("world", "players", "effects"):
        chosen = _pick_layer(folder, layer)
        if chosen is not None:
            layers[layer] = _file_ref(chosen)
    if layers:
        media["layers"] = layers
    usd = sorted(folder.glob("*_camera.usda"))
    if usd:
        media["usd"] = _file_ref(usd[0])
    audio = sorted(folder.glob("*.wav"))
    if audio:
        media["audio"] = _file_ref(audio[0])

    shot_notes = list(notes or [])
    if not sidecar:
        shot_notes.append("No Dolly take sidecar was found; camera timing came from the camera source.")
    if not video:
        shot_notes.append("No colour video was found beside the take.")
    if track_data is not None and bundle_path is not None:
        shot_notes.append("Camera track and paired-depth bundle agreed; the bundle supplied the embedded samples.")
    if camera["samples"][0].get("projection") is not None:
        shot_notes.append("Projection matrices are embedded for exact depth reprojection; the stable camera uses position, basis and horizontal FOV.")

    return {
        "id": ident,
        "name": name,
        "range": {
            "start_tick": start_tick,
            "end_tick": end_tick,
            "end_tick_exact": end_tick_exact,
        },
        "camera": {
            "source": {
                "kind": source_kind,
                "version": source_version,
                "path": str(source_path),
                "size_bytes": source_path.stat().st_size,
            },
            "space": "source-world",
            "unit_scale": 0.0254,
            "time_origin": "shot-start",
            "fps": fps,
            "width": width,
            "height": height,
            "frame_count": frames,
            "samples": samples,
            "projection_convention": "row-major-column-vector",
        },
        "media": media,
        "notes": shot_notes,
    }


def build_collection(*, take, replay, project=None, start_tick=None, tick_rate=None,
                     width=None, height=None, fps=None, name=None, ident=None,
                     match_id=None, replay_sha256=None, build=None, map_name=None,
                     scene=None, notes=None) -> dict:
    """Build a one-shot collection from a Dolly take."""
    if not Path(replay).is_file():
        _fail(f"Replay is missing: {replay}")
    if scene is not None and not Path(scene).is_file():
        _fail(f"Scene manifest is missing: {scene}")
    shot = build_shot(
        take=take, project=project, start_tick=start_tick,
        tick_rate=tick_rate, width=width, height=height, fps=fps, name=name,
        ident=ident, notes=notes,
    )
    if tick_rate is None:
        if project is None:
            _fail("Tick rate is required when no Dolly project is supplied")
        tick_rate = read_dolly_project(project)["tick_rate"]
    replay_data = {"path": str(Path(replay)), "tick_rate": float(tick_rate)}
    if match_id is not None:
        replay_data["match_id"] = str(match_id)
    if replay_sha256 is not None:
        replay_data["sha256"] = replay_sha256
    if build is not None:
        replay_data["build"] = int(build)
    if map_name is not None:
        replay_data["map"] = str(map_name)
    collection = {
        "format": FORMAT_NAME,
        "version": VERSION,
        "created_with": {"name": "RenderLock", "version": __version__},
        "replay": replay_data,
        "shots": [shot],
    }
    if scene is not None:
        collection["scene"] = _file_ref(Path(scene))
    if notes:
        collection["notes"] = list(notes)
    return collection


def same_replay(left: dict, right: dict) -> bool:
    """Identity comparison used when adding a shot to an existing collection."""
    if left.get("sha256") and right.get("sha256"):
        return left["sha256"] == right["sha256"]
    if left.get("match_id") and right.get("match_id"):
        return left["match_id"] == right["match_id"]
    left_path = os.path.normcase(str(Path(left["path"]).resolve()))
    right_path = os.path.normcase(str(Path(right["path"]).resolve()))
    return left_path == right_path


def add_shot(collection_path, shot: dict, *, replay: dict | None = None) -> dict:
    """Append one shot to an existing collection and return the new document."""
    path = Path(collection_path)
    collection = read_shot_collection(path)
    if replay is not None and not same_replay(collection["replay"], replay):
        _fail("Shot replay identity does not match the collection")
    derived = _derived_tick_rate(shot)
    if derived is not None and abs(derived - collection["replay"]["tick_rate"]) > 1e-6:
        _fail("Shot tick rate does not match the collection")
    identities = {item["id"] for item in collection["shots"]}
    if shot["id"] in identities:
        _fail(f"Shot id already exists: {shot['id']}")
    collection["shots"].append(shot)
    write_shot_collection(collection, path)
    return collection


def _add_common(parser: argparse.ArgumentParser, *, need_output: bool,
                identity: bool) -> None:
    parser.add_argument("--take", required=True,
                        help="Dolly take folder, shot.json, camera-track.json or depth/camera.json")
    parser.add_argument("--project", help="Saved Deadlock Dolly project JSON")
    parser.add_argument("--start-tick", type=int)
    parser.add_argument("--tick-rate", type=float)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--fps", type=float)
    parser.add_argument("--name")
    parser.add_argument("--id", dest="ident")
    parser.add_argument("--note", action="append", default=[])
    if identity:
        parser.add_argument("--match-id")
        parser.add_argument("--replay-sha256")
        parser.add_argument("--build", type=int)
        parser.add_argument("--map", dest="map_name")
        parser.add_argument("--scene", help="Optional .agrscene manifest to attach")
    if need_output:
        parser.add_argument("--out", required=True)


def _derived_tick_rate(shot: dict) -> float | None:
    last = shot["camera"]["samples"][-1]["replay_time"]
    if last <= 1e-12:
        return None
    return ((shot["range"]["end_tick_exact"] - shot["range"]["start_tick"]) / last)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agr.dolly_shot",
        description="Build and inspect RenderLock shot collections from Dolly takes.")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Create a one-shot collection")
    _add_common(build, need_output=True, identity=True)
    build.add_argument("--replay", required=True, help="The .dem replay used by the shot")

    add = commands.add_parser("add", help="Append a shot to an existing collection")
    _add_common(add, need_output=False, identity=False)
    add.add_argument("--collection", required=True)
    add.add_argument("--replay", help="Expected replay; defaults to the collection's replay")

    validate = commands.add_parser("validate", help="Validate a collection")
    validate.add_argument("path")
    validate.add_argument("--check-files", action="store_true")
    validate.add_argument("--summary", action="store_true")

    inspect = commands.add_parser("inspect", help="Print a collection summary")
    inspect.add_argument("path")
    inspect.add_argument("--check-files", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            collection = build_collection(
                take=args.take, replay=args.replay, project=args.project,
                start_tick=args.start_tick, tick_rate=args.tick_rate,
                width=args.width, height=args.height, fps=args.fps, name=args.name,
                ident=args.ident, match_id=args.match_id,
                replay_sha256=args.replay_sha256, build=args.build,
                map_name=args.map_name, scene=args.scene, notes=args.note,
            )
            write_shot_collection(collection, args.out)
            print(json.dumps({"written": str(Path(args.out).resolve())}, indent=2))
            return 0
        if args.command == "add":
            existing = read_shot_collection(args.collection)
            if args.replay and not same_replay(existing["replay"], {"path": args.replay}):
                _fail("Shot replay identity does not match the collection")
            expected_tick_rate = (args.tick_rate if args.tick_rate is not None
                                  else existing["replay"]["tick_rate"])
            shot = build_shot(
                take=args.take, project=args.project,
                start_tick=args.start_tick, tick_rate=expected_tick_rate,
                width=args.width, height=args.height, fps=args.fps, name=args.name,
                ident=args.ident, notes=args.note,
            )
            derived = _derived_tick_rate(shot)
            if derived is not None and abs(derived - expected_tick_rate) > 1e-6:
                _fail("Shot tick rate does not match the collection")
            if shot["id"] in {item["id"] for item in existing["shots"]}:
                _fail(f"Shot id already exists: {shot['id']}")
            existing["shots"].append(shot)
            write_shot_collection(existing, args.collection)
            print(json.dumps({"written": str(Path(args.collection).resolve())}, indent=2))
            return 0
        if args.command in ("validate", "inspect"):
            data = read_shot_collection(args.path, check_files=args.check_files)
            summary = collection_summary(data)
            print(json.dumps(summary, indent=2))
            return 0
    except (ShotCollectionError, DollyShotError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
