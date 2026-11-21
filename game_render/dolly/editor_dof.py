"""In-game controls for the existing authored native range DOF channels."""
from copy import deepcopy
from dataclasses import replace
import math

from .path import CvarTrack, Keyframe, TrackKey, validate_cvar_value
from .native_effects import EFFECTS, FLOAT32_MAX

# Appended action IDs 41..51; vector components remain one authored track.
CONTROLS = (
    ("r_depth_of_field", None, 1),
    ("r_dof_override", None, 0),
    *(("r_dof_override_ranges", component, 0) for component in range(4)),
    ("r_dof_override_near_blurry", None, -100),
    ("r_dof_override_near_crisp", None, 0),
    ("r_dof_override_far_crisp", None, 180),
    ("r_dof_override_far_blurry", None, 2000),
    ("r_dof_override_tilt_to_ground", None, .5),
)
ACTIONS = tuple(f"set_dof_{index}" for index in range(len(CONTROLS)))
RANGE_NAME = "r_dof_override_ranges"
DEFAULT_RANGES = (-100.0, 0.0, 180.0, 2000.0)


def _put_value(project, name, time, value):
    track = next((track for track in project.tracks if track.name == name), None)
    if track and track.keys:
        track.keys = [key for key in track.keys if key.time != time]
        track.keys.append(TrackKey(time, value))
        track.keys.sort(key=lambda key: key.time)
    else:
        project.setup_values[name] = value


def _enable_ranges(project, time, enabled):
    # An animated switch overrides setup_values, so edit its key too when present.
    cvars = _cvars_at(project, time)
    if enabled and "r_citadel_depthoffield_enable" in cvars:
        _put_value(project, "r_citadel_depthoffield_enable", time, 0.0)
    _put_value(project, "r_dof_override", time, float(enabled))
    _put_value(project, "r_depth_of_field", time,
               float(enabled or bool(cvars.get("r_citadel_depthoffield_enable", 0))))
    if not enabled:
        return  # Turning off never destroys an authored focus pull.
    values = _cvars_at(project, time).get(RANGE_NAME, (0, 0, 0, 0))
    track = next((track for track in project.tracks if track.name == RANGE_NAME), None)
    if not track or not track.keys:
        # Match the desktop preset, retaining prior fixed edits or restore metadata.
        ranges = tuple(values) if any(values) else DEFAULT_RANGES
        end = max(project.duration, time)
        end = end if end > 0 else 5.0
        if track is None:
            track = CvarTrack(RANGE_NAME, interpolation="smooth")
            project.tracks.append(track)
        track.keys = [TrackKey(0.0, ranges), TrackKey(end, ranges)]
    elif not any(values):
        # Four zeros explicitly disables range override. Seed this playhead only;
        # preserve the rest of an existing animated track and its restore value.
        _put_value(project, RANGE_NAME, time, DEFAULT_RANGES)


def values_at(project, time):
    values = _cvars_at(project, time)
    return tuple(values.get(name, default) if component is None
                 else values.get(name, (0, 0, 0, 0))[component]
                 for name, component, default in CONTROLS)


def published_values_at(project, time):
    """values_at, kept within what the in-game controls can show.

    A shot may hold values these controls cannot represent, such as a switch
    that fades between off and on. Publishing must still succeed: a refused
    block would stop the editor poll and with it every in-game action.
    """
    values = values_at(project, time)
    return tuple(float(bool(value)) if index < 2
                 else min(FLOAT32_MAX, max(-FLOAT32_MAX, value))
                 for index, value in enumerate(values))


def _cvars_at(project, time):
    view = project if project.keyframes else replace(project, keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0)])
    return view.evaluate(time)["cvars"]


