"""Worker-only no-video capture. Existing VideoExport owns native/timing cleanup.

The final .crav is published only after native completion, exact session/replay
ownership, timing restoration and replay byte identity have all been checked.
"""
from __future__ import annotations

import math
from pathlib import Path
import tempfile
import time

from .camera_export import sample_camera_data
from .crav_export import build_crav, export_crav, validate_crav_project
from .crav_replay import pin_replay
from .path import Project
from .video_export import ACTIVE_STATES, VideoOptions


def capture_to_crav(controller, recorder, project, destination, *, fps=60,
                    cancelled=lambda: False, timeout=120.0) -> dict:
    """Run on Dolly's operation worker, never on Tk or a polling UI callback."""
    project = Project.from_dict(project.to_dict())
    validate_crav_project(project)
    destination = Path(destination).expanduser().absolute()
    if (destination.suffix.lower() != ".crav" or not destination.parent.is_dir()
            or destination.exists() or destination.is_symlink()):
        raise ValueError("Choose a new .crav file in an existing folder")
    if type(fps) is not int or fps not in (30, 60, 120, 300, 600):
        raise ValueError("Choose 30, 60, 120, 300 or 600 capture FPS")
    frames = math.floor(project.duration * fps + 1e-9) + 1
    if not 1 <= frames <= 65535 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Camera capture exceeds the supported frame or timeout budget")
    session, replay = controller._session, controller._demo
    if session is None or replay is None:
        raise RuntimeError("Launch and connect a selected local replay before exporting")

    def check():
        if cancelled():
            raise RuntimeError("RenderLock camera export cancelled")
        if controller._session is not session or controller._demo != replay:
            raise RuntimeError("Editing session or selected replay changed during capture")

    before = pin_replay(replay, check_cancelled=check)
    if before["tick_rate"] != project.tick_rate:
        raise ValueError("Shot and selected replay tick rates disagree")
    if project.start_tick + (frames - 1) / fps * project.tick_rate >= before["total_ticks"]:
        raise ValueError("The camera shot extends outside the selected replay")
    scratch = Path(tempfile.mkdtemp(prefix=".crav-capture-", dir=destination.parent))
    trace = scratch / "camera.json"
    attempted = False
    completed = False
    try:
        check()
        with controller._op_lock:
            check()
            attempted = True
            recorder.start(VideoOptions(trace, fps=fps, fixed_step=True, shot_only=True,
                                        camera_track=True, camera_only=True, max_frames=frames),
                           project=project)
        deadline = time.monotonic() + timeout
        while recorder.status().get("state") == "starting":
            check()
            if time.monotonic() >= deadline:
                raise RuntimeError("Camera-only capture did not become ready within its budget")
            time.sleep(.02)
        if recorder.status().get("state") != "recording":
            raise RuntimeError("Native camera-only capture could not start")
        with controller._op_lock:
            check()
            controller.play(project, speed=1, rate=60, frozen=False)
        while True:
            check()
            status = recorder.status()
            if status.get("state") not in ACTIVE_STATES:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Camera-only capture exceeded its bounded time budget")
            time.sleep(.02)
        if status.get("state") != "completed":
            detail = status.get("error") or "Native camera-only capture was incomplete"
            raise RuntimeError(f"{detail} (captured {status.get('frames_written', 0)} of {frames} frames)")
        check()
        # Both operations retain the existing session/replay restoration guards.
        with controller._op_lock:
            check()
            recorder.stop(timeout=10)
            controller.stop()
        check()
        after = pin_replay(replay, check_cancelled=check)
        take = sample_camera_data(trace, fps, expected_status=status)
        if take.video_frames != frames:
            raise ValueError("Camera capture did not cover the complete requested shot")
        document = build_crav(project, take, fps=fps, replay_before=before, replay_after=after)
        # Commit point: late cancellation cannot retract a completed publication.
        with controller._op_lock:
            check()
            export_crav(document, destination)
        completed = True
        return dict(path=str(destination), frames=frames, bytes=destination.stat().st_size,
                    replay_sha256=before["sha256"], fps=fps, no_video=True)
    finally:
        # Never restore or control another session. Uncertain cleanup retains
        # scratch evidence and the existing recorder's pending reservation.
        if attempted and not completed:
            with controller._op_lock:
                if controller._session is session and controller._demo == replay:
                    try:
                        recorder.stop(cancel=True, timeout=10)
                    finally:
                        controller.stop()
        if completed:
            try:
                trace.unlink(missing_ok=True)
                scratch.rmdir()
            except OSError:
                pass
