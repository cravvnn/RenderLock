"""Headless Blender proof for a RenderLock .crav camera import.

Run with Blender, not the project Python:

  blender --background --factory-startup --python tools/verify_shot_package_blender.py -- \
    --package path/to/shot.crav --output path/to/proof.blend

It creates a minimal scene manifest for the package's replay/range, applies the
embedded cameras, and checks camera transforms and recorded projection against
Blender's own camera evaluation. It writes a JSON report beside the .blend.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agr.blender_shot_package import apply_shot_package  # noqa: E402
from agr.shot_package import read_shot_collection  # noqa: E402


def _camera_matrix(sample):
    position = sample["position"]
    right, up, forward = sample["right"], sample["up"], sample["forward"]
    backward = [-value for value in forward]
    rows = [
        [right[0], up[0], backward[0], 0.0],
        [right[1], up[1], backward[1], 0.0],
        [right[2], up[2], backward[2], 0.0],
        [
            -sum(a * b for a, b in zip(position, right)),
            -sum(a * b for a, b in zip(position, up)),
            -sum(a * b for a, b in zip(position, backward)),
            1.0,
        ],
    ]
    return rows


def _project(sample, point):
    view = _camera_matrix(sample)
    x, y, z = point
    view_space = [
        x * view[0][0] + y * view[1][0] + z * view[2][0] + view[3][0],
        x * view[0][1] + y * view[1][1] + z * view[2][1] + view[3][1],
        x * view[0][2] + y * view[1][2] + z * view[2][2] + view[3][2],
        1.0,
    ]
    projection = sample["projection"]
    clip = [
        sum(projection[row * 4 + column] * view_space[column] for column in range(4))
        for row in range(4)
    ]
    if clip[3] == 0:
        return None
    ndc_x, ndc_y = clip[0] / clip[3], clip[1] / clip[3]
    return ((ndc_x + 1) / 2, (1 - ndc_y) / 2)


def _segment(collection):
    shots = collection["shots"]
    start = min(shot["range"]["start_tick"] for shot in shots)
    end = max(shot["range"]["end_tick"] for shot in shots) + 8
    metadata = {
        "match_id": collection["replay"].get("match_id"),
        "demo_sha256": collection["replay"].get("sha256"),
        "build": collection["replay"].get("build"),
        "coordinate_system": {
            "unit_scale": 0.0254,
            "origin": [0, 0, 0],
            "world_yaw_degrees": 0,
            "model_yaw_degrees": 180,
        },
    }
    return {
        "schema_version": 1,
        "tick_rate": collection["replay"]["tick_rate"],
        "start_tick": start,
        "end_tick": end,
        "metadata": metadata,
        "entities": [],
    }


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    collection = read_shot_collection(args.package)
    segment = _segment(collection)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    fps = float(collection["shots"][0]["camera"]["fps"])
    scene.render.fps = round(fps)
    scene.render.fps_base = round(fps) / fps
    scene.frame_start = 1
    scene.frame_end = math.ceil(
        1 + (segment["end_tick"] - segment["start_tick"]) * fps / segment["tick_rate"])
    scene.render.resolution_x = collection["shots"][0]["camera"]["width"]
    scene.render.resolution_y = collection["shots"][0]["camera"]["height"]
    scene.render.resolution_percentage = 100

    report = apply_shot_package(scene, segment, collection, fps=fps)
    assert report["camera_count"] == len(collection["shots"])
    assert scene.camera is not None
    camera = scene.camera
    assert camera.type == "CAMERA"

    errors = []
    projection_errors = []
    checks = []
    for shot in collection["shots"]:
        camera = next(obj for obj in bpy.data.objects
                      if obj.type == "CAMERA" and obj.get("renderlock_shot_id") == shot["id"])
        samples = shot["camera"]["samples"]
        frames = []
        for sample in samples:
            tick = shot["range"]["start_tick"] + sample["replay_time"] * segment["tick_rate"]
            frame = 1.0 + (tick - segment["start_tick"]) * fps / segment["tick_rate"]
            frames.append(frame)
            scene.frame_set(math.floor(frame), subframe=frame % 1)
            expected = sample["position"]
            expected_location = [value * 0.0254 for value in expected]
            errors.append(max(abs(a - b) for a, b in zip(camera.matrix_world.translation, expected_location)))
            if "projection" in sample:
                points = [
                    [sample["position"][axis] + sample["forward"][axis] * 500 for axis in range(3)],
                    [sample["position"][axis] + sample["forward"][axis] * 500
                     + sample["right"][axis] * 100 + sample["up"][axis] * 50 for axis in range(3)],
                    [sample["position"][axis] + sample["forward"][axis] * 800
                     - sample["right"][axis] * 200 + sample["up"][axis] * 100 for axis in range(3)],
                ]
                for point in points:
                    actual = world_to_camera_view(scene, camera, Vector(point) * 0.0254)
                    expected_px = _project(sample, point)
                    if expected_px is None:
                        continue
                    pixel_error = max(
                        abs(actual.x - expected_px[0]) * shot["camera"]["width"],
                        abs((1 - actual.y) - expected_px[1]) * shot["camera"]["height"])
                    projection_errors.append(pixel_error)
                    checks.append({
                        "shot": shot["id"],
                        "frame": frame,
                        "actual": [actual.x, 1 - actual.y],
                        "expected": list(expected_px),
                        "pixel_error": pixel_error,
                    })
        assert len(frames) == shot["camera"]["frame_count"]
        assert max(abs(a - b) for a, b in zip(frames, sorted(frames))) < 1e-9

    proof = {
        "package": str(args.package),
        "camera_count": report["camera_count"],
        "active_camera": report["active_camera"],
        "frames": collection["shots"][0]["camera"]["frame_count"],
        "max_location_error_m": max(errors) if errors else 0,
        "max_projection_error_px": max(projection_errors) if projection_errors else 0,
        "projection_checks": len(checks),
        "check_samples": checks[:6],
        "scene_range": [segment["start_tick"], segment["end_tick"]],
        "limits": "Synthetic empty scene; verifies camera transform and projection only. "
                  "No character, cloth, particle, map, material or visual parity claim.",
    }
    assert proof["max_location_error_m"] < 1e-4, proof
    assert proof["max_projection_error_px"] < 0.1, proof
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output), compress=True)
    args.output.with_suffix(".proof.json").write_text(json.dumps(proof, indent=2) + "\n",
                                                      encoding="utf-8")
    print("SHOT_PACKAGE_PROOF", json.dumps(proof), flush=True)


if __name__ == "__main__":
    main()
