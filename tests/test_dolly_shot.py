"""Research checks for the RenderLock shot-collection filetype."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from agr import shot_package
from agr.dolly_shot import (
    DollyShotError, add_shot, build_collection, build_shot, read_dolly_camera_track,
    read_dolly_project, same_replay,
)
from agr.shot_package import (
    FORMAT_NAME, ShotCollectionError, collection_summary, read_shot_collection,
    validate_shot_collection, write_shot_collection,
)

FPS = 30.0
TICK_RATE = 64.0
START_TICK = 1000
FOV = 121.89078521728517


def _json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _project(path: Path) -> Path:
    return _json(path, {
        "format": "deadlock-dolly",
        "version": 13,
        "name": "Test shot",
        "interpolation": "smooth",
        "rotation_mode": "shortest",
        "standard_aspect": 16 / 9,
        "lens_interpolation": "smooth",
        "start_tick": START_TICK,
        "tick_rate": TICK_RATE,
        "setup_values": {},
        "keyframes": [],
        "tracks": [],
    })


def _take(path: Path, frames: int = 3, last_replay: float | None = None) -> Path:
    data = {
        "format": "deadlock-dolly-shot",
        "version": 1,
        "video_file": "clip.mkv",
        "fps": FPS,
        "fixed_step": True,
        "frames_written": frames,
        "duration_seconds": frames / FPS,
        "first_frame": 0,
        "last_frame": frames - 1,
        "first_replay_time": 0.0,
        "last_replay_time": (frames - 1) / FPS if last_replay is None else last_replay,
    }
    return _json(path / "shot.json", data)


def _track(path: Path, *, version: int = 2, frames: int = 3) -> Path:
    rows = []
    for frame in range(frames):
        yaw = float(frame)
        rows.append([
            frame, frame / FPS, frame / FPS, 100.0 + frame, 200.0, 300.0,
            0.0, yaw, 0.0, FOV, frame / FPS,
        ])
    data = {
        "format": "deadlock-dolly-camera-track",
        "version": version,
        "fps": FPS,
        "fixed_step": True,
        "frames_written": frames,
        "fields": (["frame", "video_time", "phase", "x", "y", "z",
                    "pitch", "yaw", "roll", "horizontal_fov", "capture_time"]
                   if version == 2 else
                   ["frame", "video_time", "phase", "x", "y", "z",
                    "pitch", "yaw", "roll", "horizontal_fov"]),
        "frames": [row if version == 2 else row[:10] for row in rows],
    }
    if version == 2:
        data.update({
            "complete": True, "partial_reason": "", "source": "native-view-hook",
            "width": 1280, "height": 720, "depth": True,
            "first_qpc": 1, "qpc_frequency": 10_000_000,
        })
    return _json(path / "clip.mkv.camera-track.json", data)


def _basis(yaw: float):
    radians = math.radians(yaw)
    forward = (math.cos(radians), math.sin(radians), 0.0)
    right = (math.sin(radians), -math.cos(radians), 0.0)
    up = (0.0, 0.0, 1.0)
    return forward, right, up


def _bundle(path: Path, *, frames: int = 3) -> Path:
    records = []
    for frame in range(frames):
        position = [100.0 + frame, 200.0, 300.0]
        forward, right, up = _basis(float(frame))
        backward = tuple(-value for value in forward)
        view = [
            right[0], up[0], backward[0], 0,
            right[1], up[1], backward[1], 0,
            right[2], up[2], backward[2], 0,
            -sum(a * b for a, b in zip(position, right)),
            -sum(a * b for a, b in zip(position, up)),
            -sum(a * b for a, b in zip(position, backward)),
            1,
        ]
        records.append({
            "frame": frame,
            "sample": frame,
            "replay_time": frame / FPS,
            "capture_pts_100ns": round(frame / FPS * 1e7),
            "state": "complete",
            "view": view,
            "position": position,
            "up": list(up),
            "forward": list(forward),
            "projection": [1 / math.tan(math.radians(FOV) / 2), 0, 0, 0,
                           0, 0.987654388, 0, 0, 0, 0, 0, 7, 0, 0, -1, 0],
            "inverse_z": [0, -1, 0.142857, 0],
            "viewport": [0, 1],
            "near": 7,
            "far": None,
        })
    data = {
        "format": "deadlock-dolly-camera",
        "version": 1,
        "complete": True,
        "frames": frames,
        "width": 1280,
        "height": 720,
        "color_width": 1280,
        "color_height": 720,
        "units": "positive camera-axis game units",
        "records": records,
    }
    return _json(path / "depth" / "camera.json", data)


def fixture(tmp_path: Path) -> dict:
    take = tmp_path / "capture"
    take.mkdir(parents=True)
    project = _project(tmp_path / "Test shot.json")
    sidecar = _take(take)
    track = _track(take)
    bundle = _bundle(take)
    (take / "clip.mkv").write_bytes(b"video")
    (take / "depth" / "depth.mov").write_bytes(b"depth")
    _json(take / "depth" / "manifest.json", {"version": 3, "frames": 3})
    (take / "depth" / "exr").mkdir()
    (take / "world").mkdir()
    (take / "world" / "world.mp4").write_bytes(b"world")
    (take / "clip_camera.usda").write_text("#usda 1.0\n", encoding="utf-8")
    (take / "audio.wav").write_bytes(b"wav")
    replay = tmp_path / "119933768.dem"
    replay.write_bytes(b"replay")
    return {
        "take": take, "project": project, "sidecar": sidecar, "track": track,
        "bundle": bundle, "replay": replay,
    }


def build(fixture, **options):
    values = {
        "take": fixture["take"], "replay": fixture["replay"],
        "project": fixture["project"], "match_id": "119933768",
    }
    values.update(options)
    return build_collection(**values)


def test_build_and_round_trip_with_relative_media(tmp_path):
    source = fixture(tmp_path)
    collection = build(source)
    output = tmp_path / "package" / "shots.crav"
    write_shot_collection(collection, output)
    data = read_shot_collection(output, check_files=True)

    assert data["format"] == FORMAT_NAME
    assert data["replay"]["path"] == str(source["replay"].resolve())
    assert data["replay"]["match_id"] == "119933768"
    shot = data["shots"][0]
    assert shot["id"] == "test-shot"
    assert shot["name"] == "Test shot"
    assert shot["range"]["start_tick"] == START_TICK
    assert shot["range"]["end_tick"] == 1004
    assert shot["range"]["end_tick_exact"] == pytest.approx(START_TICK + 2 / FPS * TICK_RATE)
    assert shot["camera"]["source"]["kind"] == "dolly-camera-bundle"
    assert shot["camera"]["time_origin"] == "shot-start"
    assert shot["camera"]["fps"] == FPS
    assert shot["camera"]["width"] == 1280
    assert shot["camera"]["height"] == 720
    assert shot["camera"]["frame_count"] == 3
    assert shot["camera"]["samples"][2]["position"] == [102.0, 200.0, 300.0]
    assert shot["camera"]["samples"][2]["projection"][0] == pytest.approx(
        1 / math.tan(math.radians(FOV) / 2))
    assert shot["camera"]["samples"][2]["inverse_z"] == [0, -1, 0.142857, 0]
    assert shot["camera"]["samples"][2]["viewport"] == [0, 1]
    assert shot["camera"]["samples"][2]["far"] is None
    assert shot["media"]["color"]["path"] == str((source["take"] / "clip.mkv").resolve())
    assert shot["media"]["depth"]["camera_bundle"]["path"] == str(
        source["bundle"].resolve())
    assert shot["media"]["layers"]["world"]["path"] == str(
        (source["take"] / "world" / "world.mp4").resolve())
    assert shot["media"]["usd"]["path"] == str(
        (source["take"] / "clip_camera.usda").resolve())
    assert any("agreed" in note for note in shot["notes"])

    raw = json.loads(output.read_text(encoding="utf-8"))
    assert not Path(raw["replay"]["path"]).is_absolute()
    assert not Path(raw["shots"][0]["media"]["color"]["path"]).is_absolute()


def test_optional_scene_manifest_is_a_checked_reference(tmp_path):
    source = fixture(tmp_path)
    scene = _json(tmp_path / "scene" / "manifest.json", {
        "schema_version": 1, "start_tick": START_TICK, "end_tick": 1010,
        "tick_rate": TICK_RATE, "entities": [{"id": "x", "samples": [{"tick": 1000, "position": [0, 0, 0], "body_angles": [0, 0, 0]}]}],
    })
    collection = build_collection(
        take=source["take"], replay=source["replay"], project=source["project"],
        scene=scene)
    output = tmp_path / "package" / "shots.crav"
    write_shot_collection(collection, output)
    data = read_shot_collection(output, check_files=True)
    assert data["scene"]["path"] == str(scene.resolve())
    assert collection_summary(data)["scene"]["path"] == str(scene.resolve())


def test_missing_sidecar_uses_track_and_prefers_colour_master(tmp_path):
    source = fixture(tmp_path)
    source["sidecar"].unlink()
    (source["take"] / "clip_ae_prores.mov").write_bytes(b"intermediate")
    shot = build_shot(take=source["take"], project=source["project"])
    assert shot["media"]["color"]["path"] == str((source["take"] / "clip.mkv").resolve())
    assert "take" not in shot["media"]
    assert any("No Dolly take sidecar" in note for note in shot["notes"])


def test_track_version_one_and_explicit_dimensions(tmp_path):
    source = fixture(tmp_path)
    source["bundle"].unlink()
    track = _track(source["take"], version=1)
    source["track"] = track
    shot = build_shot(
        take=source["take"], project=source["project"], width=1280, height=720)
    assert shot["camera"]["source"]["kind"] == "dolly-camera-track"
    assert shot["camera"]["source"]["version"] == 1
    assert shot["camera"]["samples"][1]["aspect"] == 1280 / 720
    assert "projection" not in shot["camera"]["samples"][1]
    collection = build_collection(
        take=source["take"], replay=source["replay"], project=source["project"],
        width=1280, height=720)
    write_shot_collection(collection, tmp_path / "track.crav")
    assert read_shot_collection(tmp_path / "track.crav")["shots"][0]["camera"]["source"]["version"] == 1


def test_add_shot_and_reject_duplicate_or_other_replay(tmp_path):
    source = fixture(tmp_path)
    output = tmp_path / "shots.crav"
    write_shot_collection(build(source), output)
    second = build_shot(take=source["take"], project=source["project"], name="Second")
    assert second["id"] == "second"
    collection = add_shot(
        output, second,
        replay={"path": str(source["replay"]), "tick_rate": TICK_RATE,
                "match_id": "119933768"})
    assert [shot["id"] for shot in collection["shots"]] == ["test-shot", "second"]
    assert len(read_shot_collection(output)["shots"]) == 2

    with pytest.raises(DollyShotError, match="already exists"):
        add_shot(output, second)
    other = {"path": str(source["replay"]), "tick_rate": TICK_RATE, "match_id": "other"}
    assert not same_replay({"path": str(source["replay"]), "match_id": "119933768"}, other)
    with pytest.raises(DollyShotError, match="replay identity"):
        add_shot(output, build_shot(take=source["take"], project=source["project"],
                                    name="Third"), replay=other)


def test_strict_schema_rejects_corruption(tmp_path):
    source = fixture(tmp_path)
    valid = build(source)
    validate_shot_collection(valid)

    cases = []
    bad = copy.deepcopy(valid)
    bad["shots"][0]["unexpected"] = True
    cases.append((bad, "unknown field"))
    bad = copy.deepcopy(valid)
    bad["shots"][0]["id"] = bad["shots"][0]["id"].replace("-", " ")
    cases.append((bad, "must start"))
    bad = copy.deepcopy(valid)
    bad["shots"][0]["range"]["end_tick"] = 1003
    cases.append((bad, "floor"))
    bad = copy.deepcopy(valid)
    bad["shots"][0]["camera"]["frame_count"] = 4
    cases.append((bad, "frame_count"))
    bad = copy.deepcopy(valid)
    bad["shots"][0]["camera"]["samples"][0]["right"] = [0, 0, 0]
    cases.append((bad, "unit vector"))
    bad = copy.deepcopy(valid)
    bad["shots"][0]["camera"]["samples"][0]["position"] = [0, float("inf"), 0]
    cases.append((bad, "finite"))
    bad = copy.deepcopy(valid)
    bad["version"] = 2
    cases.append((bad, "must be 1"))
    for data, message in cases:
        with pytest.raises(ShotCollectionError, match=message):
            validate_shot_collection(data)


def test_duplicate_json_key_and_non_finite_are_rejected(tmp_path):
    source = fixture(tmp_path)
    output = tmp_path / "shots.crav"
    write_shot_collection(build(source), output)
    text = output.read_text(encoding="utf-8")
    output.write_text(text.replace('"format": "renderlock-shot-collection"',
                                   '"format": "renderlock-shot-collection",\n  "format": "other"', 1),
                      encoding="utf-8")
    with pytest.raises(ShotCollectionError, match="duplicate JSON field"):
        read_shot_collection(output)

    output.write_text('{"format": NaN}\n', encoding="utf-8")
    with pytest.raises(ShotCollectionError, match="non-finite"):
        read_shot_collection(output)


def test_check_files_detects_missing_and_changed_media(tmp_path):
    source = fixture(tmp_path)
    output = tmp_path / "shots.crav"
    write_shot_collection(build(source), output)
    data = read_shot_collection(output, resolve_paths=False)
    assert data["shots"][0]["media"]["color"]["size_bytes"] == 5
    data["shots"][0]["media"]["color"]["size_bytes"] = 4
    output.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ShotCollectionError, match="size changed"):
        read_shot_collection(output, check_files=True)

    (source["take"] / "clip.mkv").unlink()
    with pytest.raises(ShotCollectionError, match="missing"):
        read_shot_collection(output, check_files=True)


def test_summary_and_cli_build(tmp_path, capsys):
    source = fixture(tmp_path)
    output = tmp_path / "shots.crav"
    from agr.dolly_shot import main
    assert main(["build", "--take", str(source["take"]), "--project", str(source["project"]),
                 "--replay", str(source["replay"]), "--match-id", "119933768",
                 "--out", str(output)]) == 0
    assert main(["inspect", str(output)]) == 0
    captured = capsys.readouterr()
    assert "renderlock-shot-collection" in captured.out
    data = read_shot_collection(output)
    assert collection_summary(data)["shots"][0]["camera_source"] == "dolly-camera-bundle"


def test_missing_camera_and_missing_start_tick_fail(tmp_path):
    source = fixture(tmp_path)
    source["track"].unlink()
    source["bundle"].unlink()
    with pytest.raises(DollyShotError, match="No recorded camera"):
        build_shot(take=source["take"], project=source["project"])
    source = fixture(tmp_path / "second")
    with pytest.raises(DollyShotError, match="Start tick"):
        build_collection(take=source["take"], replay=source["replay"])


def test_project_reader_rejects_wrong_version(tmp_path):
    path = _json(tmp_path / "wrong.json", {
        "format": "deadlock-dolly", "version": 99, "name": "x",
        "start_tick": 0, "tick_rate": 64,
    })
    with pytest.raises(DollyShotError, match="Unsupported"):
        read_dolly_project(path)


def test_camera_track_reader_rejects_partial_or_bad_phase(tmp_path):
    source = fixture(tmp_path)
    data = json.loads(source["track"].read_text(encoding="utf-8"))
    data["complete"] = False
    _json(source["track"], data)
    with pytest.raises(DollyShotError, match="Incomplete"):
        read_dolly_camera_track(source["track"])

    source = fixture(tmp_path / "second")
    data = json.loads(source["track"].read_text(encoding="utf-8"))
    data["frames"][2][2] = 0.0
    _json(source["track"], data)
    with pytest.raises(DollyShotError, match="replay time"):
        read_dolly_camera_track(source["track"])
