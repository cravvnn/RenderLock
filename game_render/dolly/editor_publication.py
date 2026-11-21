"""Read desktop state and publish cached native-editor blocks in wire order."""
from __future__ import annotations

import logging
import secrets

LOG = logging.getLogger("dolly.editor_session")


from . import editor_view


def _bridge(app):
    if app.controller.status().get('game_running') is False:
        return None
    return app.controller._native_bridge()


def _value(app, name, default=None):
    value = getattr(app, name, default)
    return value.get() if hasattr(value, "get") else value


def _control(app, name):
    try:
        return _value(app, name, editor_view.MISSING)
    except Exception as exc:
        # Preserve the normalizer's original per-field exception policy.
        return editor_view.ReadError(exc)


def _playback_values(app):
    controls = editor_view.EditorControls(
        speed=_control(app, "speed"),
        rate=_control(app, "rate"),
    )
    return editor_view.playback_values(controls, getattr(app, "_native_editor_config_cache", None))


def _video_values(app):
    controls = editor_view.EditorControls(
        video_fps=_control(app, "video_fps"),
        video_bitrate=_control(app, "video_bitrate"),
        video_codec=_control(app, "video_codec"),
        video_fixed_step=_control(app, "video_fixed_step"),
        video_depth=_control(app, "video_depth"),
        video_depth_exr=_control(app, "video_depth_exr"),
        video_export_speed=_control(app, "video_export_speed"),
    )
    return editor_view.video_values(controls, getattr(app, "_native_editor_config_cache", None))


def _pov_duration(app):
    controls = editor_view.EditorControls(
        video_pov_duration=_control(app, "video_pov_duration"),
    )
    return editor_view.pov_duration(controls, getattr(app, "_native_editor_config_cache", None))


def _attach_state(app, selected, count):
    return editor_view.attach_state(app.project, getattr(app, "attach_roster", None), selected, count)


def visualization_session(app):
    """Stable opened-shot identity, renewed by ShotHistory.reset."""
    history = getattr(app, "shot_history", None)
    session_id = getattr(history, "session_id", None)
    if isinstance(session_id, int) and not isinstance(session_id, bool) and 0 < session_id <= 0xFFFFFFFFFFFFFFFF:
        return session_id
    # A missing journal means there can be no editable native curve. Keep
    # read-only publications identified without retaining a mutable project.
    if not getattr(app, "_native_curve_readonly_session", None):
        app._native_curve_readonly_session = secrets.randbits(64) or 1
    return app._native_curve_readonly_session


def _configure_visualization(app, bridge, active, selected):
    publish = getattr(bridge, "publish_visualization", None)
    if publish is None:
        return
    project = app.project
    revision = getattr(getattr(app, "shot_history", None), "revision", 0)
    session_id = visualization_session(app)
    # Keep primitives, not mutable Keyframe objects: replacing an in-place
    # camera must invalidate the published guides. The native path limit also
    # bounds work on the Tk thread for oversized, console-only projects.
    from .native_path import CHANNELS, MAX_CAMERA_KEYS
    from .path import channel_value
    keys = (tuple((key.time, key.source, key.lens_scale, *(channel_value(key, name) for name in CHANNELS),
                   tuple((name, tangent.mode, tangent.incoming, tangent.outgoing)
                         for name, tangent in sorted(key.tangents.items())))
                  for key in project.keyframes)
            if len(project.keyframes) <= MAX_CAMERA_KEYS else len(project.keyframes))
    signature = (active, selected, revision, session_id, project.interpolation, project.rotation_mode,
                 project.lens_interpolation, project.standard_aspect, project.duration, keys)
    if (getattr(app, "_native_visualization_bridge", None) is bridge
            and getattr(app, "_native_visualization_cache", None) == signature):
        return
    # Remember failures too. Guides are optional; a malformed/oversized shot
    # must not retry compilation or flood logs on every editor poll.
    app._native_visualization_bridge = bridge
    app._native_visualization_cache = signature
    try:
        if publish(project, enabled=active, selected_camera=selected, revision=revision,
                   session_id=session_id) is False:
            details = bridge.visualization_diagnostics()
            LOG.warning("Path guides unavailable: %s", details.get("error") or "publication failed")
    except (RuntimeError, ValueError, OSError) as exc:
        LOG.warning("Path guides unavailable: %s", exc)


