"""Coordinate native-editor publication, dispatch and ordered acknowledgements."""
import logging
import time
from .native_bridge import NativeBridgeError, NativeStatusBusy
from . import editor_publication, editor_dispatch
from .editor_publication import (_bridge, _value, _playback_values, _video_values,
                                 _pov_duration, _attach_state, _configure_visualization)
from .editor_view import VIDEO_FPS, VIDEO_BITRATE_MBPS, CODEC_MAX, codec_label as _codec_label

LOG = logging.getLogger(__name__)


def configure(app):
    return editor_publication.configure(app)


def dispatch(app, event, bridge):
    return editor_dispatch.dispatch(app, event, bridge, publish=lambda: configure(app))


def _dispatch(app, event, bridge):
    return editor_dispatch._dispatch(app, event, bridge, publish=lambda: configure(app))


def snap_is_current(app, bridge):
    context = getattr(app, "_attach_snap_context", None)
    if not context or context["bridge"] is not bridge or context["project"] is not app.project:
        return False
    index = context["index"]
    return (app._selection_index(app.camera_tree) == index and
            0 <= index < len(app.project.keyframes) and
            app.project.keyframes[index] == context["key"])


def cancel_snap(app, message):
    app._attach_snap_pending = False
    app._attach_snap_started = None
    app._attach_snap_context = None
    if message:
        app.status_text.set(message)


def poll(app):
    if getattr(app, "closed", False):
        return
    bridge = _bridge(app)
    if bridge is None:
        app.native_editor_active = False
        cancel_snap(app, None)
        app._native_editor_bridge = None
        app._native_visualization_bridge = None
        app._native_visualization_cache = None
        return
    try:
        if getattr(app, "_attach_snap_pending", False) and not snap_is_current(app, bridge):
            cancel_snap(app, "Snap cancelled because the selected camera changed.")
        # Flight can fail before the controller marks the editor active even
        # though DX11/input finish initializing afterwards. Keep that live
        # panel's console/stop/retry actions connected to the launcher. Do not
        # interfere with the startup worker while it is still arming flight.
        if (not getattr(app, "native_editor_active", False) and not app.busy
                and app.controller.status().get("connected")):
            recovery = bridge.editor_status()
            if recovery.get("enabled"):
                app.native_editor_active = True
                if hasattr(app, "_disable_external_input"):
                    app._disable_external_input()
        configure(app)
        if not getattr(app, "native_editor_active", False):
            return
        # The attach target picker refreshes from the native roster at a low
        # rate; a stale or unavailable block must never break camera control.
        roster_now = time.monotonic()
        if roster_now - getattr(app, "_attach_roster_at", 0.0) >= 2.0:
            app._attach_roster_at = roster_now
            roster = None
            busy = False
            reader = getattr(bridge, "editor_roster", None)
            if callable(reader):
                try:
                    roster = reader()
                except NativeStatusBusy:
                    busy = True  # Keep the current picker list until the next refresh.
                except (NativeBridgeError, ValueError, OSError):
                    roster = None
            if not busy:
                app.attach_roster = roster
                handler = getattr(app, "_attach_roster_changed", None)
                if callable(handler):
                    handler(roster)
            bones = None
            reader = getattr(bridge, "editor_bones", None)
            if callable(reader):
                try:
                    bones = reader()
                except (NativeBridgeError, ValueError, OSError):
                    pass
            app.attach_bones = bones
            handler = getattr(app, "_attach_bones_changed", None)
            if callable(handler):
                handler(bones)
        # Native snap / attached-fly offsets feed the selected attach key.
        result_reader = getattr(bridge, "editor_attach_result", None)
        if callable(result_reader):
            attach_result = None
            try:
                attach_result = result_reader()
            except (NativeBridgeError, ValueError, OSError):
                attach_result = None
            if attach_result and attach_result.get("valid") and not getattr(app, "_bone_picker_context", None):
                sequence = attach_result.get("sequence")
                identity = (bridge, sequence)
                if sequence and identity != getattr(app, "_attach_result_sequence", None):
                    app._attach_result_sequence = identity
                    handler = getattr(app, "_attach_result_changed", None)
                    if callable(handler):
                        handler(attach_result)
        snap_started = getattr(app, "_attach_snap_started", None)
        if (getattr(app, "_attach_snap_pending", False) and snap_started is not None
                and time.monotonic() - snap_started > 5):
            cancel_snap(app, "Snap did not finish. Pause, check the player, and try again.")
        status = bridge.editor_status()
        app._native_editor_status = status
        if status.get("dropped_events", 0) > getattr(app, "_native_editor_dropped", 0):
            app._native_editor_dropped = status["dropped_events"]
            app.status_text.set("The editor was busy; an in-game action was ignored. Retry the action.")
        for event in status.get("events", ()):
            if app.busy and event.get("action") != "save_shot":
                break
            try:
                accepted = dispatch(app, event, bridge)
            except (RuntimeError, ValueError, OSError) as exc:
                LOG.exception("Native editor action failed: %s", event["action"])
                app._error("In-game editor", exc)
                accepted = True  # Do not replay a failed action on every tick.
            if not accepted:
                break
            bridge.acknowledge_editor_event(event["sequence"])
    except NativeBridgeError as exc:
        if "being updated" in str(exc):
            return
        if str(exc) != getattr(app, "_native_editor_error", None):
            app._native_editor_error = str(exc)
            LOG.error("Native editor: %s", exc)
            app.status_text.set(str(exc))
    except (ValueError, OSError) as exc:
        if str(exc) != getattr(app, "_native_editor_error", None):
            app._native_editor_error = str(exc)
            LOG.exception("Native editor configuration failed")
            app.status_text.set(str(exc))


def close(app):
    app._bone_picker_context = None
    bridge = _bridge(app)
    if bridge is not None:
        try:
            bridge.configure_editor(enabled=False, owner="disabled")
        except (NativeBridgeError, OSError, ValueError):
            pass
    app.native_editor_active = False
    app._native_editor_config_cache = None
    app._native_visualization_bridge = None
    app._native_visualization_cache = None
