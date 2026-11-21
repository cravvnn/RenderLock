"""Desktop wiring for high-resolution stills (see ``dolly.screenshot``).

A still reuses the layered export pipeline: a lossless color take with depth,
then the native players capture. The hooks here only redirect the pieces that
differ for a still (the camera project, the players finish step and the final
assembly), so the video export paths are unchanged.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import logging
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from . import player_layer, screenshot
from .display import client_size, focus_window
from .replays import parse_launch_options
from .video_export import (ACTIVE_STATES, BITRATE_PRESETS, VideoOptions, bundled_ffmpeg_path,
                           resolve_ffmpeg)

LOG = logging.getLogger(__name__)


def init(app) -> None:
    app._still_run = None
    app.screenshot_depth = tk.BooleanVar(value=True)
    app.screenshot_render_size = tk.StringVar(
        value=screenshot.render_size_label(app.launch_options.get()))
    app.screenshot_size_text = tk.StringVar(value="Game render size: launch a replay to see it.")


def build(app, parent=None, *, before=None, card=None) -> None:
    """Fill the Export page's Screenshot card, or add the legacy card to ``parent``.

    ``card`` is the page's kit card (``export_page.screenshot_card``): the page draws
    its title and info bubble (``SCREENSHOT_INFO``), this adds the rows. A kit card
    passed as ``parent`` is filled the same way (``before`` means nothing there).
    """
    from .ui.kit_card import KitCard
    if card is None and isinstance(parent, KitCard):
        card = parent
    if card is not None:
        _fill_card(app, card)
        return
    from .ui.export_page import SCREENSHOT_INFO
    from .ui.scale import scaler
    from .ui.widgets import GAP, card as frame_card, field
    dp = scaler(parent)
    still = frame_card(parent, "Screenshot (high-res still)")
    if before is not None:
        still.pack_configure(before=before)
    ttk.Label(still, text=SCREENSHOT_INFO, style="CardMuted.TLabel",
              wraplength=dp(760)).pack(anchor="w", pady=dp((0, GAP)))
    app.screenshot_size_combo = field(still, "Render size (next launch)", app.screenshot_render_size,
                                      values=screenshot.RENDER_SIZE_LABELS)
    app.screenshot_size_combo.bind("<<ComboboxSelected>>", lambda _e: render_size_changed(app))
    ttk.Label(still, textvariable=app.screenshot_size_text, style="CardMuted.TLabel",
              wraplength=dp(760)).pack(anchor="w", pady=dp((0, GAP)))
    row = ttk.Frame(still, style="Card.TFrame")
    row.pack(fill="x")
    app.screenshot_depth_check = ttk.Checkbutton(row, style="Card.TCheckbutton",
                                                 text="Include depth (EXR)", variable=app.screenshot_depth)
    app.screenshot_depth_check.pack(side="left", padx=dp((0, 18)))
    app.screenshot_button = ttk.Button(row, text="Take screenshot", style="Primary.TButton",
                                       command=lambda: start(app))
    app.screenshot_button.pack(side="right")


def _fill_card(app, card) -> None:
    """The kit rows (Figma [design reference]): render size, its status line, depth and the primary button."""
    row = card.row()
    row.label("Render size (next launch)")
    row.gap(10)
    app.screenshot_size_combo = row.widget(ttk.Combobox(row.host, textvariable=app.screenshot_render_size,
                                                        values=screenshot.RENDER_SIZE_LABELS, state="readonly",
                                                        style="Kit.TCombobox"), width="fill")
    app.screenshot_size_combo.bind("<<ComboboxSelected>>", lambda _e: render_size_changed(app))
    card.text(textvariable=app.screenshot_size_text)
    row = card.row()
    app.screenshot_depth_check = row.widget(ttk.Checkbutton(row.host, text="Include depth (EXR)",
                                                            variable=app.screenshot_depth, style="Kit.TCheckbutton"))
    row.push()
    app.screenshot_button = row.button("Take screenshot", lambda: start(app), kind="primary")
    # The page set its Tab stops before these rows existed (watching again only adds the new ones).
    card.page.watch_focus()


def render_size_changed(app) -> None:
    """Store the chosen render size in the launch options for the next session."""
    def operation():
        size = dict(screenshot.RENDER_SIZES)[app.screenshot_render_size.get()]
        text = screenshot.apply_render_size(app.launch_options.get().strip(), size)
        parse_launch_options(text)
        settings = replace(app.app_settings, launch_options=text)
        app.launch_options.set(text)
        app._persist_preferences(settings, "Render size saved. It applies the next time Dolly opens the "
                                           "replay: " + (text or "no display options"))
    app._guard("Screenshot render size", operation)


def refresh(app, ready: bool) -> None:
    """Called from the video refresh: button state and current render size."""
    button = getattr(app, "screenshot_button", None)
    if button is None:
        return
    idle = getattr(app, "_still_run", None) is None and not app._layer_queue
    # The render size only edits launch options, so it stays usable before launch.
    for widget, state in ((button, "normal" if ready and idle else "disabled"),
                          (app.screenshot_depth_check, "normal" if idle else "disabled"),
                          (app.screenshot_size_combo, "readonly" if idle else "disabled")):
        if str(widget.cget("state")) != state:
            widget.configure(state=state)
    now = time.monotonic()
    if now - getattr(app, "_screenshot_size_checked", 0.0) < 2.0:
        return
    app._screenshot_size_checked = now
    size = client_size(app.controller.game_pid()) if ready else None
    text = ("Game render size: %d x %d. Stills are saved at this size." % size if size
            else "Game render size: launch a replay to see it.")
    if app.screenshot_size_text.get() != text:
        app.screenshot_size_text.set(text)


def _output_directory(app) -> Path:
    raw = app.video_path.get().strip()
    directory = Path(raw).expanduser().parent if raw else None
    if directory is None or not directory.is_dir():
        pictures = Path.home() / "Pictures"
        directory = pictures if pictures.is_dir() else Path.home()
    return directory


def start(app) -> None:
    """UI thread: capture the paused view and run the still's layered take."""
    if getattr(app, "_bone_picker_context", None):
        app.status_text.set("Finish or cancel Bone Picker before taking a screenshot.")
        return
    if (app.busy or getattr(app, "_still_run", None) is not None or app._layer_queue
            or app.video_export.status().get("state") in ACTIVE_STATES):
        app.status_text.set("Finish the current operation or recording before taking a screenshot.")
        return
    try:
        ffmpeg = resolve_ffmpeg(app.ffmpeg_path.get().strip() or None) or bundled_ffmpeg_path()
        if ffmpeg is None:
            raise ValueError("Screenshots need FFmpeg. Choose it under Export > FFmpeg runtime.")
        depth = bool(app.screenshot_depth.get())
        path = screenshot.default_still_path(app.project.name, _output_directory(app))
        options = VideoOptions(path, screenshot.STILL_FPS, BITRATE_PRESETS["20 Mbps"],
                               codec=screenshot.STILL_CODEC, ffmpeg_path=ffmpeg, fixed_step=True,
                               speed=screenshot.STILL_SPEED, depth=depth, depth_exr=depth,
                               layers=("players",),
                               players_scratch=(app.players_scratch.get().strip() or None) if hasattr(app, "players_scratch") else None).validated()
        base = copy.deepcopy(app.project)
    except (ValueError, OSError) as exc:
        app._error("Screenshot", exc)
        return
    run = screenshot.StillRun(None, options, depth)
    app._still_run = run
    app._pending_auto_play = True
    app._auto_finish_layered = True
    app._base_capture = options
    app._layer_queue = [("players", "capture")]
    app._active_layer_take = None
    app._take_playing_seen = False
    app._take_audit_seen = set()
    app._take_audit_reported = False
    focus_window(app.controller.game_pid())

    def operation():
        controller = app.controller
        with controller._op_lock:
            snapshot = controller._sample_paused_native_view()
            key, tick = controller._record_native_capture(snapshot, 0.0, pause=False)
        run.project = screenshot.still_project(base, key, tick)
        LOG.info("Screenshot at tick %d into %s", tick, run.take_folder)
        return app.video_export.start(options, project=run.project, frozen=False)

    if not app._submit("Preparing screenshot", operation, app._video_operation_done):
        abandon(app)
        app._clear_layer_pipeline()
    else:
        app.status_text.set("Taking screenshot: color, depth and players passes record automatically.")


