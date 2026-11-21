"""RenderLock "Deadlock (replay)" render engine: the game draws, Blender shows it.

Experimental. Launches Deadlock with Dolly's game side, plays a local replay and
streams the game's own frames into Blender's viewport and render result. The
Blender view (or scene camera for F12) drives the game camera, and the timeline
drives the replay tick.
"""

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

def _root():
    """This file's folder, also when run from Blender's Text Editor (where __file__ is only a name)."""
    here = Path(__file__)
    if here.is_absolute() and (here.parent / "frame_stream.py").is_file():
        return here.resolve().parent
    for text in bpy.data.texts:
        if text.filepath:
            path = Path(bpy.path.abspath(text.filepath))
            if path.name == "blender_game_view.py" and (path.parent / "frame_stream.py").is_file():
                return path.resolve().parent
    raise RuntimeError("Open blender_game_view.py from its game_render folder, then run it.")


ROOT = _root()
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


def render_size(scene):
    render = scene.render
    return (int(render.resolution_x * render.resolution_percentage / 100),
            int(render.resolution_y * render.resolution_percentage / 100))


def game_window_size(scene):
    """Render resolution (or the panel's override), shrunk to fit the primary monitor."""
    props = scene.renderlock_game
    width, height = (props.width, props.height) if props.width and props.height else render_size(scene)
    try:
        import ctypes
        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        screen_w, screen_h = width, height
    scale = min(1.0, screen_w / width, screen_h / height)
    width, height = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
    return max(320, width), max(240, height)


