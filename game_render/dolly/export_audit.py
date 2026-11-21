"""Offline, read-only validation of a completed take folder.

The first user's depth failure had a distinct signature: a take that reported
complete while its depth stream contained no frames ("0 frames, 1363 skipped,
scene result=missing why=depth function=ALWAYS"). A support bundle can be
judged from its own metadata and decoded audits without relaunching the game.

Usage::

    python -m dolly.export_audit "path/to/label/paired"

The exit code is 0 when no finding was produced and 1 otherwise. Nothing is
written or modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dolly.camera_bundle import (
    CameraBundleError,
    load_bundle,
    validate_against_manifest,
    validate_bundle,
)

SIDECAR_FORMAT = "deadlock-dolly-shot"
SIDECAR_VERSION = 1
_LAYERS = ("depth", "world", "players")


def _load(path: Path):
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
            return None
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _audit_sidecar(label: str, sidecar: Path, video_dir: Path, result: dict) -> None:
    data = _load(sidecar)
    if not isinstance(data, dict):
        result["findings"].append(f"{label}: missing or unreadable shot.json")
        return
    frames = data.get("frames_written")
    problems = []
    if data.get("format") != SIDECAR_FORMAT or data.get("version") != SIDECAR_VERSION:
        problems.append("unrecognized sidecar format")
    if data.get("fixed_step") is not True:
        problems.append("not a fixed-step take")
    if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
        problems.append("zero-frame take (frames_written is not positive)")
        frames = None
    elif data.get("first_frame") != 0 or data.get("last_frame") != frames - 1:
        problems.append("frame range does not match frames_written")
    video = data.get("video_file")
    if not isinstance(video, str) or not video:
        problems.append("video_file is missing or invalid")
    elif not (video_dir / video).is_file():
        problems.append(f"referenced video is missing: {video}")
    elif (video_dir / video).stat().st_size == 0:
        problems.append(f"referenced video is empty: {video}")
    take = result["takes"].setdefault(label, {})
    take["frames_written"] = frames
    take.setdefault("problems", []).extend(problems)
    result["findings"].extend(f"{label}: {problem}" for problem in problems)


def _audit_camera(layer: str, folder: Path, manifest, result: dict) -> None:
    """Check camera.json when present; takes from before the bundle simply have none."""
    camera = folder / "camera.json"
    if not camera.is_file():
        return
    try:
        bundle = load_bundle(camera)
    except CameraBundleError as exc:
        result["findings"].append(f"{layer}: camera bundle is unreadable: {exc}")
        return
    take = result["takes"].setdefault(layer, {})
    take["camera_frames"] = bundle.frames
    problems = validate_bundle(bundle, require_complete=True)
    if isinstance(manifest, dict):
        problems.extend(validate_against_manifest(bundle, manifest))
    if problems:
        take.setdefault("problems", []).extend(f"camera bundle: {problem}"
                                               for problem in problems)
        result["findings"].extend(f"{layer}: camera bundle: {problem}" for problem in problems)


def _audit_layer(layer: str, folder: Path, result: dict) -> None:
    sidecar = folder / "shot.json"
    manifest = folder / "manifest.json"
    manifest_data = None
    if sidecar.is_file():
        _audit_sidecar(layer, sidecar, folder, result)
    if manifest.is_file():
        data = _load(manifest)
        if not isinstance(data, dict):
            result["findings"].append(f"{layer}: unreadable manifest.json")
        else:
            manifest_data = data
            frames = data.get("frames")
            problems = []
            if data.get("complete") is not True:
                problems.append("manifest is not marked complete")
            if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
                problems.append("zero frames in manifest (missing/ALWAYS depth signature)")
                frames = None
            master = data.get("master")
            if isinstance(master, str) and master and not (folder / master).is_file():
                problems.append(f"master is missing: {master}")
            take = result["takes"].setdefault(layer, {})
            take["manifest_frames"] = frames
            take.setdefault("problems", []).extend(problems)
            result["findings"].extend(f"{layer}: {problem}" for problem in problems)
    _audit_camera(layer, folder, manifest_data, result)
    videos = [path for path in (folder / f"{layer}.mov", folder / f"{layer}.mp4",
                                folder / f"{layer}.mkv") if path.is_file()]
    if not sidecar.is_file() and not manifest.is_file() and not videos:
        result["findings"].append(f"{layer}: folder has no sidecar, manifest or video")
    elif videos and all(path.stat().st_size == 0 for path in videos):
        result["findings"].append(f"{layer}: video is empty")


def _audit_decoded(root: Path, result: dict) -> None:
    for candidate in (root / "decoded-audit.json", root.parent / "decoded-audit.json"):
        data = _load(candidate)
        if not isinstance(data, dict):
            continue
        streams = data.get("streams")
        if not isinstance(streams, dict):
            result["findings"].append("decoded audit: no stream data")
            return
        for name in ("color", "depth"):
            stream = streams.get(name)
            if not isinstance(stream, dict):
                if name == "depth":
                    result["findings"].append("decoded audit: depth stream is missing")
                continue
            frames = stream.get("decoded_frames")
            if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
                result["findings"].append(
                    "decoded audit: depth stream has zero decoded frames (missing/ALWAYS signature)"
                    if name == "depth" else "decoded audit: color stream has zero or invalid decoded frames")
        color, depth = streams.get("color"), streams.get("depth")
        if (isinstance(color, dict) and isinstance(depth, dict)
                and color.get("timestamps") and color.get("timestamps") != depth.get("timestamps")):
            result["findings"].append("decoded audit: depth timestamps do not match color")
        for name in ("color", "depth"):
            stream = streams.get(name)
            if isinstance(stream, dict):
                count = stream.get("decoded_frames")
                if type(count) is int and count > 0:
                    result["takes"].setdefault(name, {})["decoded_frames"] = count
        return


def _audit_frame_counts(result):
    """Paired color/depth evidence describes the same encoded frame sequence."""
    counts = []
    for name in ("color", "depth"):
        take = result["takes"].get(name, {})
        for field in ("frames_written", "manifest_frames", "camera_frames", "decoded_frames"):
            count = take.get(field)
            if type(count) is int and count > 0:
                counts.append((name + " " + field, count))
    if counts:
        reference, expected = counts[0]
        for label, count in counts[1:]:
            if count != expected:
                result["findings"].append(
                    f"paired frame count mismatch: {reference}={expected}, {label}={count}")


def audit_take(folder) -> dict:
    """Return ``{folder, takes, findings, ok}`` for a take folder; file errors become findings."""
    folder = Path(folder)
    result: dict = {"folder": str(folder), "takes": {}, "findings": [], "ok": False}
    try:
        if not folder.is_dir():
            result["findings"].append("take folder does not exist")
            return result
        if (folder / "shot.json").is_file():
            _audit_sidecar("color", folder / "shot.json", folder, result)
        elif not any((folder / layer).is_dir() for layer in _LAYERS):
            result["findings"].append("no color shot.json and no layer folders")
        for layer in _LAYERS:
            if (folder / layer).is_dir():
                _audit_layer(layer, folder / layer, result)
        _audit_decoded(folder, result)
        _audit_frame_counts(result)
    except OSError as exc:
        result["findings"].append(f"take audit could not read files: {exc}")
    result["ok"] = not result["findings"]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit a completed Dolly take folder (read-only).")
    parser.add_argument("folder")
    args = parser.parse_args(argv)
    result = audit_take(args.folder)
    print(json.dumps(result, indent=2))
    for finding in result["findings"]:
        print("FINDING: " + finding, file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
