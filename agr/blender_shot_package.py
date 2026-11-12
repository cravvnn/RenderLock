"""Apply a RenderLock shot collection to a Blender scene.

The camera stage is deliberately independent of asset conversion: this module
only validates replay/range identity and creates cameras from the embedded
samples. Import it inside Blender after the scene frame range is known and
before camera-dependent effect stages run.

The pure helpers (``scene_frame`` and ``camera_transform``) do not import bpy
and are covered by ordinary pytest. ``apply_shot_package`` imports Blender's
modules only when called.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .shot_package import (
    ShotCollectionError, read_shot_collection, validate_shot_collection,
)


class ShotPackageImportError(ValueError):
    """The package cannot be applied to this scene without changing identity or range."""


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShotPackageImportError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ShotPackageImportError(f"{label} must be finite")
    return number


def _rotation_z(degrees: float):
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))


def _apply(matrix, vector):
    return tuple(sum(matrix[row][column] * vector[column] for column in range(3))
                 for row in range(3))


def scene_frame(shot: dict, sample: dict, *, scene_start_tick: int,
                tick_rate: float, fps: float) -> float:
    """Scene frame for one camera sample, using replay time rather than video time."""
    tick_rate = _number(tick_rate, "tick rate")
    fps = _number(fps, "scene FPS")
    if tick_rate <= 0 or fps <= 0:
        raise ShotPackageImportError("tick rate and scene FPS must be positive")
    start_tick = shot.get("range", {}).get("start_tick")
    if isinstance(start_tick, bool) or not isinstance(start_tick, int):
        raise ShotPackageImportError("shot range has no integer start_tick")
    replay_time = _number(sample.get("replay_time"), "sample replay_time")
    if replay_time < 0:
        raise ShotPackageImportError("sample replay_time must be non-negative")
    tick = start_tick + replay_time * tick_rate
    return 1.0 + (tick - scene_start_tick) * fps / tick_rate


def camera_transform(sample: dict, coordinates: dict) -> dict:
    """Source camera sample -> Blender location, rotation matrix and lens.

    ``rotation`` is a row-major 3x3 matrix whose columns are Blender camera
    X/right, Y/up and Z/backward. It maps camera-local vectors to world space.
    """
    unit_scale = _number(coordinates.get("unit_scale", 0.0254), "unit scale")
    if unit_scale <= 0:
        raise ShotPackageImportError("unit scale must be positive")
    origin = coordinates.get("origin", (0.0, 0.0, 0.0))
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        raise ShotPackageImportError("coordinate origin must contain three numbers")
    origin = tuple(_number(value, "coordinate origin") for value in origin)
    world_yaw = _number(coordinates.get("world_yaw_degrees", 0.0), "world yaw")
    world = _rotation_z(world_yaw)

    position = sample.get("position")
    right = sample.get("right")
    up = sample.get("up")
    forward = sample.get("forward")
    for label, vector in (("position", position), ("right", right),
                          ("up", up), ("forward", forward)):
        if not isinstance(vector, (list, tuple)) or len(vector) != 3:
            raise ShotPackageImportError(f"sample {label} must contain three numbers")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(float(value)) for value in vector):
            raise ShotPackageImportError(f"sample {label} must be finite numbers")

    location = _apply(world, tuple((float(position[index]) - origin[index]) * unit_scale
                                   for index in range(3)))
    right = _apply(world, tuple(float(value) for value in right))
    up = _apply(world, tuple(float(value) for value in up))
    forward = _apply(world, tuple(float(value) for value in forward))
    rotation = (
        (right[0], up[0], -forward[0]),
        (right[1], up[1], -forward[1]),
        (right[2], up[2], -forward[2]),
    )
    fov = _number(sample.get("horizontal_fov"), "sample horizontal_fov")
    if not 1 < fov < 179:
        raise ShotPackageImportError("sample horizontal_fov must be between 1 and 179 degrees")
    lens = 18.0 / math.tan(math.radians(fov) / 2.0)
    near = sample.get("near")
    far = sample.get("far")
    clip_start = (max(0.01, _number(near, "sample near") * unit_scale)
                  if near is not None else 0.01)
    clip_end = (_number(far, "sample far") * unit_scale
                if far is not None else 1_000_000.0)
    if clip_end <= clip_start:
        raise ShotPackageImportError("sample far plane must be greater than near")
    return {
        "location": list(location),
        "rotation": [list(row) for row in rotation],
        "lens": lens,
        "clip_start": clip_start,
        "clip_end": clip_end,
    }


def scene_coordinates(data: dict) -> dict:
    coordinates = data.get("metadata", {}).get("coordinate_system", {})
    if coordinates == "source2_z_up_hammer_units" or not isinstance(coordinates, dict):
        coordinates = {}
    result = {"unit_scale": 0.0254, "origin": [0, 0, 0],
              "world_yaw_degrees": 0, "model_yaw_degrees": 0}
    result.update(coordinates)
    return result


def _metadata_identity(value) -> str | None:
    return None if value is None else str(value)


def validate_against_scene(collection: dict, data: dict, *,
                           scene_path=None, fps: float | None = None) -> list[str]:
    """Check package identity/range against a decoded RenderLock scene manifest.

    Returns warnings. Raises ShotPackageImportError for identity or range
    mismatches that would make the camera belong to a different scene.
    """
    validate_shot_collection(collection)
    if "profile" in collection:
        raise ShotPackageImportError(
            "Lightweight .crav reconstruction is not implemented yet; "
            "the camera-only importer cannot satisfy map-selection requirements.")
    if not isinstance(data, dict):
        raise ShotPackageImportError("scene manifest must be an object")
    errors = []
    warnings = []
    replay = collection["replay"]
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    scene_rate = data.get("tick_rate")
    if (isinstance(scene_rate, bool) or not isinstance(scene_rate, (int, float))
            or not math.isfinite(float(scene_rate)) or float(scene_rate) <= 0):
        errors.append("scene tick_rate is invalid")
    elif abs(float(scene_rate) - float(replay["tick_rate"])) > 1e-9:
        errors.append("shot package tick rate does not match the scene")

    package_match = _metadata_identity(replay.get("match_id"))
    scene_match = _metadata_identity(metadata.get("match_id"))
    if package_match and scene_match and package_match != scene_match:
        errors.append("shot package match_id does not match the scene")
    package_sha = replay.get("sha256")
    scene_sha = metadata.get("demo_sha256")
    if package_sha and scene_sha and str(package_sha).lower() != str(scene_sha).lower():
        errors.append("shot package replay SHA-256 does not match the scene")
    if not package_match and not package_sha:
        errors.append("shot package has no match_id or replay SHA-256 identity")
    if (package_match or package_sha) and not (scene_match or scene_sha):
        errors.append("scene has no replay identity to compare with the shot package")
    package_build = _metadata_identity(replay.get("build"))
    scene_build = _metadata_identity(metadata.get("build"))
    if package_build and scene_build and package_build != scene_build:
        errors.append("shot package replay build does not match the scene")

    if "scene" in collection and scene_path is not None:
        declared = Path(collection["scene"]["path"]).resolve()
        actual = Path(scene_path).resolve()
        if declared != actual:
            warnings.append("shot package scene reference does not point at this scene manifest")

    start = data.get("start_tick")
    end = data.get("end_tick")
    if (isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int) or end < start):
        errors.append("scene tick range is invalid")
    else:
        for shot in collection["shots"]:
            interval = shot["range"]
            if interval["start_tick"] < start:
                errors.append(f"shot {shot['id']} starts before the scene range")
            if interval["end_tick_exact"] > end + 1e-6:
                errors.append(f"shot {shot['id']} ends after the scene range")
            if interval["end_tick"] > end:
                errors.append(f"shot {shot['id']} whole-tick range exceeds the scene range")

    scene_units = scene_coordinates(data).get("unit_scale", 0.0254)
    for shot in collection["shots"]:
        if abs(float(shot["camera"]["unit_scale"]) - float(scene_units)) > 1e-12:
            errors.append(
                f"shot {shot['id']} source unit scale does not match the scene coordinates")
    if fps is not None:
        fps = _number(fps, "scene FPS")
        for shot in collection["shots"]:
            if abs(float(shot["camera"]["fps"]) - fps) > 1e-6:
                warnings.append(
                    f"shot {shot['id']} is {shot['camera']['fps']:g} FPS; "
                    f"the scene is {fps:g} FPS, so its camera keys use fractional frames")
    sizes = {(shot["camera"]["width"], shot["camera"]["height"])
             for shot in collection["shots"]}
    if len(sizes) > 1:
        warnings.append("shots use different output sizes; the first shot sets the scene resolution")
    if errors:
        raise ShotPackageImportError("; ".join(errors))
    return warnings


def _iter_fcurves(action):
    seen = set()
    for fcurve in getattr(action, "fcurves", ()) or ():
        if id(fcurve) not in seen:
            seen.add(id(fcurve))
            yield fcurve
    for layer in getattr(action, "layers", ()) or ():
        for strip in getattr(layer, "strips", ()) or ():
            for bag in getattr(strip, "channelbags", ()) or ():
                for fcurve in getattr(bag, "fcurves", ()) or ():
                    if id(fcurve) not in seen:
                        seen.add(id(fcurve))
                        yield fcurve


def _linearize(camera) -> None:
    for owner in (camera, camera.data):
        animation = getattr(owner, "animation_data", None)
        action = getattr(animation, "action", None) if animation is not None else None
        if action is None:
            continue
        for fcurve in _iter_fcurves(action):
            for point in fcurve.keyframe_points:
                point.interpolation = "LINEAR"
            fcurve.update()


def _unique_name(collection, base: str) -> str:
    name = base
    suffix = 2
    while name in collection.objects:
        name = f"{base} ({suffix})"
        suffix += 1
    return name


def _shot_report(shot: dict, camera, frames: list[float]) -> dict:
    return {
        "id": shot["id"],
        "name": shot["name"],
        "camera": camera.name,
        "start_tick": shot["range"]["start_tick"],
        "end_tick": shot["range"]["end_tick"],
        "end_tick_exact": shot["range"]["end_tick_exact"],
        "frames": shot["camera"]["frame_count"],
        "fps": shot["camera"]["fps"],
        "width": shot["camera"]["width"],
        "height": shot["camera"]["height"],
        "scene_frames": [frames[0], frames[-1]] if frames else [],
        "keyframes": len(frames),
    }


def apply_shot_package(scene, data: dict, collection, *, scene_path=None,
                       fps: float | None = None, coordinates: dict | None = None) -> dict:
    """Create one camera per shot and make the first shot active.

    ``scene`` is a Blender scene. ``data`` is the decoded RenderLock scene
    manifest. ``collection`` is a path or an already-read collection dict.
    """
    import bpy
    from mathutils import Matrix

    if isinstance(collection, (str, Path)):
        package_path = Path(collection)
        collection = read_shot_collection(package_path)
    else:
        package_path = None
        validate_shot_collection(collection)
    if fps is None:
        base = float(scene.render.fps_base) or 1.0
        fps = float(scene.render.fps) / base
    fps = _number(fps, "scene FPS")
    if fps <= 0:
        raise ShotPackageImportError("scene FPS must be positive")
    if "scene" in collection and scene_path is not None:
        scene_path = Path(scene_path)
    warnings = validate_against_scene(collection, data, scene_path=scene_path, fps=fps)
    coordinates = scene_coordinates(data) if coordinates is None else coordinates

    for obj in list(bpy.data.objects):
        if obj.type == "CAMERA" and (
                obj.name == "AGR | overview camera" or obj.get("renderlock_shot_package")):
            bpy.data.objects.remove(obj, do_unlink=True)

    collection_root = bpy.data.collections.get("RenderLock | shot cameras")
    if collection_root is None:
        collection_root = bpy.data.collections.new("RenderLock | shot cameras")
        scene.collection.children.link(collection_root)

    report_shots, cameras, all_frames = [], [], []
    for shot in collection["shots"]:
        camera_data = bpy.data.cameras.new(f"RenderLock | {shot['name']} | lens")
        camera = bpy.data.objects.new(_unique_name(collection_root, f"RenderLock | {shot['name']}"), camera_data)
        collection_root.objects.link(camera)
        camera.rotation_mode = "XYZ"
        camera_data.sensor_fit = "HORIZONTAL"
        camera_data.sensor_width = 36.0
        camera_data.dof.use_dof = False
        first_transform = None
        frames = []
        for sample in shot["camera"]["samples"]:
            frame = scene_frame(
                shot, sample, scene_start_tick=data["start_tick"],
                tick_rate=data["tick_rate"], fps=fps)
            if frame < scene.frame_start - 1e-6 or frame > scene.frame_end + 1e-6:
                raise ShotPackageImportError(
                    f"shot {shot['id']} camera frame {frame:g} is outside the scene "
                    f"range {scene.frame_start}-{scene.frame_end}")
            transform = camera_transform(sample, {
                "unit_scale": coordinates.get("unit_scale", shot["camera"]["unit_scale"]),
                "origin": coordinates.get("origin", [0, 0, 0]),
                "world_yaw_degrees": coordinates.get("world_yaw_degrees", 0),
            })
            if first_transform is None:
                first_transform = transform
                camera_data.clip_start = transform["clip_start"]
                camera_data.clip_end = transform["clip_end"]
            camera.location = transform["location"]
            camera.rotation_euler = Matrix(transform["rotation"]).to_euler("XYZ")
            camera_data.lens = transform["lens"]
            camera.keyframe_insert(data_path="location", frame=frame)
            camera.keyframe_insert(data_path="rotation_euler", frame=frame)
            camera_data.keyframe_insert(data_path="lens", frame=frame)
            frames.append(frame)
        _linearize(camera)
        camera["renderlock_shot_package"] = True
        camera["renderlock_shot_id"] = shot["id"]
        camera["renderlock_shot_start_tick"] = shot["range"]["start_tick"]
        camera_data["renderlock_shot_id"] = shot["id"]
        cameras.append(camera)
        all_frames.extend(frames)
        report_shots.append(_shot_report(shot, camera, frames))

    if not cameras:
        raise ShotPackageImportError("shot package contains no cameras")
    first_shot = collection["shots"][0]
    scene.render.resolution_x = int(first_shot["camera"]["width"])
    scene.render.resolution_y = int(first_shot["camera"]["height"])
    scene.render.resolution_percentage = 100
    scene.camera = cameras[0]
    for shot, camera in zip(collection["shots"], cameras):
        frame = scene_frame(
            shot, shot["camera"]["samples"][0], scene_start_tick=data["start_tick"],
            tick_rate=data["tick_rate"], fps=fps)
        try:
            scene.timeline_markers.new(f"Shot: {shot['name']}", frame=round(frame))
        except Exception:
            # A duplicate marker name must not fail the camera import.
            pass

    report = {
        "mode": "shot package",
        "package": str(package_path) if package_path is not None else None,
        "camera_count": len(cameras),
        "active_camera": cameras[0].name,
        "shots": report_shots,
        "scene_range": [data["start_tick"], data["end_tick"]],
        "fps": fps,
        "warnings": list(dict.fromkeys(warnings)),
        "limits": "Embedded camera only. Effects imported after this stage use the active shot camera; "
                  "map/visibility reconstruction and transparent-layer occlusion remain separate.",
    }
    scene["renderlock_shot_package"] = json.dumps(report, sort_keys=True)
    return report


def apply_shot_package_file(scene, segment_path, package_path, *, fps: float | None = None) -> dict:
    """Convenience entrypoint for a Blender script operating on existing files."""
    segment_path = Path(segment_path)
    data = json.loads(segment_path.read_text(encoding="utf-8"))
    return apply_shot_package(scene, data, package_path, scene_path=segment_path, fps=fps)


def main(argv=None, *, blender_scene=None) -> int:
    """Apply a package to an existing scene when run inside Blender.

    ``blender_scene`` is injected by tests; it defaults to ``bpy.context.scene``.
    This entrypoint never launches Blender.
    """
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segment", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--fps", type=float)
    args = parser.parse_args(argv)
    if blender_scene is None:
        import bpy
        blender_scene = bpy.context.scene
    report = apply_shot_package_file(blender_scene, args.segment, args.package, fps=args.fps)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