def resample(array, width, height):
    """Bilinear resize of an HxWx4 uint8 image."""
    src_h, src_w = array.shape[:2]
    if (src_w, src_h) == (width, height):
        return array
    ys = np.clip((np.arange(height) + 0.5) * src_h / height - 0.5, 0, src_h - 1)
    xs = np.clip((np.arange(width) + 0.5) * src_w / width - 0.5, 0, src_w - 1)
    y0, x0 = ys.astype(int), xs.astype(int)
    y1, x1 = np.minimum(y0 + 1, src_h - 1), np.minimum(x0 + 1, src_w - 1)
    wy, wx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    a = array.astype(np.float32)
    top = a[y0][:, x0] * (1 - wx) + a[y0][:, x1] * wx
    bottom = a[y1][:, x0] * (1 - wx) + a[y1][:, x1] * wx
    return (top * (1 - wy) + bottom * wy + 0.5).astype(np.uint8)


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
        self.last_pose = None
        self.position_error = None
        self.held_frame = None
        self.adopt_tick_scene = None

    # ---- lifecycle
    def start(self, game, demo, width, height, hide_hud):
        if self.state in ("starting", "ready"):
            return
        self.state, self.message = "starting", "Starting Deadlock…"
        threading.Thread(target=self._start, args=(game, demo, width, height, hide_hud),
                         name="renderlock-game-start", daemon=True).start()

    def _start(self, game, demo, width, height, hide_hud):
        for attempt in range(2):
            error = self._start_once(game, demo, width, height, hide_hud)
            if error is None:
                return
            if attempt or "already running" in error:
                # Never touch a Deadlock this session did not start.
                self.state, self.message = "failed", f"Could not start: {error}"
                return
            self.state, self.message = "starting", "The game stalled while starting; restarting it once…"
            pid = self._launched_pid()
            try:
                if self.controller:
                    self.controller.close()
            except Exception:
                traceback.print_exc()
            self.controller = None
            _close_game(pid)
            time.sleep(10)

    def _start_once(self, game, demo, width, height, hide_hud):
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
            if self.adopt_tick_scene:
                bpy.app.timers.register(self._adopt_tick, first_interval=0.5)
            return None
        except Exception as error:
            traceback.print_exc()
            return str(error)

    def _log(self, *parts, **_):
        text = " ".join(str(p) for p in parts)
        if text:
            self.message = text[:300]
        print("[RenderLock game]", text, flush=True)

    def _launched_pid(self):
        """PID of the game this session launched, or None (never another Deadlock)."""
        try:
            session = getattr(self.controller, "_session", None)
            return int(session.pid) if session is not None and session.pid else None
        except Exception:
            return None

    def stop(self):
        pid = self._launched_pid()
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
        threading.Thread(target=_close_game, args=(pid,), daemon=True).start()

    # ---- game controls
    def set_hud(self, visible):
        """Floating health bars and the replay HUD (best effort)."""
        if not self.controller:
            return
        try:  # Dolly's floating health bar switch stays in effect across shots
            if bool(self.controller._healthbars_hidden) == bool(visible):
                self.controller.toggle_healthbars()
        except Exception:
            traceback.print_exc()
        value = 1 if visible else 0
        for command in (f"citadel_hud_visible {value}", f"citadel_hide_replay_hud {1 - value}"):
            try:
                self.controller._request(command, timeout=3, allow_error=True)
            except Exception:
                traceback.print_exc()

    def replay_tick(self):
        """The replay tick the game shows now (None if unknown)."""
        try:
            return int(self.controller._native_bridge().status()["tick"])
        except Exception:
            return None

    def _adopt_tick(self):
        """Main thread: a scene with no start tick starts where the replay is."""
        scene = bpy.data.scenes.get(self.adopt_tick_scene or "")
        self.adopt_tick_scene = None
        tick = self.replay_tick()
        if scene is not None and tick is not None and not scene.renderlock_game.start_tick:
            props = scene.renderlock_game
            follow, props.follow_timeline = props.follow_timeline, False
            props.start_tick = tick
            scene.frame_set(scene.frame_start)
            props.follow_timeline = follow
            self.current_tick = tick
        return None

    def push_camera(self, pose):
        if pose is not None:
            self.last_pose = tuple(pose)
        if self.stream is not None:
            self.stream.set_camera(pose)

    # ---- replay positioning
    # A bare seek leaves the world wrong (effects from the skipped stretch pile
    # up, players may not be redrawn). Dolly's recording preparation reloads the
    # replay, seeks two seconds early, simulates normally and pauses exactly on
    # the tick, then enters manual flight; the streamed camera takes over there.
    def position(self, tick, pose):
        from dolly.path import Project
        pose = pose or self.last_pose
        project = Project(name="renderlock-view", keyframes=[_keyframe(0.0, pose)],
                          interpolation="linear", start_tick=int(tick), tick_rate=TICK_RATE,
                          standard_aspect=pose[7])
        project.validate()
        self.stream.set_camera(None)
        self.held_frame = None
        self.controller.prepare_native_recording(project)
        self.stream.set_camera(pose)
        self.current_tick = int(tick)

    def request_tick(self, tick):
        self.wanted_tick = int(tick)
        if self.state == "ready" and not self.seeking:
            self.seeking = True
            threading.Thread(target=self._seek_loop, name="renderlock-game-seek", daemon=True).start()

    def _seek_loop(self):
        try:
            while self.state == "ready" and self.wanted_tick is not None and self.wanted_tick != self.current_tick:
                target = self.wanted_tick
                self.message = f"Moving the replay to tick {target}…"
                for attempt in range(3):  # a seek right after a replay reload can miss once
                    try:
                        self.position(max(0, target), self.last_pose)
                        self.message = f"Replay tick {target}"
                        self.position_error = None
                        break
                    except Exception as error:
                        traceback.print_exc()
                        self.message = f"Seek failed: {error}"
                        self.position_error = str(error)
                else:
                    # Some ticks cannot be reached by a direct jump; play a short
                    # run-up into them instead and hold the last frame.
                    self.message = f"Playing into tick {target}…"
                    try:
                        self.run_up(max(0, target), self.last_pose)
                        self.position_error = None
                        self.message = f"Replay tick {target}"
                    except Exception as error:
                        traceback.print_exc()
                        self.position_error = str(error)
                        self.current_tick = None
                        self.wanted_tick = None
                        return
        finally:
            self.seeking = False

    def run_up(self, tick, pose, frames=30, fps=60):
        """Reach `tick` by playing `frames` fixed steps from just before it."""
        lead = int(round(frames / fps * TICK_RATE))
        captured = self.capture_range(tick - lead, fps, [pose] * (frames + 1))
        if frames not in captured:
            raise RuntimeError(f"The run-up into tick {tick} did not deliver its last frame.")
        self.held_frame = captured[frames]
        self.current_tick = int(tick)

    def capture_range(self, start_tick, fps, poses, cancelled=lambda: False, timeout=600.0):
        """Play the camera animation once, fixed-step, and keep each frame's game image.

        poses[k] is the camera for shot time k / fps. Returns {k: (meta, array)}.
        """
        from dolly.path import Project
        keys = [_keyframe(k / fps, pose) for k, pose in enumerate(poses)]
        project = Project(name="renderlock-render", keyframes=keys, interpolation="linear",
                          rotation_mode="unwrapped", start_tick=int(start_tick), tick_rate=TICK_RATE,
                          standard_aspect=poses[0][7])
        project.validate()
        controller, frames = self.controller, {}
        self.seeking = True
        try:
            self.stream.set_camera(None)
            controller.prepare_native_recording(project)
            controller.set_export_timing(fps)
            while self.poll_frame() is not None:
                pass  # drain frames from before the shot
            controller.play(project, speed=1, rate=60, frozen=False)
            deadline = time.monotonic() + timeout
            last = len(poses) - 1
            while time.monotonic() < deadline and not cancelled():
                got = self.poll_frame()
                if got is None:
                    if controller.status().get("shot_finished") and last in frames:
                        break
                    time.sleep(0.001)
                    continue
                phase = got[0]["phase"]
                k = int(round(phase * fps))
                if abs(phase * fps - k) < 0.05 and 0 <= k <= last and k not in frames:
                    frames[k] = got
                if len(frames) == len(poses):
                    break
        finally:
            try:
                controller.clear_export_timing()
                controller.stop()
                controller.enter_native_flight(pose=None)
            except Exception:
                traceback.print_exc()
            self.current_tick = None
            self.seeking = False
        return frames

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


