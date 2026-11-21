"""Transactional desktop entry/exit for the native in-scene Bone Picker."""
from copy import deepcopy
import time
import math

from .native_effects import model_token
from .path import AttachKey


def open_picker(app, *, publish):
    if app.busy or app.playing:
        raise ValueError("Finish the current operation before opening Bone Picker.")
    if getattr(app, "_bone_picker_context", None):
        return
    bridge = app.controller._native_bridge()
    if bridge is None or not getattr(app, "attach_fields", None):
        raise ValueError("Bone Picker requires a connected Native replay and a selected player.")
    index = app._selection_index(app.camera_tree)
    if index is None or not 0 <= index < len(app.project.keyframes):
        raise ValueError("Select a camera and a player before opening Bone Picker.")
    key = app.project.keyframes[index]
    if key.source != "attach" or not isinstance(key.attach, AttachKey):
        raise ValueError("Choose an attached player before opening Bone Picker.")
    status = bridge.editor_status()
    from_follow = getattr(app.controller, "_follow_active", False) is True
    if not status.get("ready") or (not status.get("flight_active") and not from_follow):
        raise ValueError("Enter the Native camera editor before opening Bone Picker.")
    context = dict(index=index, key=deepcopy(key), bridge=bridge,
                   preview=bool(getattr(app, "preview_attach", False)),
                   resume=not status.get("paused"), request=None)

    def start():
        if app.controller._recorder_active():
            raise ValueError("Finish recording before opening Bone Picker.")
        if from_follow:
            app.controller.enter_native_flight(owner="panel")
        if context["resume"]:
            app.controller.toggle_replay()
        bridge.configure_editor(owner="panel")

    def ready(_):
        if not still_current(app, context):
            if context["resume"]:
                app._submit("Resuming replay after cancelled Bone Picker", app.controller.toggle_replay)
            raise ValueError("The selected camera changed while opening Bone Picker.")
        app._bone_picker_context = context
        app.status_text.set("Bone Picker is open in the game. Choose a joint, then preview its attached view.")
        publish()
        context["request"] = bridge._editor_attach_sequence

    app._submit("Opening Bone Picker", start, ready)


def still_current(app, context):
    index = context["index"]
    return (app.controller._native_bridge() is context["bridge"] and
            app._selection_index(app.camera_tree) == index and
            0 <= index < len(app.project.keyframes) and
            app.project.keyframes[index] == context["key"])


def close_picker(app, *, publish, result=None, resume=True):
    context = getattr(app, "_bone_picker_context", None)
    if not context:
        return
    if result is not None:
        if not still_current(app, context):
            raise ValueError("The selected camera changed; this bone selection was discarded.")
        attach = context["key"].attach
        if (result.get("request") != context["request"] or not result.get("finishing") or
                not result.get("ready") or not result.get("name") or
                result.get("handle") != attach.handle or result.get("entity_id") != attach.entity_id or
                result.get("model") != model_token(attach.model)):
            raise ValueError("The selected player's bone data changed; reopen Bone Picker.")
        keys = deepcopy(app.project.keyframes)
        selected = keys[context["index"]]
        selected.attach.point = "bone"
        selected.attach.bone = result["name"]
        # Clear the transaction before _commit_camera refreshes the UI/config.
        app._bone_picker_context = None
        app.preview_attach = True
        app._commit_camera(keys, selected.time)
        app.status_text.set("Attached preview: " + result["name"] + ". Reopen Bone Picker to change it.")
    else:
        app._bone_picker_context = None
        app.preview_attach = context["preview"] if still_current(app, context) else False
        app.status_text.set("Bone Picker cancelled. Your saved camera is unchanged.")
    publish()
    if context["resume"] and resume:
        app._submit("Resuming replay after Bone Picker", app.controller.toggle_replay)


def dispatch(app, event, bridge, *, publish):
    action = event["action"]
    if action == "open_bone_picker":
        open_picker(app, publish=publish)
    elif action == "cancel_bone_picker":
        close_picker(app, publish=publish)
    else:
        context = getattr(app, "_bone_picker_context", None)
        if not context:
            raise ValueError("This Bone Picker session has already ended.")
        from .native_bridge import NativeBridgeError
        try:
            result = bridge.editor_bone_picker()
        except NativeBridgeError as exc:
            if "updating" not in str(exc):
                close_picker(app, resume=False, publish=publish)
                raise
            result = None
        pose = event.get("pose") or ()
        if not pose or pose[0] != context["request"]:
            raise ValueError("This bone selection belongs to an older picker session.")
        # The native worker publishes the result asynchronously after Present.
        if not result or result.get("request") != pose[0] or not result.get("finishing"):
            if time.monotonic() - context.setdefault("finish_wait", time.monotonic()) > 5:
                close_picker(app, resume=False, publish=publish)
                raise ValueError("Bone Picker did not finish responding. Reopen it and try again.")
            return False
        if result["selected"] != event["value"]:
            raise ValueError("The Bone Picker selection changed before it was applied.")
        try:
            close_picker(app, result=result, publish=publish)
        except ValueError:
            close_picker(app, resume=False, publish=publish)
            raise
    return True


def start_attached_view(app, mutate, *, publish):
    """Create the first attached view through the normal paused capture gate."""
    if app.busy or app.playing or app.project.keyframes:
        raise ValueError("Finish the current operation before starting a bone camera.")
    original = app.project
    bridge = app.controller._native_bridge()
    if bridge is None or not getattr(app, "attach_fields", None):
        raise ValueError("Bone cameras require a connected Native replay.")
    candidate = deepcopy(original)
    rate = app._resolve_replay_tick_rate(candidate, adopt=True)

    def capture():
        # Follow owns the game's camera rather than native flight. Transfer
        # ownership through the normal restoration gate before capturing the
        # first bone camera, so opening its picker has an active native view.
        if getattr(app.controller, "_follow_active", False) is True:
            app.controller.enter_native_flight(owner="panel")
        key = app.controller.capture_at_replay(None, rate)
        tick = app.controller.status().get("tick")
        if tick is None:
            raise ValueError("The replay start tick is unavailable. Retry while paused.")
        return key, tick

    def ready(result):
        if app.project is not original or app.controller._native_bridge() is not bridge:
            raise ValueError("The replay or shot changed while starting the bone camera.")
        key, tick = result
        mutate(key)
        candidate.keyframes = [key]
        candidate.start_tick = int(tick)
        candidate.tick_rate = rate
        candidate.validate()
        app.project = candidate
        app.start_tick.set(str(candidate.start_tick))
        app.tick_rate.set(str(rate))
        app._mark_dirty()
        app._refresh_keys(key.time)
        app._set_time(key.time)
        open_picker(app, publish=publish)

    app._submit("Starting bone camera", capture, ready)


def timed_attachment(project, duration):
    """Build a separate timed take; the single authored view stays unchanged."""
    if len(project.keyframes) != 1 or project.keyframes[0].source != "attach":
        raise ValueError("Choose a single attached camera first.")
    duration = float(duration)
    if not math.isfinite(duration) or not .1 <= duration <= 120:
        raise ValueError("Choose a bone camera duration between 0.1 and 120 replay seconds.")
    candidate = deepcopy(project)
    first = candidate.keyframes[0]
    first.time = 0.0
    end = deepcopy(first)
    end.time = duration
    candidate.keyframes.append(end)
    candidate.validate()
    return candidate
