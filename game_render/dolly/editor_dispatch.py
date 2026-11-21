"""Native editor action handlers; publication is an injected callback."""
from __future__ import annotations

from dataclasses import replace
import copy
import logging
import math
import time
from .editor_wire import ATTACH_OFFSET_LIMIT
from .native_effects import model_token
from .path import AttachKey, channel_value
from .settings import save_settings
from .video_export import BITRATE_PRESETS

LOG = logging.getLogger("dolly.editor_session")
from .editor_view import VIDEO_FPS, VIDEO_BITRATE_MBPS, CODEC_MAX


from .editor_publication import _value, visualization_session
from .editor_view import codec_label as _codec_label


def _select(app, index, *, view=True):
    if not app.project.keyframes:
        app.status_text.set("Capture a camera first.")
        return
    index = max(0, min(len(app.project.keyframes) - 1, index))
    app.camera_tree.selection_set(str(index))
    app.camera_tree.see(str(index))
    app._select_key()
    if not view:
        return
    # The selected camera changes at the current paused replay moment.
    # Reuse the paused-view controller instead of seeking to its shot time.
    key = app.project.keyframes[index]
    project = app._snapshot()
    app._submit("Viewing saved camera", lambda: app.controller.select_paused_camera(project, key.time))


def _object_picker_open(app, bridge, publish):
    """Enter the Object Picker mode; the native window draws the placed list."""
    from . import object_picker as picks
    from .editor_actions import OBJECT_PICKER_ENABLED
    if not OBJECT_PICKER_ENABLED:
        # WIP integration: refuse to open even if an event somehow arrives.
        raise ValueError("The Object Picker is not available in this build.")
    # Opening requires a live paused native view; the native side re-validates.
    app.object_picker_open = True
    app.object_picker_selected = -1
    app._native_object_cache = None
    app.status_text.set("Object Picker open. Click in the scene to place the selected object.")
    publish()


def _object_picker_cancel(app, publish):
    app.object_picker_open = False
    app.object_picker_selected = -1
    app._native_object_cache = None
    app.status_text.set("Object Picker closed.")
    publish()


def _object_place(app, event, publish):
    """Append one placed object at the event's world point."""
    from . import object_picker as picks
    pose = event.get("pose") or ()
    if len(pose) < 3 or any(not math.isfinite(pose[i]) for i in range(3)):
        raise ValueError("Object placement point is invalid")
    index = event.get("value")
    from .object_library import OBJECT_IDS
    if not math.isfinite(index) or index != int(index) or not 0 <= int(index) < len(OBJECT_IDS):
        raise ValueError("Choose an object from the library")
    library_id = OBJECT_IDS[int(index)]
    camera_yaw = (app.project.keyframes[-1].yaw if app.project.keyframes else 0.0)
    candidate = picks.place(app.project, library_id, [pose[0], pose[1], pose[2]],
                            angles=[0.0, camera_yaw, 0.0])
    app.project = candidate
    app.object_picker_selected = len(candidate.objects) - 1
    app._native_object_cache = None
    app._mark_dirty()
    app.status_text.set(f"Placed {library_id}. Click again to add another, or press Delete.")
    publish()


def _object_select(app, event, publish):
    from . import object_picker as picks
    index = event.get("value")
    if not math.isfinite(index) or index != int(index):
        raise ValueError("Object selection is invalid")
    app.object_picker_selected = picks.select(app.project, int(index))
    app._native_object_cache = None
    publish()


def _object_delete(app, event, publish):
    from . import object_picker as picks
    index = event.get("value")
    if not math.isfinite(index) or index != int(index):
        raise ValueError("Object deletion index is invalid")
    app.project = picks.remove(app.project, int(index))
    app.object_picker_selected = picks.select(app.project, int(index))
    app._native_object_cache = None
    app._mark_dirty()
    app.status_text.set("Removed the placed object.")
    publish()


def _object_transform(app, event, publish):
    """Commit one gizmo drag: value = index, pose = position[3], angles[3], scale."""
    from . import object_picker as picks
    index = event.get("value")
    pose = event.get("pose") or ()
    if not math.isfinite(index) or index != int(index):
        raise ValueError("Object transform index is invalid")
    if len(pose) < 7 or any(not math.isfinite(pose[i]) for i in range(7)):
        raise ValueError("Object transform is invalid")
    app.project = picks.set_transform(
        app.project, int(index),
        position=[pose[0], pose[1], pose[2]],
        angles=[pose[3], pose[4], pose[5]],
        scale=pose[6])
    app._native_object_cache = None
    app._mark_dirty()
    publish()


def _native_operation(app, label, function, bridge, *, complete=None):
    def run():
        try:
            return function()
        except Exception:
            # Preserve F7/ordinary game input recovery after a failed transition.
            try:
                state = bridge.editor_status()
                # Native F9 suspends input optimistically before the worker
                # runs. A failed opening must not be mistaken for confirmed
                # game-UI ownership; keep Dolly's retry controls available.
                confirmed_ui = app.controller.status().get("game_ui_visible", False)
                owner = "console" if state.get("console_open") else ("game_ui" if confirmed_ui else "panel")
                bridge.configure_editor(owner=owner)
            except (RuntimeError, ValueError, OSError):
                LOG.exception("Could not restore native input after a failed action")
            raise
    return app._submit(label, run) if complete is None else app._submit(label, run, complete)


def _pose_matches_key(app, index, pose):
    """True when the live camera is parked on the indexed saved camera."""
    try:
        key = app.project.keyframes[index]
    except (IndexError, TypeError):
        return False
    for axis, name in enumerate(("x", "y", "z", "pitch", "yaw", "roll")):
        if not math.isfinite(pose[axis]):
            return False
        tolerance = 1.0 if axis < 3 else .05
        difference = pose[axis] - channel_value(key, name)
        if axis >= 3:
            difference = (difference + 180) % 360 - 180
        if abs(difference) > tolerance:
            return False
    return True