def abandon(app) -> None:
    app._still_run = None


def snapshot(app):
    """Project override for playback and layer takes while a still runs."""
    run = getattr(app, "_still_run", None)
    if run is None or run.project is None:
        return None
    return copy.deepcopy(run.project)


def finish_player_capture(app, layer):
    """Worker: write the hero stills from the settled bundle frame."""
    run = getattr(app, "_still_run", None)
    if run is None or app._base_capture is None:
        return {"state": "cancelled", "layer": layer, "reason": "run_torn_down"}
    deployment = app.controller.deployment_directory()
    if deployment is None:
        raise RuntimeError("The players capture directory is gone.")
    try:
        player_layer.request_stop(deployment)
    except OSError as exc:
        LOG.warning("Players stop request failed: %s", exc)
    from .player_scratch import from_marker
    data_dir = from_marker(deployment)
    status = player_layer.wait_for_capture(deployment, timeout=180.0)
    folder = run.take_folder
    index = screenshot.settled_index(folder, Path(run.options.path).name)
    if not status.startswith("complete"):
        # The players pass can end a frame or two before the color take. A still
        # only uses the settled frame, so accept it when that frame is aligned.
        if not (status.startswith(screenshot.SHORT_CAPTURE_STATUSES) and screenshot.settled_frame_aligned(
                data_dir / player_layer.META_NAME, folder, index)):
            raise RuntimeError("The players capture did not finish cleanly: "
                               + (status or "no status was reported"))
        LOG.warning("Players capture ended short; settled frame %d is aligned, using it", index)
    width, height, pixels = screenshot.read_bundle_frame(data_dir / player_layer.BUNDLE_NAME, index)
    alpha, _isolated = screenshot.write_hero_stills(width, height, pixels, folder)
    del pixels
    leftovers = player_layer.cleanup_capture(deployment, folder / layer)
    if leftovers:
        LOG.warning("Screenshot players stills saved; could not remove: %s", ", ".join(leftovers))
    return {"state": "completed", "layer": layer, "master": str(alpha)}


def audit(app, base) -> None:
    # A still deliberately has no players.mov; the take audit would flag it.
    if getattr(app, "_still_run", None) is None:
        app._audit_finished_layers(base)


def restore(app):
    """Worker: restore the scene, then assemble the still and drop intermediates."""
    app._restore_scene_state()
    run = getattr(app, "_still_run", None)
    if run is None:
        return None
    try:
        ffmpeg = resolve_ffmpeg(run.options.ffmpeg_path) or bundled_ffmpeg_path()
        if ffmpeg is None:
            raise RuntimeError("Screenshot assembly needs FFmpeg; the take folder is kept.")
        outputs = screenshot.finish(Path(ffmpeg), run)
    finally:
        app._still_run = None
    app._enqueue_log("Screenshot saved to " + str(run.take_folder) + " ("
                     + ", ".join(p.name for p in outputs) + ")")
    return str(run.take_folder)


def restored(app, folder) -> None:
    if folder:
        app.status_text.set("Screenshot saved to " + folder)