def configure(app):
    if getattr(app, "_refreshing_shot_history", False):
        return
    bridge = _bridge(app)
    if bridge is None or getattr(app, "closed", False):
        return
    status = app.controller.status()
    was_active = bool(getattr(app, "native_editor_active", False))
    active = bool(was_active or status.get("native_editor_active"))
    active = active and bool(status.get("connected"))
    # start_flight enables native input before its acknowledgement reaches the
    # controller. Do not turn it off while the startup worker is still arming.
    if not active and not was_active:
        return
    if not active:
        app.native_editor_active = False
    if active and not getattr(app, "native_editor_active", False):
        app.native_editor_active = True
        if hasattr(app, "_disable_external_input"):
            app._disable_external_input()
    settings = app.app_settings
    count = len(app.project.keyframes)
    selected = app._selection_index(app.camera_tree)
    selected = selected if selected is not None and 0 <= selected < count else 0
    playhead = float(_value(app, "shot_time", 0) or 0)
    playback_speed, playback_rate = _playback_values(app)
    video_fps, video_bitrate, video_encoder, video_fixed_step, video_speed, video_depth, video_depth_exr = _video_values(app)
    values = editor_view.editor_values(
        active=active, settings=settings, project=app.project, selected=selected,
        count=count, playhead=playhead, status=status, busy=app.busy,
        playback=(playback_speed, playback_rate),
        video=(video_fps, video_bitrate, video_encoder, video_fixed_step, video_speed, video_depth, video_depth_exr),
        message=str(_value(app, "status_text", "")),
        video_pov=_value(app, "video_source", "Camera path") == "Player POV",
        pov_duration=_pov_duration(app),
        layers={layer: _value(app, "video_layer_" + layer, False) for layer in ("world", "players", "effects")},
        video_ae_camera=bool(_value(app, "video_ae_camera", False)),
        video_usd_camera=bool(_value(app, "video_usd_camera", False)),
        renderlock_exporting=bool(getattr(app, "_renderlock_cancel", None) is not None and app.busy),
    )
    # A UI refresh must not overwrite an owner chosen by F7/F8/F9 in-game.
    # Explicit owner changes are handled only by command transitions below.
    if getattr(app, "_native_editor_config_cache", None) != values or getattr(app, "_native_editor_bridge", None) is not bridge:
        bridge.configure_editor(**values)
        app._native_editor_config_cache = values
        app._native_editor_bridge = bridge
    _configure_visualization(app, bridge, active, selected)
    from . import curve_preview
    curve_preview.configure(app, bridge, active=active, selected=selected)
    publish_cameras = getattr(bridge, "configure_editor_cameras", None)
    history = getattr(app, "shot_history", None)
    if callable(publish_cameras) and history is not None:
        from .editor_wire import CAMERA_LIST_COUNT
        first = getattr(app, "_native_camera_page", 0)
        first = min(first, max(0, count - CAMERA_LIST_COUNT))
        if first % CAMERA_LIST_COUNT:
            first = max(0, count - CAMERA_LIST_COUNT)
        if selected != getattr(app, "_native_camera_selected", None) and not first <= selected < first + CAMERA_LIST_COUNT:
            first = min(selected // CAMERA_LIST_COUNT * CAMERA_LIST_COUNT, max(0, count - CAMERA_LIST_COUNT))
        app._native_camera_selected = selected
        app._native_camera_page = first
        camera_values = (history.revision, first, history.can_undo, history.can_redo)
        if (getattr(app, "_native_cameras_bridge", None) is not bridge
                or getattr(app, "_native_cameras_cache", None) != camera_values):
            publish_cameras(app.project, history.revision, first,
                            can_undo=history.can_undo, can_redo=history.can_redo)
            app._native_cameras_bridge, app._native_cameras_cache = bridge, camera_values
    publish_lite = getattr(bridge, "configure_lite_replay", None)
    if callable(publish_lite) and history is not None:
        signature = (history.revision, app._native_camera_page, app.project.start_tick,
                     app.project.tick_rate, status.get("total_ticks"))
        if getattr(app, "_lite_replay_cache", None) != signature or getattr(app, "_lite_replay_bridge", None) is not bridge:
            publish_lite(app.project, history.revision, app._native_camera_page, status.get("total_ticks"))
            app._lite_replay_cache, app._lite_replay_bridge = signature, bridge
    publish_grid = getattr(bridge, "configure_framing_grid", None)
    publish_events = getattr(bridge, 'configure_replay_events', None)
    if callable(publish_events) and active:
        from .replay_events import ReplayEventReader
        reader = getattr(app, '_replay_event_reader', None)
        if reader is None:
            reader = app._replay_event_reader = ReplayEventReader()
        index = reader.snapshot(getattr(app.controller, '_demo', None))
        if (getattr(app, '_replay_events_cache', None) != index
                or getattr(app, '_replay_events_bridge', None) is not bridge):
            publish_events(index)
            app._replay_events_cache, app._replay_events_bridge = index, bridge
    if callable(publish_grid):
        grid_binding = settings.action_bindings.get("framing_grid")
        grid = (active, bool(settings.framing_grid_enabled), grid_binding)
        if (getattr(app, "_native_grid_bridge", None) is not bridge
                or getattr(app, "_native_grid_cache", None) != grid):
            publish_grid(enabled=active and grid[1], binding=grid_binding)
            app._native_grid_bridge, app._native_grid_cache = bridge, grid
    publish_level = getattr(bridge, "configure_level_view", None)
    if callable(publish_level):
        level = (active, settings.action_bindings.get("level_view"),
                 round(float(getattr(app.project, "standard_aspect", 0.0) or 0.0), 6))
        if (getattr(app, "_native_level_bridge", None) is not bridge
                or getattr(app, "_native_level_cache", None) != level):
            publish_level(binding=level[1] if active else None,
                          standard_aspect=level[2] if active else None)
            app._native_level_bridge, app._native_level_cache = bridge, level
    publish_dof = getattr(bridge, "configure_editor_dof", None)
    if callable(publish_dof):
        from .editor_dof import published_values_at
        dof = (active, published_values_at(app.project, max(0.0, playhead)))
        if (getattr(app, "_native_dof_bridge", None) is not bridge
                or getattr(app, "_native_dof_cache", None) != dof):
            publish_dof(dof[1], enabled=active)
            app._native_dof_bridge, app._native_dof_cache = bridge, dof
    publish_citadel = getattr(bridge, "configure_editor_citadel_dof", None)
    if callable(publish_citadel):
        from .editor_dof import published_citadel_values_at
        enabled, sensor, focus = published_citadel_values_at(app.project, max(0.0, playhead))
        citadel = (active, enabled, round(sensor, 6), round(focus, 6))
        if (getattr(app, "_native_citadel_dof_bridge", None) is not bridge
                or getattr(app, "_native_citadel_dof_cache", None) != citadel):
            publish_citadel(sensor, focus, available=active, enabled=active and enabled)
            app._native_citadel_dof_bridge, app._native_citadel_dof_cache = bridge, citadel
    publish_objects = getattr(bridge, "configure_object_picker", None)
    if callable(publish_objects) and active:
        from .editor_actions import OBJECT_PICKER_ENABLED
        objects = list(getattr(app.project, "objects", None) or [])
        # WIP integration: never let the mode open or a binding reach native
        # while disabled, so its key cannot fire in a published build.
        open_ = OBJECT_PICKER_ENABLED and bool(getattr(app, "object_picker_open", False))
        payload = (tuple((o.library_id, o.model, tuple(o.position), tuple(o.angles), o.scale)
                         for o in objects), open_, int(getattr(app, "object_picker_selected", -1)))
        if (getattr(app, "_native_object_bridge", None) is not bridge
                or getattr(app, "_native_object_cache", None) != payload):
            binding = settings.action_bindings.get("object_picker") if OBJECT_PICKER_ENABLED else None
            publish_objects(objects, active=open_, selected=payload[2], binding=binding)
            app._native_object_bridge, app._native_object_cache = bridge, payload
    publish_follow = getattr(bridge, "configure_editor_follow", None)
    if callable(publish_follow):
        from .follow_camera import FollowSettings
        from .replay_camera import CLIENT_SHA256
        settings = getattr(app.controller, '_follow_settings', None)
        if not isinstance(settings, FollowSettings):
            settings = FollowSettings()
        evidence = getattr(app.controller, '_startup_evidence', {})
        identity = evidence.get('replay_camera_identity', {})
        follow_available = active and identity.get('client_sha256') == CLIENT_SHA256
        capability_available = getattr(bridge, 'capability_available', None)
        if follow_available and callable(capability_available):
            follow_available = capability_available('follow')
        transaction = getattr(app.controller, '_follow_transaction', None)
        pending = bool((transaction is not None and transaction.originals)
                       or getattr(app.controller, '_follow_mode_original', None) is not None)
        following = getattr(app.controller, '_follow_active', False) is True
        show_hud = getattr(app.controller, '_show_replay_hud', False) is True
        follow = (settings, bool(follow_available), following, pending, show_hud)
        if (getattr(app, '_native_follow_bridge', None) is not bridge
                or getattr(app, '_native_follow_cache', None) != follow):
            publish_follow(settings, available=bool(follow_available), active=following, pending=pending, show_hud=show_hud)
            app._native_follow_bridge, app._native_follow_cache = bridge, follow
    publish_attach = getattr(bridge, "configure_editor_attach", None)
    fields = getattr(app, "attach_fields", None)
    if callable(publish_attach) and fields and active:
        picker_context = getattr(app, "_bone_picker_context", None)
        if picker_context:
            from .bone_picker import still_current
            if not still_current(app, picker_context):
                app._bone_picker_context = None
                picker_context = None
                app.preview_attach = False
        preview = bool(getattr(app, "preview_attach", False))
        snap_request = int(getattr(app, "_attach_snap_request", 0))
        attach_state = _attach_state(app, selected, count)
        if picker_context:
            # Roster refreshes may reorder UI rows while the exact handle/model
            # stays unchanged. Keep this transaction's wire request stable;
            # native sampling still revalidates entity/model ownership.
            attach_state = picker_context.setdefault("attach_state", attach_state)
        attach = (tuple(sorted(fields.items())), attach_state, preview,
                  snap_request, bool(picker_context))
        if (getattr(app, "_native_attach_bridge", None) is not bridge
                or getattr(app, "_native_attach_cache", None) != attach):
            kwargs = dict(preview=preview, snap_request=snap_request)
            if picker_context:
                kwargs["picker"] = True
            publish_attach(dict(fields), attach[1], **kwargs)
            app._native_attach_bridge, app._native_attach_cache = bridge, attach