def edited_project(project, time, control, value):
    if (type(control) is not int or not 0 <= control < len(CONTROLS)
            or not math.isfinite(time) or time < 0):
        raise ValueError("Invalid DOF control or shot time")
    name, component, _default = CONTROLS[control]
    _id, minimum, maximum, discrete = EFFECTS[name]
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum
            or (discrete and value != int(value))):
        raise ValueError("DOF value is outside the native control's range")
    candidate = deepcopy(project)
    if control == 0:
        _enable_ranges(candidate, time, bool(value))
        candidate.validate()
        return candidate
    if component is not None:
        previous = _cvars_at(candidate, time).get(name, (0, 0, 0, 0))
        vector = list(previous)
        vector[component] = value
        value = vector
    value = validate_cvar_value(name, value)
    if control < 2 and value not in (0, 1):
        raise ValueError("DOF switches require zero or one")
    _put_value(candidate, name, time, value)
    candidate.validate()
    return candidate


# Citadel DOF: the game's own depth of field, authored alongside the native
# engine controls. Enabling it sets both switches the engine needs.
CITADEL_ENABLE = ("r_citadel_depthoffield_enable", "r_depth_of_field")
CITADEL_SENSOR = "r_citadel_depthoffield_sensor_size"
CITADEL_FOCUS = "r_citadel_depthoffield_focus_distance"
CITADEL_ACTIONS = ("set_citadel_dof_enabled", "set_citadel_dof_sensor_size",
                   "set_citadel_dof_focus_distance")
CITADEL_SENSOR_DEFAULT = 1.0
CITADEL_FOCUS_DEFAULT = 200.0
CITADEL_APERTURE = "r_citadel_depthoffield_aperture_diameter"
CITADEL_APERTURE_DEFAULT = .5


def citadel_values_at(project, time):
    """Current Citadel DOF switch, sensor size and focus distance."""
    cvars = _cvars_at(project, time)
    enabled = all(cvars.get(name, 0) for name in CITADEL_ENABLE)
    sensor = float(cvars.get(CITADEL_SENSOR, CITADEL_SENSOR_DEFAULT))
    focus = float(cvars.get(CITADEL_FOCUS, CITADEL_FOCUS_DEFAULT))
    return bool(enabled), sensor, focus


def published_citadel_values_at(project, time):
    """citadel_values_at, kept within the in-game sliders' ranges."""
    enabled, sensor, focus = citadel_values_at(project, time)
    _id, sensor_low, sensor_high, _discrete = EFFECTS[CITADEL_SENSOR]
    _id, focus_low, focus_high, _discrete = EFFECTS[CITADEL_FOCUS]
    return (enabled, min(float(sensor_high), max(float(sensor_low), sensor)),
            min(float(focus_high), max(float(focus_low), focus)))


def edited_citadel_project(project, time, control, value):
    if (type(control) is not int or not 0 <= control < len(CITADEL_ACTIONS)
            or not math.isfinite(time) or time < 0):
        raise ValueError("Invalid Citadel DOF control or shot time")
    candidate = deepcopy(project)
    if control == 0:
        if isinstance(value, bool):
            state = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool) \
                and math.isfinite(value) and value in (0, 1):
            state = bool(value)
        else:
            raise ValueError("Citadel DOF switch must be on or off")
        cvars = _cvars_at(candidate, time)
        _put_value(candidate, CITADEL_ENABLE[0], time, float(state))
        _put_value(candidate, "r_depth_of_field", time,
                   float(state or bool(cvars.get("r_dof_override", 0))))
        if state:
            _put_value(candidate, "r_dof_override", time, 0.0)
            # The game's zero aperture default cannot produce lens blur.
            # Keep explicitly authored aperture curves, including deliberate zeros.
            if CITADEL_APERTURE not in cvars:
                _put_value(candidate, CITADEL_APERTURE, time, CITADEL_APERTURE_DEFAULT)
            if RANGE_NAME not in cvars:
                _put_value(candidate, RANGE_NAME, time, (0.0,) * 4)
    else:
        name = CITADEL_SENSOR if control == 1 else CITADEL_FOCUS
        _id, minimum, maximum, _discrete = EFFECTS[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not minimum <= value <= maximum):
            raise ValueError("Citadel DOF value is outside the native control's range")
        _put_value(candidate, name, time, validate_cvar_value(name, value))
    candidate.validate()
    return candidate
