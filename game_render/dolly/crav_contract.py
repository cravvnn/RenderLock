"""Versioned RenderLock shot-collection interchange (v1).

A shot collection is a single JSON document that carries one or more recorded
camera shots for the same replay. Camera samples are embedded so the collection
does not depend on Deadlock Dolly at read time; large colour, depth and layer
media stay beside it as referenced files.

The format is deliberately strict: unknown fields, duplicate JSON keys,
non-finite numbers and inconsistent frame/time ranges are rejected rather than
silently ignored. See ``specs/CRAV.md`` for the lightweight reconstruction
profile; unprofiled research collections retain optional external media.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

FORMAT_NAME = "renderlock-shot-collection"
VERSION = 1
EXTENSION = ".crav"
RECONSTRUCTION_PROFILE = "replay-reconstruction-v1"
MAX_BYTES = 64 * 1024 * 1024
MAX_SHOTS = 256
MAX_SAMPLES = 262_144
MAX_PATH = 4_096
MAX_TEXT = 256
MAX_NOTES = 100

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ShotCollectionError(ValueError):
    """The shot collection is invalid, unsupported or unreadable."""


def _error(what: str, message: str) -> None:
    raise ShotCollectionError(f"{what}: {message}")


def _object(value, what: str, allowed: tuple[str, ...] | None = None) -> dict:
    if not isinstance(value, dict):
        _error(what, "must be an object")
    if allowed is not None:
        unknown = sorted(set(value) - set(allowed))
        if unknown:
            _error(what, "has unknown field(s): " + ", ".join(unknown))
    return value


def _required(data: dict, name: str, what: str):
    if name not in data:
        _error(what, f"is missing required field {name}")
    return data[name]


def _number(value, what: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(what, "must be a number")
    number = float(value)
    if not math.isfinite(number):
        _error(what, "must be finite")
    if minimum is not None and number < minimum:
        _error(what, f"must be at least {minimum:g}")
    if maximum is not None and number > maximum:
        _error(what, f"must be at most {maximum:g}")
    return number


def _integer(value, what: str, *, minimum: int | None = None,
             maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _error(what, "must be an integer")
    if minimum is not None and value < minimum:
        _error(what, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _error(what, f"must be at most {maximum}")
    return value


def _text(value, what: str, *, maximum: int = MAX_TEXT, path: bool = False) -> str:
    if not isinstance(value, str) or not value:
        _error(what, "must be a non-empty string")
    limit = MAX_PATH if path else maximum
    if len(value) > limit:
        _error(what, f"is longer than {limit} characters")
    if _CONTROL.search(value):
        _error(what, "contains control characters")
    return value


def _vector(value, length: int, what: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        _error(what, f"must contain exactly {length} numbers")
    return tuple(_number(item, f"{what}[{index}]") for index, item in enumerate(value))


def _optional_notes(value, what: str) -> None:
    if value is None:
        return
    if not isinstance(value, list) or len(value) > MAX_NOTES:
        _error(what, f"must be an array of at most {MAX_NOTES} strings")
    for index, note in enumerate(value):
        _text(note, f"{what}[{index}]", maximum=4_096)


def _file_ref(value, what: str) -> dict:
    data = _object(value, what, ("path", "sha256", "size_bytes", "kind", "note"))
    _text(_required(data, "path", what), f"{what}.path", path=True)
    if "sha256" in data:
        digest = _text(data["sha256"], f"{what}.sha256", maximum=64)
        if not _SHA256.fullmatch(digest):
            _error(f"{what}.sha256", "must be 64 lowercase hexadecimal characters")
    if "size_bytes" in data:
        _integer(data["size_bytes"], f"{what}.size_bytes", minimum=0)
    if "kind" in data:
        _text(data["kind"], f"{what}.kind", maximum=64)
    if "note" in data:
        _text(data["note"], f"{what}.note", maximum=4_096)
    return data


def _validate_replay(value) -> dict:
    data = _object(value, "replay", (
        "path", "match_id", "sha256", "tick_rate", "build", "total_ticks",
        "map", "notes",
    ))
    _text(_required(data, "path", "replay"), "replay.path", path=True)
    _number(_required(data, "tick_rate", "replay"), "replay.tick_rate", minimum=0.000_001)
    if "match_id" in data:
        _text(data["match_id"], "replay.match_id", maximum=128)
    if "sha256" in data:
        digest = _text(data["sha256"], "replay.sha256", maximum=64)
        if not _SHA256.fullmatch(digest):
            _error("replay.sha256", "must be 64 lowercase hexadecimal characters")
    for name in ("build", "total_ticks"):
        if name in data:
            _integer(data[name], f"replay.{name}", minimum=1)
    if "map" in data:
        _text(data["map"], "replay.map", maximum=256)
    if "notes" in data: _optional_notes(data["notes"], "replay.notes")
    return data


def _validate_camera_source(value, what: str) -> dict:
    data = _object(value, what, ("kind", "version", "path", "sha256", "size_bytes", "note"))
    kind = _text(_required(data, "kind", what), f"{what}.kind", maximum=64)
    if kind not in ("dolly-camera-track", "dolly-camera-bundle", "dolly-recorded-camera", "external"):
        _error(f"{what}.kind", "unsupported camera source")
    if "version" in data and (
            isinstance(data["version"], bool)
            or not isinstance(data["version"], (int, str))):
        _error(f"{what}.version", "must be an integer or string")
    if "path" in data:
        _text(data["path"], f"{what}.path", path=True)
    if "sha256" in data:
        digest = _text(data["sha256"], f"{what}.sha256", maximum=64)
        if not _SHA256.fullmatch(digest):
            _error(f"{what}.sha256", "must be 64 lowercase hexadecimal characters")
    if "size_bytes" in data:
        _integer(data["size_bytes"], f"{what}.size_bytes", minimum=0)
    if "note" in data:
        _text(data["note"], f"{what}.note", maximum=4_096)
    return data


def _validate_sample(value, index: int, fps: float,
                     previous_replay: float | None) -> float:
    what = f"camera.samples[{index}]"
    data = _object(value, what, (
        "frame", "video_time", "replay_time", "position", "right", "up",
        "forward", "horizontal_fov", "aspect", "projection", "inverse_z",
        "viewport", "near", "far", "capture_time", "sample", "state",
    ))
    frame = _integer(_required(data, "frame", what), f"{what}.frame", minimum=0)
    if frame != index:
        _error(f"{what}.frame", "must equal the sample index")
    video_time = _number(_required(data, "video_time", what),
                         f"{what}.video_time", minimum=0)
    expected = index / fps
    if abs(video_time - expected) > 1e-3:
        _error(f"{what}.video_time",
               f"does not match encoded frame {index} at {fps:g} FPS")
    replay_time = _number(_required(data, "replay_time", what),
                          f"{what}.replay_time", minimum=0)
    if previous_replay is not None and replay_time < previous_replay - 1e-9:
        _error(f"{what}.replay_time", "must not decrease")

    position = _vector(_required(data, "position", what), 3, f"{what}.position")
    right = _vector(_required(data, "right", what), 3, f"{what}.right")
    up = _vector(_required(data, "up", what), 3, f"{what}.up")
    forward = _vector(_required(data, "forward", what), 3, f"{what}.forward")
    for name, vector in (("position", position), ("right", right),
                         ("up", up), ("forward", forward)):
        if any(abs(component) > 1e12 for component in vector):
            _error(f"{what}.{name}", "is outside the supported magnitude")
    for name, vector in (("right", right), ("up", up), ("forward", forward)):
        length = math.sqrt(sum(component * component for component in vector))
        if abs(length - 1.0) > 2e-3:
            _error(f"{what}.{name}", "must be a unit vector")
    for left_name, left, right_name, right_vector in (
            ("right", right, "up", up), ("right", right, "forward", forward),
            ("up", up, "forward", forward)):
        dot = sum(a * b for a, b in zip(left, right_vector))
        if abs(dot) > 2e-3:
            _error(what, f"{left_name} and {right_name} must be orthogonal")
    expected_right = (
        forward[1] * up[2] - forward[2] * up[1],
        forward[2] * up[0] - forward[0] * up[2],
        forward[0] * up[1] - forward[1] * up[0],
    )
    if max(abs(a - b) for a, b in zip(right, expected_right)) > 5e-3:
        _error(what, "right must equal forward x up for a right-handed camera")

    fov = _number(_required(data, "horizontal_fov", what),
                  f"{what}.horizontal_fov", minimum=1.0, maximum=179.0)
    aspect = _number(_required(data, "aspect", what),
                     f"{what}.aspect", minimum=0.1, maximum=10.0)
    if "projection" in data:
        projection = _vector(data["projection"], 16, f"{what}.projection")
        if any(abs(projection[index]) > 1e-6 for index in (8, 9, 12, 13)):
            _error(f"{what}.projection", "must be a non-oblique projection")
    if "inverse_z" in data:
        _vector(data["inverse_z"], 4, f"{what}.inverse_z")
    viewport = None
    if "viewport" in data:
        viewport = _vector(data["viewport"], 2, f"{what}.viewport")
        if not 0.0 <= viewport[0] < viewport[1] <= 1.0:
            _error(f"{what}.viewport", "must be an increasing range within 0..1")
    near = None
    if "near" in data:
        near = _number(data["near"], f"{what}.near", minimum=0)
    if "far" in data:
        if data["far"] is not None:
            far = _number(data["far"], f"{what}.far")
            if near is not None and far <= near:
                _error(f"{what}.far", "must be greater than near")
    if "capture_time" in data:
        _number(data["capture_time"], f"{what}.capture_time", minimum=0)
    if "sample" in data:
        _integer(data["sample"], f"{what}.sample", minimum=0)
    if "state" in data and data["state"] not in ("complete", "projection-only"):
        _error(f"{what}.state", "must be complete or projection-only")
    return replay_time


def _validate_camera(value) -> dict:
    data = _object(value, "camera", (
        "source", "space", "unit_scale", "time_origin", "fps", "width", "height",
        "frame_count", "samples", "projection_convention",
    ))
    _validate_camera_source(_required(data, "source", "camera"), "camera.source")
    space = _text(_required(data, "space", "camera"), "camera.space", maximum=64)
    if space != "source-world":
        _error("camera.space", "v1 supports only source-world")
    time_origin = _text(_required(data, "time_origin", "camera"),
                        "camera.time_origin", maximum=64)
    if time_origin != "shot-start":
        _error("camera.time_origin", "v1 supports only shot-start")
    _number(_required(data, "unit_scale", "camera"),
            "camera.unit_scale", minimum=1e-12, maximum=1e6)
    fps = _number(_required(data, "fps", "camera"),
                  "camera.fps", minimum=0.001, maximum=1_000)
    width = _integer(_required(data, "width", "camera"),
                     "camera.width", minimum=1, maximum=32_768)
    height = _integer(_required(data, "height", "camera"),
                      "camera.height", minimum=1, maximum=32_768)
    frame_count = _integer(_required(data, "frame_count", "camera"),
                           "camera.frame_count", minimum=1, maximum=MAX_SAMPLES)
    samples = _required(data, "samples", "camera")
    if not isinstance(samples, list) or len(samples) != frame_count:
        _error("camera.samples", "must be an array with frame_count entries")
    previous = None
    for index, sample in enumerate(samples):
        previous = _validate_sample(sample, index, fps, previous)
    if "projection_convention" in data:
        convention = _text(data["projection_convention"],
                           "camera.projection_convention", maximum=64)
        if convention != "row-major-column-vector":
            _error("camera.projection_convention",
                   "v1 supports only row-major-column-vector")
    return data


def _validate_media(value) -> dict:
    data = _object(value, "media", (
        "color", "depth", "layers", "project", "take", "usd", "audio", "notes",
    ))
    if "color" in data:
        _file_ref(data["color"], "media.color")
    if "depth" in data:
        depth = _object(data["depth"], "media.depth",
                        ("master", "manifest", "camera_bundle", "exr_directory"))
        for name in ("master", "manifest", "camera_bundle", "exr_directory"):
            if name in depth:
                _file_ref(depth[name], f"media.depth.{name}")
    if "layers" in data:
        layers = _object(data["layers"], "media.layers", ("world", "players", "effects"))
        for name in ("world", "players", "effects"):
            if name in layers:
                _file_ref(layers[name], f"media.layers.{name}")
    for name in ("project", "take", "usd", "audio"):
        if name in data:
            _file_ref(data[name], f"media.{name}")
    if "notes" in data: _optional_notes(data["notes"], "media.notes")
    return data


def _validate_shot(value, index: int) -> str:
    what = f"shots[{index}]"
    data = _object(value, what, ("id", "name", "range", "camera", "media", "notes"))
    ident = _text(_required(data, "id", what), f"{what}.id", maximum=64)
    if not _ID.fullmatch(ident):
        _error(f"{what}.id", "must start with a letter or digit and use only A-Z a-z 0-9 . _ -")
    _text(_required(data, "name", what), f"{what}.name", maximum=MAX_TEXT)

    interval = _object(_required(data, "range", what), f"{what}.range",
                        ("start_tick", "end_tick", "end_tick_exact"))
    start = _integer(_required(interval, "start_tick", f"{what}.range"),
                     f"{what}.range.start_tick", minimum=0)
    end = _integer(_required(interval, "end_tick", f"{what}.range"),
                   f"{what}.range.end_tick", minimum=0)
    exact = _number(_required(interval, "end_tick_exact", f"{what}.range"),
                    f"{what}.range.end_tick_exact", minimum=0)
    if end < start:
        _error(f"{what}.range.end_tick", "must be at least start_tick")
    if exact < start:
        _error(f"{what}.range.end_tick_exact", "must be at least start_tick")
    expected_end = math.floor(exact + 1e-6)
    if end != expected_end:
        _error(f"{what}.range.end_tick",
               f"must be floor(end_tick_exact) ({expected_end})")
    if exact > end + 1:
        _error(f"{what}.range.end_tick_exact", "is inconsistent with end_tick")

    _validate_camera(_required(data, "camera", what))
    if "media" in data:
        _validate_media(data["media"])
    if "notes" in data: _optional_notes(data["notes"], f"{what}.notes")
    return ident


def validate_shot_collection(data) -> None:
    """Validate a decoded collection, raising ShotCollectionError."""
    data = _object(data, "shot collection", (
        "format", "version", "created_with", "replay", "scene", "shots", "notes",
        "profile", "reconstruction",
    ))
    if data.get("format") != FORMAT_NAME:
        _error("format", f"must be {FORMAT_NAME!r}")
    version = data.get("version")
    if type(version) is not int or version != VERSION:
        _error("version", f"must be {VERSION}")
    if "created_with" in data:
        created = _object(data["created_with"], "created_with", ("name", "version"))
        _text(_required(created, "name", "created_with"), "created_with.name", maximum=64)
        if "version" in created:
            _text(created["version"], "created_with.version", maximum=64)
    _validate_replay(_required(data, "replay", "shot collection"))
    if "scene" in data:
        _file_ref(data["scene"], "scene")
    shots = data.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        _error("shots", f"must be an array with 1 to {MAX_SHOTS} entries")
    identities = set()
    for index, shot in enumerate(shots):
        ident = _validate_shot(shot, index)
        if ident in identities:
            _error(f"shots[{index}].id", f"duplicates {ident!r}")
        identities.add(ident)
    if "notes" in data: _optional_notes(data["notes"], "notes")
    if "profile" in data:
        if data["profile"] != RECONSTRUCTION_PROFILE:
            _error("profile", "unsupported reconstruction profile")
        _validate_reconstruction(data)
    elif "reconstruction" in data:
        _error("reconstruction", "requires an explicit profile")


def _validate_reconstruction(data: dict) -> None:
    """Validate the lightweight contract, not reconstruction capability."""
    if "scene" in data:
        _error("scene", "is forbidden in the lightweight profile")
    replay = data["replay"]
    for key in ("sha256", "build", "map", "total_ticks"):
        _required(replay, key, "replay")
    request = _object(_required(data, "reconstruction", "shot collection"),
                      "reconstruction", ("map_selection", "missing_content"))
    if request.get("missing_content") != "error":
        _error("reconstruction.missing_content", "must be error; partial output needs explicit user consent")
    selection = _object(_required(request, "map_selection", "reconstruction"),
                        "map_selection", ("mode", "margin_source_units", "include_lighting_dependencies"))
    if selection.get("mode") != "camera-path":
        _error("map_selection.mode", "must be camera-path")
    _number(_required(selection, "margin_source_units", "map_selection"),
            "map_selection.margin_source_units", minimum=0, maximum=65536)
    if selection.get("include_lighting_dependencies") is not True:
        _error("map_selection.include_lighting_dependencies", "must be true")
    count = 0
    for shot in data["shots"]:
        if "media" in shot:
            _error("media", "is forbidden in the lightweight profile")
        camera = shot["camera"]
        if camera["source"]["kind"] == "external":
            _error("camera.source", "requires a recorded Dolly camera")
        if abs(camera["unit_scale"] - 0.0254) > 1e-12:
            _error("camera.unit_scale", "must be 0.0254 metres per Source unit")
        count += camera["frame_count"]
        if count > MAX_SAMPLES:
            _error("shots", "exceeds the collection-wide sample limit")
        samples = camera["samples"]
        offset = samples[0]["replay_time"]
        for sample in samples:
            if sample.get("state") != "complete":
                _error("camera.samples.state", "must be complete")
            if not 1 < sample["horizontal_fov"] < 179:
                _error("camera.horizontal_fov", "must be strictly between 1 and 179")
            if abs(sample["aspect"] - camera["width"] / camera["height"]) > 1e-6:
                _error("camera.aspect", "must match width / height")
            if abs(sample["replay_time"] - offset - sample["frame"] / camera["fps"]) > 1e-6:
                _error("camera.replay_time", "v1 requires normal-speed playback without holds or retiming")
            if "projection" in sample and "projection_convention" not in camera:
                _error("camera.projection_convention", "is required with projection samples")
        end = shot["range"]["start_tick"] + samples[-1]["replay_time"] * replay["tick_rate"]
        if abs(end - shot["range"]["end_tick_exact"]) > 1e-6:
            _error("range.end_tick_exact", "must match the final camera sample's replay tick")
        if end >= replay["total_ticks"]:
            _error("range.end_tick_exact", "must be below replay.total_ticks (exclusive)")