def _keyframe(time_s, pose):
    from dolly.path import Keyframe
    x, y, z, pitch, yaw, roll, hfov, aspect = pose
    return Keyframe(time=time_s, x=x, y=y, z=z, pitch=pitch, yaw=yaw, roll=roll, fov=hfov,
                    aspect_ratio=aspect, lens_scale=math.tan(math.radians(hfov) / 2) / aspect)


SESSION = Session()


def _close_game(pid):
    """Ask the game this session launched to close; other Deadlock processes are left alone."""
    if not pid:
        return
    for _ in range(20):
        result = subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
        if result.returncode != 0:
            return
        time.sleep(1.5)


def cache_folder(scene):
    props = scene.renderlock_game
    folder = bpy.path.abspath(props.cache_dir) if props.cache_dir else ""
    if not folder or props.cache_dir.startswith("//") and not bpy.data.filepath:
        folder = os.path.join(bpy.app.tempdir, "renderlock_game_frames")
    return Path(folder)


def baked_frame(scene, frame):
    """A frame captured by "Bake game frames" for this scene's replay range, if any."""
    folder = cache_folder(scene)
    path = folder / f"frame_{frame:06d}.npy"
    info = folder / "bake.json"
    if not path.is_file() or not info.is_file():
        return None
    import json
    bake = json.loads(info.read_text())
    if bake.get("start_tick") != scene.renderlock_game.start_tick or bake.get("replay") != \
            bpy.path.abspath(scene.renderlock_game.replay_path):
        return None
    meta = bake["frames"].get(str(frame))
    return (meta, np.load(path)) if meta else None


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
            frame = scene.frame_current
            got = baked_frame(scene, frame)
            if got is None:
                # Exact single frame: settle the replay on this tick, then wait for
                # consecutive game frames that show the scene camera.
                pose = camera_object_pose(scene, scene.camera)
                SESSION.last_pose = pose
                tick = scene_tick(scene)
                SESSION.request_tick(tick)
                deadline = time.monotonic() + 180
                while (SESSION.seeking or SESSION.current_tick != tick) and time.monotonic() < deadline:
                    if self.test_break():
                        return
                    time.sleep(0.02)
                if SESSION.current_tick != tick:
                    self.report({'ERROR'}, f"The replay could not be settled on tick {tick}: {SESSION.position_error}")
                    return
                got = SESSION.held_frame or SESSION.wait_frame(pose)
                SESSION.held_frame = None
            if got is None:
                self.report({'ERROR'}, "No frame arrived from the game.")
                return
            meta, array = got
            if (meta["width"], meta["height"]) != (width, height):
                # The game window could not match the render size (monitor too small).
                array = resample(array, width, height)
            rgba = array[::-1].astype(np.float32) / 255.0
            rgba[..., :3] = srgb_to_linear(rgba[..., :3])
            rgba[..., 3] = 1.0
            layer.rect.foreach_set(rgba.ravel())
            scene["renderlock_game_last_frame"] = {k: meta[k] for k in ("tick", "present", "seq")}
        finally:
            self.end_result(result)


