"""Latest-only, render-only curve drafts; saved shots and camera control stay separate."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import logging
import math
import secrets
import time

from . import visualization_preview_wire as wire
from .native_path import CHANNELS, MAX_CAMERA_KEYS
from .path import CURVE_CHANNELS, POSITION_CHANNELS, TANGENT_MODES, Project
from .video_export import ACTIVE_STATES
from .visualization_wire import build_visualization

LOG = logging.getLogger("dolly.curve_preview")
CADENCE = .100
LEASE = .750


@dataclass
class _Draft:
    source: int
    token: int
    project: Project
    original: Project
    snapshot: Project
    session: int
    revision: int
    index: int
    channel: int
    key_time: float
    gesture: object = None

    def identity(self):
        return dict(source=self.source, token=self.token, revision=self.revision,
                    session_id=self.session, selected_camera=self.index,
                    channel=self.channel, key_time=self.key_time)


class _State:
    def __init__(self):
        self.draft = None
        self.bridge = None
        self.active = False
        self.published = False
        self.compiled = self.compiled_project = None
        self.last_sent = -math.inf
        self.last_native_offer = -math.inf
        self.desktop_gesture = None
        self.desktop_token = secrets.randbits(32) or 1
        self.native_session = self.native_token = 0
        self.native_terminal = False
        self.last_identity = None
        self.desktop_terminal = None


def _state(app):
    state = getattr(app, "_curve_preview_state", None)
    if state is None:
        state = app._curve_preview_state = _State()
    return state


def _identity(app):
    history = getattr(app, "shot_history", None)
    session, revision = getattr(history, "session_id", 0), getattr(history, "revision", 0)
    if (isinstance(session, bool) or not isinstance(session, int) or not 0 < session < 1 << 64
            or isinstance(revision, bool) or not isinstance(revision, int) or not 0 < revision < 1 << 32):
        return None
    return session, revision


def _selected(app):
    index = app._selection_index(app.camera_tree)
    return index if isinstance(index, int) and not isinstance(index, bool) else 0


def _base_current(app, draft):
    project = app.project
    return (_identity(app) == (draft.session, draft.revision)
            and project is draft.original and project == draft.snapshot
            and 0 <= draft.index < len(project.keyframes) <= MAX_CAMERA_KEYS
            and _selected(app) == draft.index
            and project.keyframes[draft.index].time == draft.key_time
            and project.keyframes[draft.index].source == "free"
            and len(draft.project.keyframes) == len(project.keyframes)
            and all(left.time == right.time for left, right in zip(project.keyframes, draft.project.keyframes)))


def _admitted(app, bridge, source):
    if (getattr(app, "closed", False) or getattr(app, "busy", False)
            or getattr(app, "playing", False) or getattr(app, "preview_attach", False)
            or getattr(app, "_pending_capture", None) or getattr(app, "_bone_picker_context", None)
            or getattr(app, "object_picker_open", False)
            or getattr(app, "_layer_queue", None) or getattr(app, "_active_layer_take", None)):
        return False
    video = getattr(app, "video_export", None)
    if video is not None and video.status().get("state") in ACTIVE_STATES:
        return False
    status = bridge.editor_status()
    if not (status.get("enabled") and status.get("ready") and status.get("paused")
            and status.get("flight_active")):
        return False
    owner = status.get("input_mode")
    if source == wire.NATIVE:
        return bool(status.get("focused") and owner == "panel")
    return ((status.get("focused") and owner in ("panel", "flight"))
            or (not status.get("focused") and owner == "unfocused"))


def _clear(state, *, terminal=True):
    draft = state.draft
    identity = draft.identity() if draft is not None else state.last_identity
    if terminal:
        if state.desktop_gesture is not None:
            state.desktop_terminal = state.desktop_gesture
            try:
                state.desktop_gesture._curve_preview_retired = True
            except (AttributeError, TypeError):
                pass
        state.desktop_gesture = None
        if (identity is not None and identity["source"] == wire.NATIVE
                and identity["session_id"] == state.native_session and identity["token"] == state.native_token):
            state.native_terminal = True
    if state.bridge is not None and (state.published or terminal and identity is not None):
        try:
            # Empty clear is recoverable within a gesture. Identity-bearing
            # clear is terminal, including after an earlier invalid candidate.
            state.bridge.publish_visualization_preview(None, **(identity if terminal and identity else {}))
        except (RuntimeError, ValueError, OSError):
            LOG.warning("Could not clear optional curve preview", exc_info=True)
    state.draft = None
    state.published = False
    state.compiled = state.compiled_project = None
    if terminal:
        state.last_identity = None


def clear(app):
    """Terminal clear, also safe when the game or input owner has disappeared."""
    state = getattr(app, "_curve_preview_state", None)
    if state is not None:
        _clear(state)


def offer_desktop(app, project, gesture=None):
    """Accept a workspace draft without committing or publishing controls.

    None/gesture temporarily clears an invalid candidate; None/None retires the
    gesture. The workspace emits terminal clear before its ordinary commit.
    """
    state = _state(app)
    if project is None:
        if ((gesture is None and (state.desktop_gesture is not None
                                  or state.last_identity is not None and state.last_identity["source"] == wire.DESKTOP))
                or gesture is not None and state.desktop_gesture is gesture):
            _clear(state, terminal=gesture is None)
        return False
    identity = _identity(app)
    if (not isinstance(project, Project) or gesture is None or identity is None
            or getattr(gesture, "context", None) != identity
            or getattr(gesture, "channel", None) not in CHANNELS
            or not isinstance(getattr(gesture, "index", None), int)
            or isinstance(gesture.index, bool)
            or gesture is state.desktop_terminal or getattr(gesture, "_curve_preview_retired", False)):
        if state.desktop_gesture is gesture and gesture is not None:
            _clear(state)
        return False
    draft = _Draft(wire.DESKTOP, state.desktop_token, project, gesture.project,
                   gesture.snapshot, *identity, gesture.index, CHANNELS.index(gesture.channel),
                   gesture.time, gesture)
    if not _base_current(app, draft):
        if state.desktop_gesture is gesture:
            _clear(state)
        return False
    if state.desktop_gesture is not gesture:
        _clear(state)
        state.desktop_gesture = gesture
        state.desktop_token = (state.desktop_token+1) & 0xFFFFFFFF or 1
        draft.token = state.desktop_token
    state.draft = draft
    state.last_identity = draft.identity()
    if state.active and state.bridge is not None:
        configure(app, state.bridge, active=True, selected=draft.index)
    return True


def _event(app, event, *, tangent=False, clearing=False):
    pose, value = event.get("pose"), event.get("value")
    if (not isinstance(pose, (list, tuple)) or len(pose) != 7
            or any(isinstance(part, bool) or not isinstance(part, (int, float))
                   or not math.isfinite(part) for part in pose)
            or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value != int(value)
            or not 0 <= value < MAX_CAMERA_KEYS*(12 if tangent else 1)
            or any(pose[i] != int(pose[i]) or not 0 <= pose[i] <= 0xFFFFFFFF for i in (0, 4, 5, 6))
            or not pose[0] or not pose[6]):
        raise ValueError("Invalid render-only curve preview request")
    session = int(pose[4]) | int(pose[5]) << 32
    if _identity(app) != (session, int(pose[0])):
        return None
    index, axis, mode = int(value), 0, 0
    if tangent:
        index, remainder = divmod(index, 12)
        axis, mode = divmod(remainder, 4)
    elif not clearing:
        if pose[1] != int(pose[1]) or not 0 <= pose[1] <= 6:
            raise ValueError("Invalid curve preview channel")
        axis = int(pose[1])
    if (not index < len(app.project.keyframes) or app.project.keyframes[index].time != pose[3]):
        return None
    if clearing and (pose[1] or pose[2]):
        raise ValueError("Invalid terminal curve preview request")
    return session, int(pose[0]), int(pose[6]), index, axis, mode, pose


def _native_token(state, session, token, *, terminal=False):
    # Native tokens are a global monotonically advancing u32 gesture counter.
    # Serial arithmetic handles wrap while rejecting late terminal tokens.
    if state.native_session != session:
        state.native_session, state.native_token, state.native_terminal = session, 0, False
    if state.native_token:
        distance = (token-state.native_token) & 0xFFFFFFFF
        if token != state.native_token and not 0 < distance < 0x80000000:
            return False
        if token == state.native_token and state.native_terminal and not terminal:
            return False
    if token != state.native_token:
        state.native_token, state.native_terminal = token, False
    if terminal:
        state.native_terminal = True
    return True


def offer_native(app, event, bridge, *, tangent=False):
    """Apply a non-authoring native request only to a private candidate."""
    parsed = _event(app, event, tangent=tangent)
    if parsed is None:
        return False
    session, revision, token, index, channel, mode, pose = parsed
    state = _state(app)
    if not _native_token(state, session, token):
        return False
    try:
        admitted = _admitted(app, bridge, wire.NATIVE)
    except (RuntimeError, ValueError, OSError):
        admitted = False
    if (index != _selected(app) or app.project.keyframes[index].source != "free" or not admitted):
        if state.last_identity is not None and state.last_identity["source"] == wire.NATIVE:
            _clear(state)
        state.native_terminal = True
        return False
    candidate = copy.deepcopy(app.project)
    try:
        if tangent:
            if candidate.interpolation != "smooth":
                if state.last_identity is not None and state.last_identity["source"] == wire.NATIVE:
                    _clear(state)
                return False
            kwargs = {} if mode < 2 else dict(incoming=pose[1], outgoing=pose[2])
            if (mode < 2 and (pose[1] or pose[2])) or (mode == 2 and pose[1] != pose[2]):
                raise ValueError("Invalid preview arrival/departure pair")
            candidate.set_camera_tangent(index, POSITION_CHANNELS[channel], TANGENT_MODES[mode], **kwargs)
        else:
            from .path import channel_value
            value = pose[2]
            if (abs(value) > 1e8 or channel == 6 and not .5 <= value <= 4
                    or abs(channel_value(app.project.keyframes[index], CHANNELS[channel])) > 1e8):
                raise ValueError("Invalid curve preview value")
            name = CHANNELS[channel]
            setattr(candidate.keyframes[index], "curve_"+name if name in CURVE_CHANNELS else name, float(value))
            candidate.validate()
    except (ValueError, TypeError, ArithmeticError):
        if state.last_identity is not None and state.last_identity["source"] == wire.NATIVE:
            _clear(state, terminal=False)
        return False
    if state.last_identity is not None and (state.last_identity["source"] != wire.NATIVE
                                           or state.last_identity["token"] != token):
        _clear(state)
        state.native_terminal = False
    state.last_identity = dict(source=wire.NATIVE, token=token, revision=revision,
                               session_id=session, selected_camera=index,
                               channel=channel, key_time=pose[3])
    snapshot = state.draft.snapshot if state.draft is not None else copy.deepcopy(app.project)
    state.draft = _Draft(wire.NATIVE, token, candidate, app.project, snapshot,
                         session, revision, index, channel, pose[3])
    state.last_native_offer = time.monotonic()
    configure(app, bridge, active=True, selected=index)
    return True


def clear_native(app, event):
    parsed = _event(app, event, clearing=True)
    if parsed is None:
        return False
    session, _revision, token, _index, _channel, _mode, _pose = parsed
    state = _state(app)
    if not _native_token(state, session, token, terminal=True):
        return False
    if (state.last_identity is not None and state.last_identity["source"] == wire.NATIVE
            and state.last_identity["token"] == token):
        _clear(state)
    return True


def finish_native(app):
    """Existing committed edit actions terminate their current preview first."""
    state = getattr(app, "_curve_preview_state", None)
    if state is not None and state.last_identity is not None and state.last_identity["source"] == wire.NATIVE:
        _clear(state)


def configure(app, bridge, *, active, selected):
    """Flush at most one latest candidate per100ms; unchanged bytes heartbeat."""
    state = _state(app)
    if state.bridge is not None and state.bridge is not bridge:
        _clear(state)
        state.native_session = state.native_token = 0
    state.bridge, state.active = bridge, active
    draft = state.draft
    if draft is None:
        if state.last_identity is not None:
            try:
                if not active or not _admitted(app, bridge, state.last_identity["source"]):
                    _clear(state)
            except (RuntimeError, ValueError, OSError):
                _clear(state)
        return
    now = time.monotonic()
    try:
        valid = (active and selected == draft.index and _base_current(app, draft)
                 and _admitted(app, bridge, draft.source)
                 and (draft.source != wire.NATIVE or now-state.last_native_offer < LEASE))
        if not valid:
            _clear(state)
            return
        if now-state.last_sent < CADENCE:
            return
        if state.compiled_project != draft.project:
            state.compiled = build_visualization(draft.project, selected_camera=draft.index,
                revision=draft.revision, session_id=draft.session)
            state.compiled_project = copy.deepcopy(draft.project)
        publish = getattr(bridge, "publish_visualization_preview", None)
        if not callable(publish) or publish(state.compiled, **draft.identity()) is False:
            _clear(state, terminal=False)
            return
        state.last_sent, state.published = now, True
    except (RuntimeError, ValueError, TypeError, ArithmeticError, OSError):
        _clear(state, terminal=False)
        LOG.warning("Optional curve draft was refused", exc_info=True)