def dispatch(app, event, bridge, *, publish):
    action = event["action"]
    previous = getattr(app, "_history_edit_group", None)
    if action == "set_camera_roll":
        # A bank drag names its camera. The first step also selects that
        # camera, so the selection cannot identify the gesture.
        app._history_edit_group = (action, event.get("value"))
    else:
        app._history_edit_group = ((action, app._selection_index(app.camera_tree),
                                    float(_value(app, "shot_time", 0) or 0))
                                   if action.startswith("set_") and action not in (
                                       "set_camera_position", "set_camera_curve_value", "set_camera_position_tangents") else None)
    try:
        return _dispatch(app, event, bridge, publish=publish)
    finally:
        app._history_edit_group = previous


def _dispatch(app, event, bridge, *, publish):
    """Dispatch one event on Tk's thread. False means leave it queued."""
    action = event["action"]
    if action == "save_shot":
        app.save()
        return True
    if action in ("preview_camera_curve_value", "preview_camera_position_tangents", "clear_camera_curve_preview"):
        from . import curve_preview
        if action == "clear_camera_curve_preview":
            curve_preview.clear_native(app, event)
        else:
            curve_preview.offer_native(app, event, bridge, tangent=action == "preview_camera_position_tangents")
        return True
    if app.busy:
        return False
    if action in ("open_bone_picker", "cancel_bone_picker", "finish_bone_picker"):
        from .bone_picker import dispatch as picker_dispatch
        return picker_dispatch(app, event, bridge, publish=publish)
    if getattr(app, "_bone_picker_context", None):
        if action in ("game_ui", "console", "stop"):
            from .bone_picker import close_picker
            close_picker(app, resume=False, publish=publish)
        else:
            raise ValueError("Finish or cancel Bone Picker before using other camera controls.")
    if (action in ("flight", "panel") and getattr(app, "preview_attach", False)
            and bridge.status().get("state") in ("fault", "stopped", "probe")):
        # Re-enter a free camera after release. Reusing a failed live attach
        # preview would immediately fault the recovery command again. The
        # authored target/bone/offsets remain intact for an explicit retry.
        app.preview_attach = False
        app._native_attach_cache = None
        publish()
    if action == "clear_camera_path":
        if app.playing:
            app.status_text.set("Stop path playback before clearing cameras.")
            publish()
            return True
        if not app._shot_edit_ready():
            # A queued confirmation can arrive after recording starts. Keep
            # its cameras and report the actual blocker without a modal.
            publish()
            return True
        history = getattr(app, "shot_history", None)
        if history is None or event["pose"][0] != history.revision:
            raise ValueError("The camera list changed. Try again.")
        candidate = copy.deepcopy(app.project)
        candidate.keyframes = []
        candidate.validate()
        app._commit_project(candidate)
        app._native_camera_page = 0
        publish()
    elif action == "reset_camera_path":
        if app.playing:
            raise ValueError("Stop path playback before resetting the camera path.")
        # The native panel confirms replacement before sending this snapshot.
        app._capture_view("start", native_snapshot=event, replacement_confirmed=True)
    elif action in ("set_camera_curve_value", "select_curve_camera"):
        if action == "set_camera_curve_value":
            from .curve_preview import finish_native
            finish_native(app)
        if _edit_camera_curve(app, event, bridge, select=action == "select_curve_camera"):
            publish()
    elif action == "set_camera_position_tangents":
        from .curve_preview import finish_native
        finish_native(app)
        if _edit_camera_position_tangents(app, event, bridge):
            publish()
    elif action == "set_camera_position":
        if _edit_camera_position(app, event, bridge):
            publish()
    elif action in ("select_camera", "view_camera", "seek_camera", "delete_camera", "undo_shot", "redo_shot",
                    "camera_page", "set_camera_time", "set_camera_roll"):
        history = getattr(app, "shot_history", None)
        revision = event["pose"][0]
        if history is None or revision != history.revision:
            raise ValueError("The camera list changed. Select the camera again.")
        if not app._shot_edit_ready():
            return True
        value = event["value"]
        if not math.isfinite(value) or value != int(value):
            raise ValueError("Invalid in-game camera selection")
        if action in ("undo_shot", "redo_shot"):
            if value != 0:
                raise ValueError("Invalid shot history action")
            (app.redo_shot if action == "redo_shot" else app.undo_shot)()
        elif action == "camera_page":
            from .editor_wire import CAMERA_LIST_COUNT
            total = len(app.project.keyframes)
            if (value < 0 or value > max(0, total - 1)
                    or value % CAMERA_LIST_COUNT and value != max(0, total - CAMERA_LIST_COUNT)):
                raise ValueError("Invalid camera list page")
            app._native_camera_page = int(value)
            publish()
        elif action in ("set_camera_time", "set_camera_roll"):
            if not 0 <= value < len(app.project.keyframes):
                raise ValueError("Selected in-game camera no longer exists")
            _edit_camera_value(app, action, int(value), event["pose"][1],
                               snap_to_tick=event["pose"][2] == 1)
            publish()
        else:
            if not 0 <= value < len(app.project.keyframes):
                raise ValueError("Selected in-game camera no longer exists")
            if action == "seek_camera":
                if app.controller.status().get("playing"):
                    raise ValueError("Pause shot playback before seeking a saved camera.")
                project = app._snapshot()
                shot_time = project.keyframes[int(value)].time
            _select(app, int(value), view=action == "view_camera")
            if action == "seek_camera":
                _native_operation(app, "Seeking saved camera",
                                  lambda: app.controller.seek(project, shot_time), bridge)
            elif action == "delete_camera":
                app._delete_key()
            publish()
    elif action in ("capture", "replace"):
        operation = "replace" if action == "replace" else ("append" if app.project.keyframes else "start")
        snapshot = dict(event)
        # New native builds put the rendered horizontal FOV in the otherwise
        # unused Capture/Replace value. Zero remains the legacy wire value.
        if event.get("value", 0) != 0:
            snapshot["horizontal_fov"] = event["value"]
        app._capture_view(operation, native_snapshot=snapshot)
    elif action == "set_framing":
        factor = event["pose"][6]
        native_index = event["value"]
        if (not math.isfinite(factor) or factor <= 0
                or not math.isfinite(native_index) or native_index != int(native_index)
                or native_index < -1):
            raise ValueError("Invalid in-game framing edit")
        # The native event can lag a selection change. The camera list is the
        # authority; fall back to the reported index only when none is selected.
        index = app._selection_index(app.camera_tree)
        if index is None:
            index = int(native_index)
        # Scroll edits the shot being framed. A saved camera is only edited
        # while the live camera is actually parked on it (the in-game previous/
        # next view or the camera list). Once the user flies away, the edit is
        # live-only and the next capture stores it.
        parked = (isinstance(index, int) and 0 <= index < len(app.project.keyframes)
                  and _pose_matches_key(app, index, event["pose"]))
        if not parked:
            app.status_text.set("Live framing updated. Capture a camera to save it.")
        else:
            keys = copy.deepcopy(app.project.keyframes)
            key = keys[index]
            value = max(.5, min(4.0, round(key.aspect_ratio * factor, 4)))
            if value != key.aspect_ratio:
                key.aspect_ratio = value
                app._commit_camera(keys, key.time)
                app.status_text.set(f"Camera {index + 1} framing set to {value:.4f}.")
    elif action.startswith("set_dof_"):
        from .editor_dof import ACTIONS, RANGE_NAME, edited_project
        if action not in ACTIONS:
            raise ValueError("Unsupported native DOF control")
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            raise ValueError("Pause shot playback before editing DOF.")
        at = float(_value(app, "shot_time", 0) or 0)
        candidate = edited_project(app.project, at, ACTIONS.index(action), event["value"])
        def complete(_result):
            app.project = candidate
            app._mark_dirty()
            selected = next((index for index, track in enumerate(candidate.tracks)
                             if track.name == RANGE_NAME), None)
            app._refresh_tracks(selected)
            app._refresh_fixed()
            publish()
        app._submit("Applying native DOF", lambda: app.controller.preview_native_effects(candidate, at), complete)
    elif action.startswith("set_citadel_dof_"):
        from .editor_dof import CITADEL_ACTIONS, edited_citadel_project
        if action not in CITADEL_ACTIONS:
            raise ValueError("Unsupported Citadel DOF control")
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            raise ValueError("Pause shot playback before editing DOF.")
        at = float(_value(app, "shot_time", 0) or 0)
        candidate = edited_citadel_project(app.project, at,
                                           CITADEL_ACTIONS.index(action), event["value"])
        def complete(_result):
            app.project = candidate
            app._mark_dirty()
            app._refresh_tracks()
            app._refresh_fixed()
            publish()
        app._submit("Applying Citadel DOF", lambda: app.controller.preview_native_effects(candidate, at), complete)
    elif action.startswith("set_confetti_"):
        value = event["value"]
        if action == "set_confetti_spawn_height":
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 100 <= value <= 1500):
                raise ValueError("Confetti spawn height must be between 100 and 1500 units.")
            changes = {"confetti_spawn_height": float(round(value))}
        else:
            if value not in (0, 1):
                raise ValueError("Confetti switch must be on or off.")
            field = ("confetti_enabled" if action == "set_confetti_enabled"
                     else "confetti_despawn_on_ground")
            changes = {field: bool(value)}
        app.project = replace(app.project, **changes)
        if hasattr(app, "confetti_enabled"):
            app.confetti_enabled.set(app.project.confetti_enabled)
            app.confetti_spawn_height.set(f"{app.project.confetti_spawn_height:g}")
            app.confetti_despawn_on_ground.set(app.project.confetti_despawn_on_ground)
        app._mark_dirty()
        app.controller.set_native_confetti(
            app.project.confetti_enabled, app.project.confetti_spawn_height,
            app.project.confetti_despawn_on_ground)
        app.status_text.set(
            (f"Confetti rain enabled at {app.project.confetti_spawn_height:g} units; "
             + ("despawns on ground." if app.project.confetti_despawn_on_ground
                else "remains on the ground."))
            if app.project.confetti_enabled else "Confetti rain disabled for this shot.")
        publish()
    elif action == "set_replay_hud":
        if event['value'] not in (0, 1):
            raise ValueError('Choose whether to show the replay HUD.')
        _native_operation(app, 'Changing replay HUD',
                          lambda: app.controller.set_replay_hud(bool(event['value'])), bridge)
    elif action == "object_picker_open":
        _object_picker_open(app, bridge, publish)
    elif action == "object_picker_cancel":
        _object_picker_cancel(app, publish)
    elif action == "object_place":
        _object_place(app, event, publish)
    elif action == "object_select":
        _object_select(app, event, publish)
    elif action == "object_delete":
        _object_delete(app, event, publish)
    elif action == "object_transform":
        _object_transform(app, event, publish)
    elif action == "framing_grid":
        settings = replace(app.app_settings,
                           framing_grid_enabled=not app.app_settings.framing_grid_enabled)
        def saved(_result):
            app.app_settings = settings
            publish()
        app.status_text.set("Framing guide " +
                            ("on." if settings.framing_grid_enabled else "off."))
        app._submit("Saving framing guide", lambda: save_settings(settings), saved)
    elif action == "set_pause_after_capture":
        value = event["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in (0, 1):
            raise ValueError("Pause after capture must be on or off.")
        settings = replace(app.app_settings, pause_after_capture=bool(value))
        def saved(_result):
            app.app_settings = settings
            app.status_text.set("Capture will pause the replay." if settings.pause_after_capture
                                else "Capture will keep the replay running.")
            publish()
        app._submit("Saving capture playback preference", lambda: save_settings(settings), saved)
    elif action == "play_pause":
        _native_operation(app, "Toggling replay playback", app.controller.toggle_replay, bridge)
    elif action == "play_path":
        app._play()
    elif action == "prepare_cinematic":
        token = event["value"]
        pose = event.get("pose") or ()
        history = getattr(app, "shot_history", None)
        if (isinstance(token, bool) or not math.isfinite(token) or token != int(token)
                or not 0 < token <= 0xffffffff or history is None or not pose
                or pose[0] != history.revision or len(app.project.keyframes) < 2
                or app.playing or getattr(app, "preview_attach", False)):
            raise ValueError("Review the current path before preparing a cinematic.")
        token, revision = int(token), history.revision
        project, snapshot = app.project, app._snapshot()
        bridge.configure_editor_cinematic(token, revision, 1)
        app._native_cinematic_request = (token, revision)

        def prepare():
            try:
                return app.controller.seek(snapshot, 0)
            except Exception:
                bridge.configure_editor_cinematic(token, revision, 3)
                raise

        def prepared(result):
            unchanged = (app.project is project and app.shot_history.revision == revision
                         and getattr(app, '_native_cinematic_request', None) == (token, revision))
            bridge.configure_editor_cinematic(token, revision, 2 if unchanged else 3)
            if unchanged:
                app._set_time(0)

        if not app._submit("Preparing cinematic", prepare, prepared):
            bridge.configure_editor_cinematic(token, revision, 3)
    elif action == "seek_replay_tick":
        value = event["value"]
        if (isinstance(value, bool) or not math.isfinite(value) or value != int(value)
                or not 0 <= value <= 2147483647):
            raise ValueError("Choose a valid replay tick")
        if getattr(app, "preview_attach", False) or app.controller.status().get("playing"):
            raise ValueError("Pause the shot and detach the camera before seeking")
        _native_operation(app, "Seeking replay tick", lambda: app.controller.seek_replay_tick(int(value)), bridge)
    elif action == "focus_replay_player":
        from .replay_player_focus import ReplayPlayerUnavailable, ReplayPlayerDeferred
        value = event['value']
        index = getattr(app, '_replay_events_cache', None)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value != int(value) or not 0 < value < 0x7fff
                or index is None or index.status != 2
                or int(value) not in {player[0] for player in index.players}
                or getattr(app, 'preview_attach', False)):
            raise ValueError('Choose an available replay player and detach the bone camera.')
        def focus():
            try:
                return app.controller.follow_replay_player(int(value))
            except (ReplayPlayerUnavailable,ReplayPlayerDeferred) as exc:
                return str(exc)

        def focused(message):
            if message:
                app.status_text.set(message)
                publish()

        _native_operation(app, 'Following replay player', focus, bridge, complete=focused)
    elif action == "seek_shot":
        value = event["value"]
        if (not math.isfinite(value) or not app.project.keyframes
                or not 0 <= value <= app.project.duration):
            raise ValueError("Choose a time within the current shot.")
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            raise ValueError("Pause shot playback before seeking an in-between view.")
        app._set_time(value)
        app._seek()
    elif action == "destroy_ragdolls":
        _native_operation(app, "Clearing ragdolls", app.controller.destroy_ragdolls, bridge)
    elif action == "toggle_citadel_glow":
        _native_operation(app, "Toggling Citadel glow", app.controller.toggle_citadel_glow, bridge)
    elif action == "toggle_healthbars":
        _native_operation(app, "Toggling health bars", app.controller.toggle_healthbars, bridge)
    elif action == "near_player_opacity_fix":
        _native_operation(app, "Toggling near-player opacity",
                          app.controller.near_player_opacity_fix, bridge)
    elif action == "start_video":
        app._start_video_recording()
    elif action == "export_renderlock":
        app._export_renderlock(destination=app._renderlock_auto_path())
    elif action == "cancel_renderlock":
        app._cancel_renderlock_export()
    elif action == "stop_video":
        app._stop_video_recording(cancel=False)
    elif action == "take_screenshot":
        from . import screenshot_ui
        screenshot_ui.start(app)
    elif action == "stop":
        preparation = getattr(app, '_native_cinematic_request', None)
        app._native_cinematic_request = None
        if preparation:
            bridge.configure_editor_cinematic(*preparation, 3)
        _native_operation(app, "Stopping and restoring",
                          lambda: app.controller.stop(preserve_speed=True), bridge)
    elif action in ("previous_view", "next_view", "select_view"):
        selected = app._selection_index(app.camera_tree)
        if action == "select_view":
            value = event["value"]
            if not math.isfinite(value) or value != int(value) or not 0 <= value < len(app.project.keyframes):
                raise ValueError("Selected in-game camera no longer exists")
            selected = int(value)
        else:
            selected = (selected if selected is not None else 0) + (-1 if action == "previous_view" else 1)
        _select(app, selected)
    elif action == "step_replay_ticks":
        if getattr(app, "preview_attach", False):
            raise ValueError("Detach the camera before stepping with a fixed view.")
        value = event["value"]
        if isinstance(value, bool) or value not in (-25, -10, -5, -2, -1, 1, 2, 5, 10, 25):
            raise ValueError("Choose a tick step of 1, 2, 5, 10 or 25.")
        project = app.project
        def complete(result):
            if app.project is project and project.keyframes:
                app._set_time(max(0, (result["tick"] - project.start_tick) / project.tick_rate))
        app._submit("Stepping replay ticks", lambda: app.controller.step_replay_ticks(int(value)), complete)
    elif action in ("seek_back", "seek_forward"):
        delta = -1.0 if action == "seek_back" else 1.0
        _native_operation(app, "Seeking replay", lambda: app.controller.seek_relative(delta, tick_rate=app.project.tick_rate), bridge)
    elif action == "console":
        _native_operation(app, "Toggling game console", lambda: app.controller.toggle_console(enabled=bool(event["value"])), bridge)
    elif action == "game_ui":
        _native_operation(app, "Switching replay UI", lambda: app.controller.toggle_game_ui(enabled=bool(event["value"])), bridge)
    elif action == "flight":
        # A failed target lookup must not remain cached across an explicit retry
        # (for example after returning from the game UI or seeking).
        app._native_attach_cache = None
        publish()
        _native_operation(app, "Entering free camera", app.controller.enter_native_flight, bridge)
    elif action == "panel":
        # Normal F8 is handled locally. From the game's own UI, first return
        # camera ownership before displaying the editor panel.
        if event["value"] == 1:
            def return_to_editor():
                # Both controller handoffs close the console themselves. Let
                # them validate readiness first so a rejected F8 keeps the
                # console and ordinary game input available for recovery.
                if (_value(app, "video_source", "Camera path") == "Player POV"
                        or getattr(app.controller, "_follow_active", False) is True):
                    app.controller.open_pov_panel()
                else:
                    app.controller.toggle_game_ui(enabled=False)
                bridge.configure_editor(owner="panel")
            _native_operation(app, "Opening in-game editor", return_to_editor, bridge)
    elif action == "start_game_follow":
        from .follow_camera import FollowSettings
        settings = FollowSettings(distance=event['value'], shoulder=event['pose'][0],
                                  height=event['pose'][1])
        settings.values()
        if getattr(app, 'preview_attach', False):
            raise ValueError('Detach the bone preview before Game Follow.')
        roster = bridge.editor_roster()
        pose = event['pose']
        index = int(pose[2])
        players = roster.get('players', []) if isinstance(roster, dict) else []
        if (pose[2] != index or not 0 <= index < len(players)
                or players[index].get('handle') != pose[3]
                or players[index].get('entity_index') != pose[4]
                or pose[5] != (players[index].get('model', 0) & 0xffffffff)
                or pose[6] != (players[index].get('model', 0) >> 32)):
            raise ValueError('The player list changed. Select the hero again.')
        player = dict(players[index])
        _native_operation(app, 'Starting Game Follow',
                          lambda: app.controller.start_selected_game_follow(settings, player), bridge)
    elif action == "stop_game_follow":
        _native_operation(app, 'Restoring Game Follow settings', app.controller.stop_game_follow, bridge)
    elif action == "set_video_source":
        if event["value"] not in (0, 1):
            raise ValueError("Unknown video camera source")
        app.video_source.set("Player POV" if event["value"] else "Camera path")
        app._video_source_changed()
    elif action == "set_pov_duration":
        value = event["value"]
        if not math.isfinite(value) or not .1 <= value <= 120:
            raise ValueError("POV duration must be between 0.1 and 120 seconds")
        app.video_pov_duration.set(str(value))
    elif action == "set_speed":
        value = event["value"]
        if not math.isfinite(value) or not 1 <= value <= 10000:
            raise ValueError("Movement speed must be between 1 and 10,000")
        settings = replace(app.app_settings, movement_speed=value)
        # Save on the worker, not on either UI/render thread.
        def saved(_result):
            app.app_settings = settings
            if hasattr(app, "editor_move_speed"):
                app.editor_move_speed.set(f"{value:g}")
            publish()
        app._submit("Saving movement speed", lambda: save_settings(settings), saved)
    elif action in ("set_playback_speed", "set_playback_rate"):
        value = event["value"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Playback option must be a finite number")
        if action == "set_playback_speed":
            if not .05 <= value <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            app.speed.set(f"{value:g}")
            # Apply immediately: the controller updates demo_timescale for the
            # running or paused replay as well as the next Play shot.
            app._submit("Applying playback speed", lambda: app.controller.set_playback_speed(value))
        else:
            # The update rate paces Dolly's monitoring thread and is fixed when
            # a shot starts; changing it mid-shot is still unsafe.
            if app.controller.status().get("playing") or getattr(app, "playing", False):
                app.status_text.set("Stop path playback before changing the update rate.")
                return True
            if value not in (30, 60, 120):
                raise ValueError("Choose a playback update rate of 30, 60, or 120.")
            app.rate.set(str(int(value)))
        publish()
    elif action in ("set_video_fps", "set_video_bitrate", "set_video_encoder"):
        value = event["value"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Export option must be a finite number")
        if value != int(value):
            raise ValueError("Export option must be a whole number")
        value = int(value)
        if action == "set_video_fps":
            if value not in VIDEO_FPS:
                raise ValueError("Video FPS must be 30, 60, 120, 300, or 600.")
            app.video_fps.set(str(value))
        elif action == "set_video_bitrate":
            if value not in VIDEO_BITRATE_MBPS:
                raise ValueError("Video bitrate must be 10, 20, or 40 Mbps.")
            label = next((name for name, bps in BITRATE_PRESETS.items()
                          if bps == value * 1_000_000), None)
            if label is None:
                raise ValueError("Video bitrate must be 10, 20, or 40 Mbps.")
            app.video_bitrate.set(label)
        else:
            if not 0 <= value <= CODEC_MAX:
                raise ValueError("Unknown video encoder.")
            app.video_codec.set(_codec_label(value))
        publish()
    elif action == "set_video_ae_camera":
        if event["value"] not in (0, 1):
            raise ValueError("After Effects camera export must be on or off.")
        app.video_ae_camera.set(bool(event["value"]))
        publish()
    elif action == "set_video_usd_camera":
        if event["value"] not in (0, 1):
            raise ValueError("USD camera export must be on or off.")
        app.video_usd_camera.set(bool(event["value"]))
        publish()
    elif action == "set_video_fixed_step":
        app.video_fixed_step.set(bool(event["value"]))
        publish()
    elif action == "set_video_depth":
        if event["value"] not in (0, 1):
            raise ValueError("Depth master must be on or off.")
        app.video_depth.set(bool(event["value"]))
        # A depth master is a paired, render-paced export. Default to the same
        # fixed-step pairing as the desktop Export tab.
        if app.video_depth.get():
            app.video_fixed_step.set(True)
        else:
            app.video_depth_exr.set(False)
        publish()
    elif action == "set_video_depth_exr":
        if event["value"] not in (0, 1):
            raise ValueError("Depth EXR sequence must be on or off.")
        app.video_depth_exr.set(bool(event["value"]) and bool(app.video_depth.get()))
        publish()
    elif action in ("set_video_layer_world", "set_video_layer_players", "set_video_layer_effects"):
        if event["value"] not in (0, 1):
            raise ValueError("Video layer must be on or off.")
        getattr(app, action.removeprefix("set_")).set(bool(event["value"]))
        app._layer_toggled()
        publish()
    elif action == "set_video_speed":
        value = event["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not .05 <= value <= 4):
            raise ValueError("Export speed must be between 0.05 and 4.")
        app.video_export_speed.set(f"{value:g}")
        publish()
    elif action in ("set_attach_target", "attach_cycle_target"):
        roster = getattr(app, "attach_roster", None)
        players = roster.get("players", []) if isinstance(roster, dict) else []
        if not players:
            app.status_text.set("No live players are available for the attach camera yet.")
            return True
        requested = int(event["value"]) if action == "set_attach_target" else -1

        def mutate_target(key):
            if action == "set_attach_target":
                if not 0 <= requested < len(players):
                    raise ValueError("The selected attach player is no longer in the roster.")
                player = players[requested]
            else:
                current = -1
                for position, candidate in enumerate(players):
                    if (isinstance(key.attach, AttachKey)
                            and candidate.get("handle") == key.attach.handle
                            and str(candidate.get("model_path") or "") == key.attach.model):
                        current = position
                        break
                player = players[(current + 1) % len(players)]
            attach = key.attach if isinstance(key.attach, AttachKey) else AttachKey()
            attach.handle = int(player.get("handle", 0))
            attach.entity_id = int(player.get("entity_index", 0) or 0)
            attach.model = str(player.get("model_path") or "")
            key.attach = attach
            key.source = "attach"

        if not app.project.keyframes:
            from .bone_picker import start_attached_view
            start_attached_view(app, mutate_target, publish=publish)
            return True
        _attach_edit(app, mutate_target)
        publish()
    elif action in ("set_attach_point", "attach_cycle_point"):
        requested = int(event["value"]) if action == "set_attach_point" else -1
        if action == "set_attach_point" and requested not in (0, 1, 2):
            raise ValueError("Unknown attach point.")

        def mutate_point(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before changing the attach point.")
            if action == "set_attach_point":
                key.attach.point = ("eyes", "weapon", "bone")[requested]
            else:
                key.attach.point = "weapon" if key.attach.point == "eyes" else "eyes"
            key.attach.bone = (key.attach.bone or "head") if key.attach.point == "bone" else ""

        _attach_edit(app, mutate_point)
        publish()
    elif action == "set_attach_bone":
        bones = bridge.editor_bones()
        index = int(event["value"])
        pose = event.get("pose") or ()
        if (not bones or not pose or pose[0] != bones["sequence"] or
                event["value"] != index or not 0 <= index < len(bones["names"])):
            raise ValueError("The bone picker changed; select the bone again.")

        def mutate_bone(key):
            attach = key.attach
            if (not isinstance(attach, AttachKey) or attach.handle != bones["handle"] or
                    attach.entity_id != bones["entity_id"] or model_token(attach.model) != bones["model"]):
                raise ValueError("The bone picker belongs to a different player.")
            attach.point = "bone"
            attach.bone = bones["names"][index]

        _attach_edit(app, mutate_bone)

    elif action == "set_attach_offsets":
        pose = event.get("pose") or []
        if len(pose) != 7 or any(not math.isfinite(component) for component in pose):
            raise ValueError("Attach offsets must be finite numbers.")
        offsets = tuple(float(component) for component in pose[:6])
        if any(abs(component) > ATTACH_OFFSET_LIMIT for component in offsets):
            raise ValueError("Attach offsets are outside the supported range.")

        def mutate_offsets(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before editing offsets.")
            key.attach.offset = offsets

        _attach_edit(app, mutate_offsets)
        publish()
    elif action == "set_attach_smoothing":
        smoothing = event["value"]
        if (isinstance(smoothing, bool) or not isinstance(smoothing, (int, float))
                or not math.isfinite(smoothing) or not 0 <= smoothing <= 5):
            raise ValueError("Attach smoothing must be between 0 and 5 seconds.")

        def mutate_smoothing(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before setting smoothing.")
            key.attach.smoothing = float(smoothing)

        _attach_edit(app, mutate_smoothing)
        publish()
    elif action == "set_attach_hide":
        if event["value"] not in (0, 1, 2, 3):
            raise ValueError("Attach visibility and clearance choice is invalid.")
        hide = bool(int(event["value"]) & 1)
        clearance = "auto" if int(event["value"]) & 2 else "exact"

        def mutate_hide(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before hiding the body.")
            key.attach.hide_body = hide
            key.attach.clearance_mode = clearance

        _attach_edit(app, mutate_hide)
        publish()
    elif action == "set_attach_vertical_aim_lock":
        if isinstance(event["value"], bool) or event["value"] not in (0, 1):
            raise ValueError("Vertical aim lock must be on or off.")
        pose = event.get("pose") or ()
        index = app._selection_index(app.camera_tree)
        history = getattr(app, "shot_history", None)
        if (len(pose) != 7 or history is None or pose[0] != history.revision or
                index is None or pose[1] != index):
            raise ValueError("The camera changed. Select it and try again.")
        if getattr(app, "_attach_snap_pending", False):
            raise ValueError("Wait for Snap to finish before changing vertical aim lock.")
        if not app._shot_edit_ready():
            return True

        def mutate_aim(key):
            if not isinstance(key.attach, AttachKey) or key.attach.point not in ("bone", "weapon"):
                raise ValueError("Choose a bone or weapon before changing vertical aim lock.")
            key.attach.lock_vertical_aim = bool(event["value"])

        _attach_edit(app, mutate_aim)
        app.status_text.set("Vertical aim lock changed. Snap again to keep the current view.")
        publish()
    elif action == "set_source_blend":
        value = float(event["value"])
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError("Source blend must be between 0 and 10 seconds")
        _attach_edit(app, lambda key: setattr(key, "source_blend", value))
        publish()
    elif action == "attach_reset":
        app.preview_attach = False
        app._attach_snap_pending = False
        app._attach_snap_started = None
        app._attach_snap_context = None

        def mutate_reset(key):
            key.source = "free"
            key.attach = None

        _attach_edit(app, mutate_reset)
        publish()
    elif action == "attach_preview":
        if event["value"] not in (0, 1):
            raise ValueError("Attach preview must be on or off.")
        if event["value"] == 1 and getattr(app, "_attach_snap_pending", False):
            raise ValueError("Wait for Snap to finish before attaching the camera.")
        if event["value"] == 1 and getattr(app.controller, "_follow_active", False) is True:
            project = app.project

            def attached_after_follow(_):
                if app.project is not project:
                    raise ValueError("The shot changed while switching from Follow. Select the camera again.")
                app.preview_attach = True
                app.status_text.set("Attach preview on. Fly with WASD/mouse to edit the offsets.")
                publish()

            app._submit("Switching Follow to attached camera",
                        lambda: app.controller.enter_native_flight(owner="panel"), attached_after_follow)
            return True
        app.preview_attach = bool(event["value"])
        app.status_text.set("Attach preview on. Fly with WASD/mouse to edit the offsets."
                            if app.preview_attach else "Attach preview off.")
        publish()
        if not app.preview_attach:
            # Clearing preview alone cannot clear the native command's latched
            # fault. Re-arm a free view, keeping the editor panel available.
            _native_operation(app, "Detaching camera",
                              lambda: app.controller.enter_native_flight(owner="panel"), bridge)
    elif action == "attach_snap":
        if getattr(app, "preview_attach", False):
            raise ValueError("Detach the camera before snapping a free-camera view.")
        index = app._selection_index(app.camera_tree)
        if index is None or not 0 <= index < len(app.project.keyframes):
            raise ValueError("Select an attached camera before snapping.")
        selected = app.project.keyframes[index]
        if selected.source != "attach" or not isinstance(selected.attach, AttachKey):
            raise ValueError("Select an attached camera before snapping.")
        app._attach_snap_context = {
            "project": app.project, "index": index, "key": copy.deepcopy(selected), "bridge": bridge,
        }
        app._attach_snap_request = int(getattr(app, "_attach_snap_request", 0)) + 1
        app._attach_snap_pending = True
        app._attach_snap_started = time.monotonic()
        app.status_text.set("Snap requested; the next free-camera frame stores its offsets.")
        publish()
    else:
        raise ValueError("The native editor requested an unsupported UI action")
    return True


def _edit_camera_position_tangents(app, event, bridge):
    """Apply one identity-bound XYZ slope gesture through normal shot history."""
    from .native_path import MAX_CAMERA_KEYS
    from .path import POSITION_CHANNELS, TANGENT_MODES

    pose, encoded = event.get("pose"), event.get("value")
    if (not isinstance(pose, (tuple, list)) or len(pose) != 7
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in pose)
            or isinstance(encoded, bool) or not isinstance(encoded, (int, float))
            or not math.isfinite(encoded) or encoded != int(encoded)
            or not 0 <= encoded < MAX_CAMERA_KEYS * 12 or pose[6] != 0
            or pose[0] != int(pose[0]) or not 1 <= pose[0] <= 0xFFFFFFFF
            or any(part != int(part) or not 0 <= part <= 0xFFFFFFFF for part in pose[4:6])):
        raise ValueError("Invalid in-game arrival/departure edit")
    index, remainder = divmod(int(encoded), 12)
    axis, mode_number = divmod(remainder, 4)
    revision, incoming, outgoing, anchor = pose[:4]
    session_id = int(pose[4]) | int(pose[5]) << 32
    history = getattr(app, "shot_history", None)
    if (history is None or revision != history.revision or not session_id
            or session_id != visualization_session(app)):
        raise ValueError("The shot changed. Select the camera again.")
    if index >= len(app.project.keyframes):
        raise ValueError("Choose a camera for its arrival/departure controls.")
    mode, name = TANGENT_MODES[mode_number], POSITION_CHANNELS[axis]
    if (mode in ("auto", "flat") and (incoming != 0 or outgoing != 0)
            or mode == "linked" and incoming != outgoing):
        raise ValueError("Auto and Flat use zero slope payloads; Linked arrival/departure slopes must match.")
    key = app.project.keyframes[index]
    if key.time != anchor:
        raise ValueError("The camera timing changed. Select the camera again.")
    if key.source != "free":
        raise ValueError("Curves edit free-camera keys. Use attachment offsets for a tracked camera.")
    if app.project.interpolation != "smooth":
        raise ValueError("Arrival/departure controls apply in Smooth mode; saved handles are preserved in other modes.")
    if not app._shot_edit_ready():
        return False
    state = bridge.editor_status()
    if (not state.get("enabled") or not state.get("ready") or not state.get("focused")
            or not state.get("paused") or state.get("input_mode") != "panel"):
        app.status_text.set("Arrival/departure edit cancelled. Pause the replay and return to Dolly's controls.")
        return False
    selected = app._selection_index(app.camera_tree)
    selected = selected if selected is not None and 0 <= selected < len(app.project.keyframes) else 0
    if selected != index:
        app.status_text.set("Arrival/departure edit cancelled because the selected camera changed.")
        return False
    candidate = copy.deepcopy(app.project)
    slopes = {} if mode in ("auto", "flat") else dict(incoming=float(incoming), outgoing=float(outgoing))
    if not candidate.set_camera_tangent(index, name, mode, **slopes):
        return False
    app._commit_project(candidate, key.time)
    app.status_text.set(f"Camera {index + 1:02d} {name.upper()} arrival/departure set to {mode.title()}. Preview to check the move.")
    return True


def _edit_camera_curve(app, event, bridge, *, select=False):
    """Commit one full-shot value gesture, with no game seek or live preview."""
    pose, index = event.get("pose"), event.get("value")
    if (not isinstance(pose, (tuple, list)) or len(pose) != 7
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in pose)
            or isinstance(index, bool) or not isinstance(index, (int, float))
            or not math.isfinite(index) or index != int(index) or pose[6] != 0
            or any(part != int(part) or not 0 <= part <= 0xFFFFFFFF for part in pose[4:6])):
        raise ValueError("Invalid in-game curve edit")
    revision, channel, value, anchor = pose[:4]
    session_id = int(pose[4]) | int(pose[5]) << 32
    history = getattr(app, "shot_history", None)
    if (history is None or revision != history.revision or not session_id
            or session_id != visualization_session(app)):
        raise ValueError("The shot changed. Select the camera again.")
    if (channel != int(channel) or not 0 <= channel <= 6
            or abs(value) > 1e8 or not 0 <= index < len(app.project.keyframes)
            or (select and (channel != 0 or value != 0))
            or (not select and channel == 6 and not .5 <= value <= 4)):
        raise ValueError("Choose a valid camera curve value (aspect ratio 0.5 to 4).")
    index, channel = int(index), int(channel)
    key = app.project.keyframes[index]
    if key.time != anchor:
        raise ValueError("The camera timing changed. Select the camera again.")
    if not select and key.source != "free":
        raise ValueError("Curves edit free-camera keys. Use attachment offsets for a tracked camera.")
    if not app._shot_edit_ready():
        return False
    state = bridge.editor_status()
    if (not state.get("enabled") or not state.get("ready") or not state.get("focused")
            or not state.get("paused") or state.get("input_mode") != "panel"):
        app.status_text.set("Curve edit cancelled. Pause the replay and return to Dolly's controls.")
        return False
    if select:
        from .editor_wire import CAMERA_LIST_COUNT
        app._native_camera_page = index // CAMERA_LIST_COUNT * CAMERA_LIST_COUNT
        _select(app, index, view=False)
        return True
    selected = app._selection_index(app.camera_tree)
    selected = selected if selected is not None and 0 <= selected < len(app.project.keyframes) else 0
    if selected != index:
        app.status_text.set("Curve edit cancelled because the selected camera changed.")
        return False
    from .path import channel_value
    from .native_path import CHANNELS
    name = CHANNELS[channel]
    if abs(channel_value(key, name)) > 1e8:
        raise ValueError("This authored value is outside the in-game curve editor range.")
    if value == channel_value(key, name):
        return False
    keys = copy.deepcopy(app.project.keyframes)
    # Rotation editing authors a curve override. Captured angles, calibrated
    # lens scale and every other camera property remain intact.
    field = "curve_" + name if 3 <= channel <= 5 else name
    setattr(keys[index], field, float(value))
    app._commit_camera(keys, key.time)
    unit = " units" if channel < 3 else (" degrees" if channel < 6 else "")
    app.status_text.set(f"Camera {index + 1:02d} {name.replace('_', ' ')} set to {value:g}{unit}. Preview to check the move.")
    return True


def _edit_camera_position(app, event, bridge):
    """Commit one revision-bound XYZ gesture; never move or seek the live camera."""
    pose = event.get("pose")
    index = event.get("value")
    if (not isinstance(pose, (tuple, list)) or len(pose) != 7
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in pose)
            or isinstance(index, bool) or not isinstance(index, (int, float))
            or not math.isfinite(index) or index != int(index) or any(pose[4:])):
        raise ValueError("Invalid in-game position edit")
    revision, axis, value, anchor = pose[:4]
    history = getattr(app, "shot_history", None)
    if history is None or revision != history.revision:
        raise ValueError("The camera list changed. Select the camera again.")
    if axis not in (0, 1, 2) or abs(value) > 1e8 or not 0 <= index < len(app.project.keyframes):
        raise ValueError("Choose a camera and a finite X, Y or Z position within 100 million units.")
    index = int(index)
    key = app.project.keyframes[index]
    if key.time != anchor:
        raise ValueError("The camera timing changed. Select the camera again.")
    if key.source != "free":
        raise ValueError("Position curves edit free-camera keys. Use attachment offsets for a tracked camera.")
    if not app._shot_edit_ready():
        return False
    state = bridge.editor_status()
    if (not state.get("enabled") or not state.get("ready") or not state.get("focused")
            or not state.get("paused") or state.get("input_mode") != "panel"):
        app.status_text.set("Position edit cancelled. Pause the replay and return to Dolly's controls.")
        return False
    selected = app._selection_index(app.camera_tree)
    # Publication presents camera zero when a newly opened tree has no row
    # selected. Match that contract while still refusing a different valid row.
    selected = selected if selected is not None and 0 <= selected < len(app.project.keyframes) else 0
    if selected != index:
        app.status_text.set("Position edit cancelled because the selected camera changed.")
        return False
    channel = ("x", "y", "z")[int(axis)]
    if value == getattr(key, channel):
        return False
    keys = copy.deepcopy(app.project.keyframes)
    setattr(keys[index], channel, float(value))
    app._commit_camera(keys, key.time)
    app.status_text.set(f"Camera {index + 1:02d} {channel.upper()} set to {value:.2f} units. Preview to check the move.")
    return True


def _edit_camera_value(app, action, index, value, *, snap_to_tick=False):
    """Apply an in-game inline Arrive/Bank edit to one camera key."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid in-game camera edit")
    keys = copy.deepcopy(app.project.keyframes)
    key = keys[index]
    if action == "set_camera_time":
        if value < 0:
            raise ValueError("Camera arrival times cannot be negative.")
        edit = float(value)
        if snap_to_tick:
            start, rate = app.project.start_tick, app.project.tick_rate
            if (isinstance(start, bool) or not isinstance(start, int) or start < 0
                    or isinstance(rate, bool) or not isinstance(rate, (int, float))
                    or not math.isfinite(rate) or rate <= 0):
                raise ValueError("The shot's replay clock is unavailable. Check the start tick and tick rate.")
            total = app.controller.status().get("total_ticks")
            maximum = total - 1 if isinstance(total, int) and not isinstance(total, bool) else 2147483647
            if maximum < start:
                raise ValueError("The shot starts beyond the end of this replay.")
            edit = min(edit, (maximum - start) / rate)
            tick = min(maximum, max(start, round(start + edit * rate)))
            occupied = {round(start + other.time * rate) for i, other in enumerate(keys) if i != index}
            if tick in occupied:
                # Preserve Dolly's forward collision policy, moving by complete
                # replay ticks. At the end of a replay, use a free tick behind it.
                forward = tick
                while forward in occupied and forward <= maximum:
                    forward += 1
                if forward <= maximum:
                    tick = forward
                else:
                    while tick in occupied and tick >= start:
                        tick -= 1
                    if tick < start:
                        raise ValueError("There is no free replay tick for this camera.")
            key.time = (tick - start) / rate
            app._commit_camera(keys, key.time)
            app.status_text.set(f"Camera {index + 1:02d} arrives at tick {tick} ({key.time:.3f} s).")
            return
        # Path times stay strictly increasing. A drag that lands exactly on
        # another camera is nudged forward instead of rejected.
        for _ in range(16):
            if not any(i != index and abs(other.time - edit) < 1e-6
                       for i, other in enumerate(keys)):
                break
            edit += 0.001
        else:
            raise ValueError("Another camera already arrives at that time.")
        key.time = edit
        app._commit_camera(keys, key.time)
        app.status_text.set(f"Camera {index + 1:02d} arrives at {key.time:.2f} s.")
    else:
        # A roll curve override drives the bank, so the edit replaces it.
        setattr(key, "roll" if key.curve_roll is None else "curve_roll", float(value))
        app._commit_camera(keys, key.time)
        app.status_text.set(f"Camera {index + 1:02d} bank set to {float(value):.1f}°.")


def _attach_edit(app, mutate):
    """Apply an in-game attach edit to the selected camera key."""
    index = app._selection_index(app.camera_tree)
    if index is None or not 0 <= index < len(app.project.keyframes):
        app.status_text.set("Select a camera view before editing the attach camera.")
        return
    keys = copy.deepcopy(app.project.keyframes)
    key = keys[index]
    mutate(key)
    app._commit_camera(keys, key.time)