# --------------------------------------------------------------------------- UI

def _scene_of(props):
    return next((scene for scene in bpy.data.scenes if scene.renderlock_game == props), None)


def _get_end_tick(self):
    scene = _scene_of(self)
    return scene_tick(scene, scene.frame_end) if scene else self.start_tick


def _set_end_tick(self, value):
    scene = _scene_of(self)
    if scene is None:
        return
    fps = scene.render.fps / scene.render.fps_base
    frames = max(0, int(round((value - self.start_tick) / TICK_RATE * fps)))
    scene.frame_end = scene.frame_start + frames


def match_time(tick):
    seconds = max(0, tick) / TICK_RATE
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


def _on_hud(self, context):
    if SESSION.state == "ready":
        SESSION.set_hud(not self.hide_hud)


class RenderLockGameSettings(bpy.types.PropertyGroup):
    game_path: bpy.props.StringProperty(name="Deadlock folder", subtype='DIR_PATH',
                                        default="B:/SteamLibrary/steamapps/common/Deadlock")
    replay_path: bpy.props.StringProperty(name="Replay", subtype='FILE_PATH')
    start_tick: bpy.props.IntProperty(name="Start tick", min=0,
                                      description="Replay tick shown at the scene's first frame")
    end_tick: bpy.props.IntProperty(name="End tick", min=0, get=_get_end_tick, set=_set_end_tick,
                                    description="Replay tick shown at the scene's last frame "
                                                "(sets the scene's End frame)")
    width: bpy.props.IntProperty(name="Game width", default=0, min=0, max=7680,
                                 description="Game window width; 0 uses the scene's render resolution")
    height: bpy.props.IntProperty(name="Game height", default=0, min=0, max=4320,
                                  description="Game window height; 0 uses the scene's render resolution")
    hide_hud: bpy.props.BoolProperty(name="Hide health bars and HUD", default=True, update=_on_hud)
    cache_dir: bpy.props.StringProperty(name="Baked frames", subtype='DIR_PATH',
                                        default="//renderlock_game_frames")
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
        if not props.start_tick:
            SESSION.adopt_tick_scene = context.scene.name
        context.scene.view_settings.view_transform = 'Standard'
        width, height = game_window_size(context.scene)
        SESSION.start(bpy.path.abspath(props.game_path), replay, width, height, props.hide_hud)
        return {'FINISHED'}


class RENDERLOCK_OT_game_bake(bpy.types.Operator):
    """Play the scene's frame range once in the game and keep every frame for F12 / Render Animation"""
    bl_idname = "renderlock.game_bake"
    bl_label = "Bake game frames"

    def execute(self, context):
        import json
        scene = context.scene
        if SESSION.state != "ready" or scene.camera is None:
            self.report({'ERROR'}, "Connect to the game and set a scene camera first.")
            return {'CANCELLED'}
        fps = scene.render.fps / scene.render.fps_base
        if not 18 <= fps <= 120:
            self.report({'ERROR'}, "Use a scene frame rate from 18 to 120 fps for game frames.")
            return {'CANCELLED'}
        original = scene.frame_current
        frames = list(range(scene.frame_start, scene.frame_end + 1))
        poses, previous_yaw = [], None
        for frame in frames:
            scene.frame_set(frame)
            pose = list(camera_object_pose(scene, scene.camera))
            if previous_yaw is not None:  # continuous yaw for interpolation
                pose[4] += round((previous_yaw - pose[4]) / 360) * 360
            previous_yaw = pose[4]
            poses.append(tuple(pose))
        scene.frame_set(original)
        props = scene.renderlock_game
        start_tick = props.start_tick
        captured = SESSION.capture_range(start_tick, fps, poses)
        folder = cache_folder(scene)
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("frame_*.npy"):
            old.unlink()
        metas = {}
        for k, (meta, array) in captured.items():
            np.save(folder / f"frame_{frames[k]:06d}.npy", np.ascontiguousarray(array))
            metas[str(frames[k])] = {key: meta[key] for key in ("tick", "phase", "present", "seq", "width", "height")}
        (folder / "bake.json").write_text(json.dumps(dict(
            start_tick=start_tick, replay=bpy.path.abspath(props.replay_path), fps=fps,
            requested=len(frames), captured=len(metas), frames=metas), indent=1))
        missing = len(frames) - len(metas)
        level = {'WARNING'} if missing else {'INFO'}
        self.report(level, f"Baked {len(metas)} of {len(frames)} frames into {folder}")
        return {'FINISHED'}


