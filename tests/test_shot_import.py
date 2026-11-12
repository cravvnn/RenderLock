"""Pure checks for the shot-collection camera importer."""
from __future__ import annotations

import copy
import math

import pytest

from agr.blender_shot_package import (
    ShotPackageImportError, camera_transform, scene_coordinates, scene_frame,
    validate_against_scene,
)


def camera_sample(**overrides):
    sample = {
        "frame": 0,
        "video_time": 0.0,
        "replay_time": 0.5,
        "position": [100.0, 0.0, 0.0],
        "right": [0.0, -1.0, 0.0],
        "up": [0.0, 0.0, 1.0],
        "forward": [1.0, 0.0, 0.0],
        "horizontal_fov": 90.0,
        "aspect": 16 / 9,
        "near": 4.0,
        "far": None,
        "state": "complete",
    }
    sample.update(overrides)
    return sample


def collection():
    return {
        "format": "renderlock-shot-collection",
        "version": 1,
        "replay": {
            "path": "119690843.dem",
            "match_id": "119690843",
            "sha256": "a" * 64,
            "tick_rate": 64.0,
            "build": 6851,
        },
        "shots": [{
            "id": "shot-1",
            "name": "Shot 1",
            "range": {"start_tick": 1000, "end_tick": 1016, "end_tick_exact": 1016.5},
            "camera": {
                "source": {"kind": "dolly-camera-bundle"},
                "space": "source-world",
                "unit_scale": 0.0254,
                "time_origin": "shot-start",
                "fps": 30.0,
                "width": 1280,
                "height": 720,
                "frame_count": 1,
                "samples": [camera_sample()],
            },
        }],
    }


def scene():
    return {
        "tick_rate": 64.0,
        "start_tick": 1000,
        "end_tick": 1100,
        "metadata": {
            "match_id": 119690843,
            "demo_sha256": "a" * 64,
            "build": 6851,
            "coordinate_system": {
                "unit_scale": 0.0254,
                "origin": [0, 0, 0],
                "world_yaw_degrees": 0,
                "model_yaw_degrees": 180,
            },
        },
    }


def test_scene_coordinates_handles_legacy_string_and_defaults():
    data = {"metadata": {"coordinate_system": "source2_z_up_hammer_units"}}
    assert scene_coordinates(data) == {
        "unit_scale": 0.0254, "origin": [0, 0, 0],
        "world_yaw_degrees": 0, "model_yaw_degrees": 0,
    }
    data = {"metadata": {"coordinate_system": {
        "unit_scale": 1.0, "origin": [1, 2, 3], "world_yaw_degrees": 45,
        "model_yaw_degrees": 180,
    }}}
    assert scene_coordinates(data)["world_yaw_degrees"] == 45


def test_scene_frame_uses_replay_time():
    assert scene_frame(collection()["shots"][0], camera_sample(), scene_start_tick=1000,
                       tick_rate=64, fps=30) == pytest.approx(16.0)
    assert scene_frame(collection()["shots"][0], camera_sample(replay_time=0.0),
                       scene_start_tick=1000, tick_rate=64, fps=30) == pytest.approx(1.0)
    assert scene_frame(collection()["shots"][0], camera_sample(replay_time=0.5),
                       scene_start_tick=998, tick_rate=64, fps=30) == pytest.approx(16.9375)


def test_camera_transform_applies_scene_coordinates():
    transform = camera_transform(camera_sample(), {
        "unit_scale": 0.0254,
        "origin": [0, 0, 0],
        "world_yaw_degrees": 90,
    })
    assert transform["location"] == pytest.approx([0.0, 2.54, 0.0], abs=1e-9)
    flat = [value for row in transform["rotation"] for value in row]
    assert flat == pytest.approx([1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0], abs=1e-9)
    assert transform["lens"] == pytest.approx(18.0)
    assert transform["clip_start"] == pytest.approx(4 * 0.0254)
    assert transform["clip_end"] == 1_000_000.0


def test_validate_accepts_matching_identity_and_range():
    warnings = validate_against_scene(collection(), scene(), fps=30.0)
    assert warnings == []


@pytest.mark.parametrize("field,value,message", [
    ("match_id", "other", "match_id"),
    ("sha256", "b" * 64, "SHA-256"),
    ("build", 9999, "build"),
    ("tick_rate", 32.0, "tick rate"),
])
def test_validate_rejects_identity_mismatch(field, value, message):
    data = collection()
    data["replay"][field] = value
    with pytest.raises(ShotPackageImportError, match=message):
        validate_against_scene(data, scene(), fps=30.0)


def test_validate_rejects_source_unit_mismatch():
    data = collection()
    data["shots"][0]["camera"]["unit_scale"] = 1.0
    with pytest.raises(ShotPackageImportError, match="unit scale"):
        validate_against_scene(data, scene(), fps=30.0)


def test_validate_rejects_range_outside_scene():
    data = collection()
    data["shots"][0]["range"] = {
        "start_tick": 999, "end_tick": 1016, "end_tick_exact": 1016.5}
    with pytest.raises(ShotPackageImportError, match="starts before"):
        validate_against_scene(data, scene(), fps=30.0)
    data = collection()
    data["shots"][0]["range"] = {
        "start_tick": 1000, "end_tick": 1100, "end_tick_exact": 1100.5}
    with pytest.raises(ShotPackageImportError, match="ends after"):
        validate_against_scene(data, scene(), fps=30.0)


def test_validate_requires_identity_on_both_sides():
    data = collection()
    data["replay"].pop("match_id")
    data["replay"].pop("sha256")
    with pytest.raises(ShotPackageImportError, match="no match_id"):
        validate_against_scene(data, scene(), fps=30.0)

    data = collection()
    scene_data = scene()
    scene_data["metadata"].pop("match_id")
    scene_data["metadata"].pop("demo_sha256")
    with pytest.raises(ShotPackageImportError, match="scene has no replay identity"):
        validate_against_scene(data, scene_data, fps=30.0)


def test_validate_warns_for_different_fps_and_size():
    data = collection()
    data["shots"][0]["camera"]["fps"] = 60.0
    warnings = validate_against_scene(data, scene(), fps=30.0)
    assert any("fractional frames" in warning for warning in warnings)

    second = copy.deepcopy(data["shots"][0])
    second["id"] = "shot-2"
    second["camera"]["width"] = 1920
    second["camera"]["height"] = 1080
    data["shots"].append(second)
    warnings = validate_against_scene(data, scene(), fps=30.0)
    assert any("different output sizes" in warning for warning in warnings)
