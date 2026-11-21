"""RenderLock "Deadlock (replay)" render engine: the game draws, Blender shows it.

Experimental. Launches Deadlock with Dolly's game side, plays a local replay and
streams the game's own frames into Blender's viewport and render result. The
Blender view (or scene camera for F12) drives the game camera, and the timeline
drives the replay tick.
"""
from __future__ import annotations

bl_info = {
    "name": "RenderLock Game View (experimental)",
    "author": "RenderLock",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "category": "Render",
}

import math
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import bpy
import gpu
import numpy as np
from gpu_extras.presets import draw_texture_2d

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import frame_stream  # noqa: E402

ENGINE = "RENDERLOCK_GAME"
UNIT = 0.0254          # metres per Source unit (RenderLock scene coordinates)
TICK_RATE = 64.0
IMAGE_NAME = "RenderLock Game View"


# --------------------------------------------------------------------------- camera

def source_pose(matrix, hfov_radians, aspect):
    """Blender camera/view world matrix -> (x, y, z, pitch, yaw, roll, hfov_deg, aspect)."""
    m = matrix
    forward = (-m[0][2], -m[1][2], -m[2][2])
    right = (m[0][0], m[1][0], m[2][0])
    pitch = math.degrees(math.atan2(-forward[2], math.hypot(forward[0], forward[1])))
    yaw = math.degrees(math.atan2(forward[1], forward[0]))
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    zero_right, zero_up = (sy, -cy, 0.0), (sp * cy, sp * sy, cp)
    roll = math.degrees(math.atan2(-sum(a * b for a, b in zip(right, zero_up)),
                                   sum(a * b for a, b in zip(right, zero_right))))
    return (m[0][3] / UNIT, m[1][3] / UNIT, m[2][3] / UNIT, pitch, yaw, roll,
            math.degrees(hfov_radians), aspect)


def camera_object_pose(scene, camera):
    render = scene.render
    width = render.resolution_x * render.pixel_aspect_x
    height = render.resolution_y * render.pixel_aspect_y
    aspect = width / height
    data = camera.data
    fit = data.sensor_fit
    if fit == 'AUTO':
        fit = 'HORIZONTAL' if width >= height else 'VERTICAL'
    if fit == 'HORIZONTAL':
        hfov = 2 * math.atan(data.sensor_width / (2 * data.lens))
    else:
        vfov = 2 * math.atan(data.sensor_height / (2 * data.lens))
        hfov = 2 * math.atan(math.tan(vfov / 2) * aspect)
    return source_pose(camera.matrix_world, hfov, aspect)


def scene_tick(scene, frame=None):
    props = scene.renderlock_game
    frame = scene.frame_current if frame is None else frame
    fps = scene.render.fps / scene.render.fps_base
    return int(round(props.start_tick + (frame - scene.frame_start) / fps * TICK_RATE))


# --------------------------------------------------------------------------- session