class RENDERLOCK_OT_game_start_here(bpy.types.Operator):
    """Make the current frame show the replay tick the game is on now"""
    bl_idname = "renderlock.game_start_here"
    bl_label = "Start here"

    def execute(self, context):
        tick = SESSION.replay_tick() if SESSION.state == "ready" else None
        if tick is None:
            self.report({'ERROR'}, "Connect to the game first.")
            return {'CANCELLED'}
        scene = context.scene
        fps = scene.render.fps / scene.render.fps_base
        props = scene.renderlock_game
        follow, props.follow_timeline = props.follow_timeline, False
        props.start_tick = max(0, int(round(tick - (scene.frame_current - scene.frame_start) / fps * TICK_RATE)))
        props.follow_timeline = follow
        SESSION.current_tick = tick
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
        row = col.row(align=True)
        row.prop(props, "start_tick")
        row.operator(RENDERLOCK_OT_game_start_here.bl_idname, text="", icon='TIME')
        col.prop(props, "end_tick")
        scene = context.scene
        seconds = (scene.frame_end - scene.frame_start) / (scene.render.fps / scene.render.fps_base)
        col.label(text=f"Replay {match_time(props.start_tick)} to {match_time(props.end_tick)}"
                       f"  ({seconds:.2f} s, frames {scene.frame_start}-{scene.frame_end})")
        row = col.row(align=True)
        row.prop(props, "width")
        row.prop(props, "height")
        col.prop(props, "hide_hud")
        col.prop(props, "follow_timeline")
        col.prop(props, "cache_dir")
        row = layout.row()
        if SESSION.state in ("starting", "ready"):
            row.operator(RENDERLOCK_OT_game_disconnect.bl_idname, icon='CANCEL')
            row.operator(RENDERLOCK_OT_game_bake.bl_idname, icon='RENDER_ANIMATION')
        else:
            row.operator(RENDERLOCK_OT_game_connect.bl_idname, icon='PLAY')
        box = layout.box()
        box.label(text=f"State: {SESSION.state}")
        if SESSION.message:
            box.label(text=SESSION.message[:120])
        if SESSION.frame_meta:
            meta = SESSION.frame_meta
            box.label(text=f"Game shows tick {meta['tick']} ({match_time(meta['tick'])})  "
                           f"{meta['width']}×{meta['height']}  · {meta['seq']} frames streamed")
            want = render_size(context.scene)
            if (meta['width'], meta['height']) != want:
                box.label(text=f"Game window {meta['width']}×{meta['height']} is scaled to the "
                               f"{want[0]}×{want[1]} render.", icon='INFO')


@bpy.app.handlers.persistent
def _frame_changed(scene, _depsgraph=None):
    props = getattr(scene, "renderlock_game", None)
    if props is None or not props.follow_timeline or SESSION.state != "ready":
        return
    if scene.render.engine != ENGINE:
        return
    SESSION.request_tick(scene_tick(scene))


def _all_panels(base=None):
    for panel in (base or bpy.types.Panel).__subclasses__():
        yield panel
        yield from _all_panels(panel)


def _compatible_panels():
    """Generic Properties panels (output, camera, object, ...) that any engine can use.

    Blender marks these with BLENDER_RENDER; EEVEE/Cycles-only render settings are skipped.
    """
    return [p for p in _all_panels()
            if isinstance(getattr(p, "COMPAT_ENGINES", None), set) and "BLENDER_RENDER" in p.COMPAT_ENGINES]


CLASSES = (RenderLockGameSettings, RENDERLOCK_OT_game_connect, RENDERLOCK_OT_game_disconnect,
           RENDERLOCK_OT_game_bake, RENDERLOCK_OT_game_start_here,
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
    try:  # running the script again replaces the earlier registration
        unregister()
    except Exception:
        pass
    register()
