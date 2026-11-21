"""Lightweight RenderLock producer; no authored-camera fallback or media copies.

Capture owners must supply replay identity pinned BEFORE sampling and rechecked
afterwards. This module cannot establish that an arbitrary old camera sidecar
belongs to a replay. The direct camera-only capture owner is a separate stage.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from .camera_export import Take, angle_vectors, write_script
from .crav_contract import MAX_BYTES, validate_shot_collection
from .path import Project


def validate_crav_project(project: Project) -> None:
    """Reject unsupported scene intent before capture changes game state."""
    project.validate()
    unsupported = set(project.setup_values) - {"r_aspectratio"}
    unsupported.update(track.name for track in project.tracks if track.name != "r_aspectratio")
    if unsupported or project.objects or project.confetti_enabled:
        raise ValueError("The shot contains unsupported RenderLock scene/lens overrides")


def build_crav(project: Project, take: Take, *, fps: float,
               replay_before: dict, replay_after: dict,
               shot_id: str = "shot-1", margin_source_units: float = 128) -> dict:
    """Pure conversion of a completed, identity-bound recorded take.

    Both replay dictionaries contain path, sha256, build, map, tick_rate and
    total_ticks; they come from the capture owner, not a user-selected pairing.
    Source-pose arrays remain absolute. Neither input is modified.
    """
    validate_crav_project(project)
    if replay_before != replay_after:
        raise ValueError("Replay identity changed during camera capture")
    if not isinstance(replay_before, dict):
        raise ValueError("Capture must pin a replay identity")
    if replay_before.get("tick_rate") != project.tick_rate:
        raise ValueError("Project and recorded replay tick rates disagree")
    # Camera framing already lives in recorded samples. No arbitrary cvars or
    # authored scene edits can be silently lost by this first profile.
    if take.source not in ("native-view-hook", "renderer-bundle"):
        raise ValueError("RenderLock export requires recorded cameras, not authored keys")
    if take.fixed_step is not True or take.warnings:
        raise ValueError("RenderLock export requires a complete fixed-step take without camera warnings")
    if (not take.size or len(take.size) != 2
            or any(type(value) is not int or not 1 <= value <= 32768 for value in take.size)
            or take.video_frames != len(take.frames) or not take.frames):
        raise ValueError("Incomplete recorded camera take")
    samples = []
    for frame in take.frames:
        if frame.video_time is not None and abs(frame.video_time - frame.time) > 1e-6:
            raise ValueError("Recorded frame timing disagrees with output timing")
        if frame.basis is None:
            forward, right, up = angle_vectors(*frame.angles)
        else:
            if len(frame.basis) != 9:
                raise ValueError("Invalid recorded camera basis")
            right, up = frame.basis[:3], frame.basis[3:6]
            forward = tuple(-x for x in frame.basis[6:])
        if frame.projection is not None:
            # Avoid silently losing asymmetric/jittered projection. A qualified
            # direct capture can initially emit the ordinary FOV-only form.
            raise ValueError("Projection-bearing takes need qualified projection support")
        samples.append(dict(frame=frame.frame, video_time=frame.time,
                            replay_time=frame.phase, position=list(frame.position),
                            right=list(right), up=list(up), forward=list(forward),
                            horizontal_fov=frame.fov, aspect=take.size[0] / take.size[1],
                            state="complete"))
    end = project.start_tick + samples[-1]["replay_time"] * project.tick_rate
    document = dict(
        format="renderlock-shot-collection", version=1, profile="replay-reconstruction-v1",
        created_with={"name": "Deadlock Dolly"}, replay=copy.deepcopy(replay_before),
        reconstruction={"map_selection": {"mode": "camera-path",
                        "margin_source_units": margin_source_units,
                        "include_lighting_dependencies": True}, "missing_content": "error"},
        shots=[dict(id=shot_id, name=project.name,
                    range=dict(start_tick=project.start_tick, end_tick=math.floor(end + 1e-6), end_tick_exact=end),
                    camera=dict(source={"kind": "dolly-recorded-camera", "version": 1},
                                space="source-world", unit_scale=.0254, time_origin="shot-start",
                                fps=fps, width=take.size[0], height=take.size[1],
                                frame_count=len(samples), samples=samples))])
    validate_shot_collection(document)
    return document


def export_crav(document: dict, destination: str | Path) -> None:
    """Validate then publish through Dolly's existing no-overwrite file owner."""
    destination = Path(destination)
    if destination.suffix.lower() != ".crav":
        raise ValueError("RenderLock export destination must end in .crav")
    if document.get("profile") != "replay-reconstruction-v1":
        raise ValueError("RenderLock export requires the lightweight reconstruction profile")
    validate_shot_collection(document)
    encoded = json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
    if len(encoded) > MAX_BYTES:
        raise ValueError("RenderLock camera file exceeds the size limit")
    write_script(destination, encoded)