class Session:
    """One game session owned by this Blender process."""

    def __init__(self):
        self.controller = None
        self.stream = None
        self.state = "idle"          # idle, starting, ready, failed, closed
        self.message = ""
        self.lock = threading.Lock()
        self.wanted_tick = None
        self.current_tick = None
        self.seeking = False
        self.frame_meta = None
        self.image = None
        self.received = 0

    # ---- lifecycle
    def start(self, game, demo, width, height, hide_hud):
        if self.state in ("starting", "ready"):
            return
        self.state, self.message = "starting", "Starting Deadlock…"
        threading.Thread(target=self._start, args=(game, demo, width, height, hide_hud),
                         name="renderlock-game-start", daemon=True).start()

    def _start(self, game, demo, width, height, hide_hud):
        try:
            from dolly.controller import Controller
            self.controller = Controller(log_callback=self._log)
            self.controller.start_editing(
                game, demo, protocol="netcon", native=True,
                launch_options=f"-w {width} -h {height} -windowed -noborder")
            bridge = self.controller._native_bridge()
            self.stream = frame_stream.FrameStream(bridge.token, 3840, 2160)
            self.stream.enable(True)
            if hide_hud:
                self.set_hud(False)
            self.state, self.message = "ready", "Connected. The viewport shows the game."
        except Exception as error:
            traceback.print_exc()
            self.state, self.message = "failed", f"Could not start: {error}"

    def _log(self, *parts, **_):
        text = " ".join(str(p) for p in parts)
        if text:
            self.message = text[:300]
        print("[RenderLock game]", text, flush=True)

    def stop(self):
        try:
            if self.stream:
                self.stream.close()
            if self.controller:
                self.controller.close()
        except Exception:
            traceback.print_exc()
        finally:
            self.stream = None
            self.controller = None
            self.state, self.message = "closed", "Disconnected."
        threading.Thread(target=_close_game, daemon=True).start()

    # ---- game controls
    def set_hud(self, visible):
        """Floating health bars and the replay HUD (best effort)."""
        if not self.controller:
            return
        value = 1 if visible else 0
        for command in (f"citadel_hud_visible {value}", f"citadel_hide_replay_hud {1 - value}"):
            try:
                self.controller._request(command, timeout=3, allow_error=True)
            except Exception:
                traceback.print_exc()

    def push_camera(self, pose):
        if self.stream is not None:
            self.stream.set_camera(pose)

    def request_tick(self, tick):
        self.wanted_tick = int(tick)
        if self.state == "ready" and not self.seeking:
            self.seeking = True
            threading.Thread(target=self._seek_loop, name="renderlock-game-seek", daemon=True).start()

    def _seek_loop(self):
        try:
            while self.state == "ready" and self.wanted_tick is not None and self.wanted_tick != self.current_tick:
                target = self.wanted_tick
                try:
                    settled = self.controller.seek_replay_tick(max(0, target))
                    self.current_tick = target
                    self.message = f"Replay tick {settled.get('tick', target)}"
                except Exception as error:
                    self.message = f"Seek failed: {error}"
                    self.current_tick = target
        finally:
            self.seeking = False

    # ---- frames
    def poll_frame(self):
        """Newest game frame as (meta, top-down RGBA uint8 array), or None."""
        if self.stream is None:
            return None
        got = self.stream.latest()
        if got is None:
            return None
        meta, pixels = got
        array = np.frombuffer(pixels, dtype=np.uint8).reshape(meta["height"], meta["width"], 4)
        if meta["format"] in frame_stream.BGRA8:
            array = array[:, :, (2, 1, 0, 3)]
        self.frame_meta = meta
        self.received += 1
        return meta, array

    def wait_frame(self, pose, timeout=10.0, settle=3):
        """Block until `settle` consecutive frames show `pose`; return the last one."""
        deadline = time.monotonic() + timeout
        matched, last = 0, None
        while time.monotonic() < deadline:
            self.push_camera(pose)
            got = self.poll_frame()
            if got is None:
                time.sleep(0.002)
                continue
            shown = got[0]["pose"]
            close = all(abs(a - b) < 1e-2 for a, b in zip(shown[:3], pose[:3])) and \
                abs(((shown[4] - pose[4]) + 180) % 360 - 180) < 1e-2
            matched = matched + 1 if close and not self.seeking else 0
            last = got
            if matched >= settle:
                return got
        return last


SESSION = Session()


def _close_game():
    for _ in range(20):
        result = subprocess.run(["taskkill", "/IM", "deadlock.exe"], capture_output=True)
        if result.returncode != 0:
            return
        time.sleep(1.5)


def to_display_image(array):
    """Top-down uint8 RGBA -> Blender image pixel floats (bottom-up)."""
    return (array[::-1].astype(np.float32) / 255.0).ravel()


def srgb_to_linear(values):
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


# --------------------------------------------------------------------------- engine

