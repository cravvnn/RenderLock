"""Replay camera command text and status parsing shared by the controller.

Pure helpers moved out of the controller so camera command construction and
spec_pos/status parsing have a leaf owner. The controller re-exports them for
existing callers and tests.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import time

from .convar_response import NUMBER
from .path import (Project, Keyframe, validate_cvar_name, STANDARD_ASPECT, ASPECT_MIN, ASPECT_MAX,
                   validate_cvar_value, format_cvar_value)

ASPECT_CVAR = "r_aspectratio"
LEGACY_FOV_CONTROLS = {"citadel_camera_fov", "citadel_camera_spectator_fov"}
DOF_RANGES = {
    "r_citadel_depthoffield_enable": (0, 1),
    "r_citadel_depthoffield_aperture_diameter": (0, 3),
    "r_citadel_depthoffield_focus_distance": (0, 10000),
    "r_citadel_depthoffield_mode": (0, 2),
    "r_citadel_depthoffield_sensor_size": (0.5, 3),
}
DISCRETE = {"r_citadel_depthoffield_enable", "r_citadel_depthoffield_mode",
            "r_depth_of_field", "r_dof_override", "cl_lock_camera"}


def motion_clock_info():
    # Python 3.12's Windows monotonic clock can advance in 15/16 ms steps.
    # All motion timestamps use the same high-resolution QPC-backed domain.
    info = time.get_clock_info("perf_counter")
    return {"name": "perf_counter", "implementation": info.implementation,
            "resolution_seconds": info.resolution, "monotonic": info.monotonic}


def numeric(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("A camera value is not finite.")
    return format(value, ".9g")


def tick_rates_match(left, right, tolerance=.02):
    """True when two replay tick rates describe the same clock.

    Replay files are recorded at different rates (32 and 64 are both live
    today), so a project's ticks/second must match the loaded replay before a
    tick is converted to an authored shot second. The tolerance absorbs the
    rounding in engine status text; half a tick is always accepted.
    """
    try:
        left, right = float(left), float(right)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(left) or not math.isfinite(right) or left <= 0 or right <= 0:
        return False
    return abs(left - right) <= max(.5, tolerance * max(left, right))


def tick_rate_advice(replay_rate, project_rate):
    """Explain a replay/project tick-rate mismatch and how to repair it."""
    return (f"This replay runs at {float(replay_rate):.3g} ticks/second, but the shot uses "
            f"{float(project_rate):.3g}. Replay-timed cameras would arrive at the wrong replay "
            "moments. Retime the shot to this replay (Shot settings) or set Ticks/second to "
            "match before adding or replacing cameras.")


def _tail_file(path, limit=2_000_000):
    with Path(path).open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit)


def parse_camera(output, fov=90.0, roll=0.0, *, aspect_ratio=STANDARD_ASPECT):
    """spec_pos produces spec_goto X Y Z pitch yaw; getpos has a second form."""
    match = re.search(r"\bspec_goto\s+" + r"\s+".join([f"({NUMBER})"] * 5), output)
    if match:
        xyzpy = [float(v) for v in match.groups()]
        frame = Keyframe(0.0, *xyzpy, float(roll), float(fov), aspect_ratio=float(aspect_ratio))
        Project(keyframes=[frame]).validate()
        return frame
    match = re.search(r"\bsetpos(?:_exact)?\s+" + r"\s+".join([f"({NUMBER})"] * 3)
                      + r"\s*;\s*setang(?:_exact)?\s+" + r"\s+".join([f"({NUMBER})"] * 3), output)
    if match:
        frame = Keyframe(0.0, *[float(v) for v in match.groups()], float(fov), aspect_ratio=float(aspect_ratio))
        Project(keyframes=[frame]).validate()
        return frame
    raise ValueError("The spectator camera position could not be read. Enter replay freecam, then export diagnostics if Capture still fails.")


def parse_camera_readback(output, *, roll, aspect_ratio):
    """Accept a printed position, never the outgoing multi-command echo."""
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    pattern = (r"^[ \t]*(?:\[[^\]\r\n]*\][ \t]*)*spec_goto[ \t]+"
               + r"[ \t]+".join([f"({NUMBER})"] * 5) + r"[ \t\r]*$")
    matches = list(re.finditer(pattern, clean, re.M))
    if not matches:
        raise ValueError("The camera readback did not contain a printed spec_pos position. Movement stopped; export diagnostics.")
    return parse_camera("spec_goto " + " ".join(matches[-1].groups()),
                        roll=roll, aspect_ratio=aspect_ratio)


def frame_commands(frame, lens_cvar=ASPECT_CVAR):
    if lens_cvar != ASPECT_CVAR:
        raise ValueError("Framing uses r_aspectratio. Degree-based FOV controls are no longer supported.")
    aspect = float(frame["aspect_ratio"])
    if not math.isfinite(aspect) or not ASPECT_MIN <= aspect <= ASPECT_MAX:
        raise ValueError(f"Aspect ratio must be between {ASPECT_MIN:g} and {ASPECT_MAX:g}. These are the editor's framing limits.")
    commands = ["spec_goto " + " ".join(numeric(frame[k]) for k in ("x", "y", "z", "pitch", "yaw")),
                "cl_citadel_forceangles " + " ".join(numeric(frame[k]) for k in ("pitch", "yaw", "roll")),
                ASPECT_CVAR + " " + numeric(aspect)]
    cvars = frame.get("cvars", {})
    for name, value in sorted(cvars.items()):
        validate_cvar_name(name)
        value = validate_cvar_value(name, value)
        if (name == "r_dof_override_ranges" and cvars.get("r_citadel_depthoffield_enable", 0)
                and cvars.get("r_dof_override") == 0):
            value = (0.0,) * 4  # Suppress the override without changing authored curves.
        if name == ASPECT_CVAR or name in LEGACY_FOV_CONTROLS:
            raise ValueError("Use the Framing curve for aspect ratio. Legacy FOV and duplicate aspect tracks are disabled.")
        if name in DOF_RANGES:
            lo, hi = DOF_RANGES[name]
            if not lo <= float(value) <= hi:
                raise ValueError(f"{name} must be between {lo:g} and {hi:g}.")
        if name in DISCRETE and float(value) != int(float(value)):
            raise ValueError(f"{name} needs whole-number values and a Step track.")
        commands.append(name + " " + format_cvar_value(value))
    return "; ".join(commands)