class RenderLockGameEngine(bpy.types.RenderEngine):
    bl_idname = ENGINE
    bl_label = "Deadlock (replay)"
    bl_use_preview = False
    bl_use_postprocess = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._texture = None
        self._texture_seq = None

    # Viewport: the view drives the game camera, the newest game frame is drawn.
    def view_update(self, context, depsgraph):
        pass

    def view_draw(self, context, depsgraph):
        region, rv3d = context.region, context.region_data
        if SESSION.state == "ready" and rv3d is not None and rv3d.is_perspective:
            hfov = 2 * math.atan(1.0 / rv3d.window_matrix[0][0])
            SESSION.push_camera(source_pose(rv3d.view_matrix.inverted(), hfov,
                                            region.width / max(1, region.height)))
        got = SESSION.poll_frame()
        if got is not None:
            meta, array = got
            image = bpy.data.images.get(IMAGE_NAME)
            if image is None or tuple(image.size) != (meta["width"], meta["height"]):
                if image is not None:
                    bpy.data.images.remove(image)
                image = bpy.data.images.new(IMAGE_NAME, meta["width"], meta["height"], alpha=True)
                image.colorspace_settings.name = "Non-Color"
            image.pixels.foreach_set(to_display_image(array))
            image.update()
            self._texture = gpu.texture.from_image(image)
        if self._texture is not None:
            draw_texture_2d(self._texture, (0, 0), region.width, region.height)
        if SESSION.state in ("starting", "ready"):
            self.tag_redraw()

    # F12 / Render Animation: scene camera, exact replay tick, settled game frame.
    def render(self, depsgraph):
        scene = depsgraph.scene
        width = int(scene.render.resolution_x * scene.render.resolution_percentage / 100)
        height = int(scene.render.resolution_y * scene.render.resolution_percentage / 100)
        result = self.begin_result(0, 0, width, height)
        layer = result.layers[0].passes["Combined"]
        try:
            if SESSION.state != "ready" or scene.camera is None:
                self.report({'ERROR'}, "Connect to the game and set a scene camera first.")
                return
            tick = scene_tick(scene)
            SESSION.request_tick(tick)
            deadline = time.monotonic() + 60
            while (SESSION.seeking or SESSION.current_tick != tick) and time.monotonic() < deadline:
                if self.test_break():
                    return
                time.sleep(0.02)
            got = SESSION.wait_frame(camera_object_pose(scene, scene.camera))
            if got is None:
                self.report({'ERROR'}, "No frame arrived from the game.")
                return
            meta, array = got
            if (meta["width"], meta["height"]) != (width, height):
                # Nearest-neighbour fit to the render size (game window size may differ).
                ys = (np.arange(height) * meta["height"] / height).astype(int)
                xs = (np.arange(width) * meta["width"] / width).astype(int)
                array = array[ys][:, xs]
            rgba = array[::-1].astype(np.float32) / 255.0
            rgba[..., :3] = srgb_to_linear(rgba[..., :3])
            rgba[..., 3] = 1.0
            layer.rect.foreach_set(rgba.ravel())
            scene["renderlock_game_last_frame"] = {k: meta[k] for k in ("tick", "present", "seq")}
        finally:
            self.end_result(result)


# --------------------------------------------------------------------------- UI

def _on_hud(self, context):
    if SESSION.state == "ready":
        SESSION.set_hud(not self.hide_hud)


class RenderLockGameSettings(bpy.types.PropertyGroup):
    game_path: bpy.props.StringProperty(name="Deadlock folder", subtype='DIR_PATH',
                                        default="B:/SteamLibrary/steamapps/common/Deadlock")
    replay_path: bpy.props.StringProperty(name="Replay", subtype='FILE_PATH')
    start_tick: bpy.props.IntProperty(name="Start tick", min=0,
                                      description="Replay tick shown at the scene's first frame")
    width: bpy.props.IntProperty(name="Game width", default=1280, min=320, max=3840)
    height: bpy.props.IntProperty(name="Game height", default=720, min=240, max=2160)
    hide_hud: bpy.props.BoolProperty(name="Hide health bars and HUD", default=True, update=_on_hud)
    follow_timeline: bpy.props.BoolProperty(name="Follow timeline", default=True,
                                            description="Seek the replay when the frame changes")


class RENDERLOCK_OT_game_connect(bpy.types.Operator):
    bl_idname = "renderlock.game_connect"
    bl_label = "Connect to game"

    def execute(self, context):
        props = context.scene.renderlock_game
        replay = bpy.path.abspath(props.replay_path)
        if not os.path.isfile(replay):
            self.report({'ERROR'}, "Choose a local .dem replay")
            return {'CANCELLED'}
        if not props.start_tick and "agr_start_tick" in context.scene:
            props.start_tick = int(context.scene["agr_start_tick"])
        context.scene.view_settings.view_transform = 'Standard'
        SESSION.start(bpy.path.abspath(props.game_path), replay, props.width, props.height, props.hide_hud)
        return {'FINISHED'}


class RENDERLOCK_OT_game_disconnect(bpy.types.Operator):
    bl_idname = "renderlock.game_disconnect"
    bl_label = "Disconnect"

    def execute(self, context):
        SESSION.stop()
        return {'FINISHED'}


class RENDERLOCK_PT_game(bpy.types.Panel):
    bl_label = "RenderLock Game View"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"
    COMPAT_ENGINES = {ENGINE}

    @classmethod
    def poll(cls, context):
        return context.engine == ENGINE

    def draw(self, context):
        props = context.scene.renderlock_game
        layout = self.layout
        layout.use_property_split = True
        col = layout.column()
        col.prop(props, "game_path")
        col.prop(props, "replay_path")
        col.prop(props, "start_tick")
        row = col.row(align=True)
        row.prop(props, "width")
        row.prop(props, "height")
        col.prop(props, "hide_hud")
        col.prop(props, "follow_timeline")
        row = layout.row()
        if SESSION.state in ("starting", "ready"):
            row.operator(RENDERLOCK_OT_game_disconnect.bl_idname, icon='CANCEL')
        else:
            row.operator(RENDERLOCK_OT_game_connect.bl_idname, icon='PLAY')
        box = layout.box()
        box.label(text=f"State: {SESSION.state}")
        if SESSION.message:
            box.label(text=SESSION.message[:120])
        if SESSION.frame_meta:
            meta = SESSION.frame_meta
            box.label(text=f"Frame {meta['seq']}  tick {meta['tick']}  {meta['width']}×{meta['height']}")


@bpy.app.handlers.persistent
def _frame_changed(scene, _depsgraph=None):
    props = getattr(scene, "renderlock_game", None)
    if props is None or not props.follow_timeline or SESSION.state != "ready":
        return
    if scene.render.engine != ENGINE:
        return
    SESSION.request_tick(scene_tick(scene))


def _compatible_panels():
    from bl_ui import properties_output, properties_render  # noqa: F401
    return [p for p in bpy.types.Panel.__subclasses__()
            if hasattr(p, "COMPAT_ENGINES") and "BLENDER_EEVEE_NEXT" in p.COMPAT_ENGINES
            and p.__name__.startswith(("RENDER_PT_format", "RENDER_PT_frame_range",
                                       "RENDER_PT_output", "RENDER_PT_color_management"))]


CLASSES = (RenderLockGameSettings, RENDERLOCK_OT_game_connect, RENDERLOCK_OT_game_disconnect,
           RenderLockGameEngine, RENDERLOCK_PT_game)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.renderlock_game = bpy.props.PointerProperty(type=RenderLockGameSettings)
    for panel in _compatible_panels():
        panel.COMPAT_ENGINES.add(ENGINE)
    if _frame_changed not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_frame_changed)


def unregister():
    if _frame_changed in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_changed)
    for panel in _compatible_panels():
        panel.COMPAT_ENGINES.discard(ENGINE)
    del bpy.types.Scene.renderlock_game
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
