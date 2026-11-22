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
        self.wanted_exact = False
        self.live_playing = False
        self.suppress_follow = 0
        self.capturing = False       # a capture thread owns the frame stream
        self.preview = None          # newest frame it saw, for the viewport
        self.bake = None             # {"done", "total", "state", "error", ...} while baking

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
        if self.capturing:
            return  # the shot's own camera path drives the game
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
        pose = pose or self.last_pose or self.game_pose()
        project = Project(name="renderlock-view", keyframes=[_keyframe(0.0, pose)],
                          interpolation="linear", start_tick=int(tick), tick_rate=TICK_RATE,
                          standard_aspect=pose[7])
        project.validate()
        self.stream.set_camera(None)
        self.held_frame = None
        self.live_playing = False
        self.controller.prepare_native_recording(project)
        self.stream.set_camera(pose)
        self.current_tick = int(tick)

    def game_pose(self):
        """The camera the game shows now as (x, y, z, pitch, yaw, roll, hfov, aspect)."""
        try:
            status = self.controller._native_bridge().status()
            pose = list(status["applied_pose"] or status["original_pose"])
            fov = float(status.get("applied_fov") or status.get("original_fov") or 90.0)
            return (*pose[:6], fov, pose[6] if len(pose) > 6 and pose[6] else 16 / 9)
        except Exception:
            return None

    def quick_position(self, tick, pose):
        """Editing jump: seek, a short natural run-up and an exact pause, without reloading the replay."""
        from dolly.path import Project
        pose = pose or self.last_pose or self.game_pose()
        project = Project(name="renderlock-view", keyframes=[_keyframe(0.0, pose)],
                          interpolation="linear", start_tick=int(tick), tick_rate=TICK_RATE,
                          standard_aspect=pose[7])
        project.validate()
        controller = self.controller
        self.stream.set_camera(None)
        self.held_frame = None
        timings, began = {}, time.monotonic()
        try:
            with controller._op_lock:
                controller.stop()
                controller._stop_event.clear()
                positioned, start = self._quick_settle(project, int(tick), timings, began)
                controller.enter_native_flight(pose=project.evaluate(start))
                timings["camera"] = time.monotonic() - began
        finally:
            self.stream.set_camera(pose)
            self.last_timings = {k: round(v, 2) for k, v in timings.items()}
            print("[RenderLock game] jump timings", self.last_timings, flush=True)
        self.live_playing = False
        self.current_tick = int(tick)

    def _quick_settle(self, project, tick, timings, began):
        """Seek, settle two seconds of replay at 4x speed, pause exactly on `tick`. No reload.

        Caller holds the controller's op lock. Returns Dolly's (positioned, start).
        """
        controller = self.controller
        # The previous settle leaves a one-shot pause goal armed; resuming
        # clears it, otherwise the next forward jump stops at the old tick.
        controller._request("demo_resume; demo_pause", allow_error=True)
        timings["release"] = time.monotonic() - began
        offset = getattr(self, "seek_offset", None)
        if offset is not None and tick > 2 * TICK_RATE:
            # The replay's demo/server clock offset is fixed; the settle seeks
            # two seconds early itself, so skip the jump onto the tick.
            controller._last_seek_details = {"target_tick": tick, "actual_tick": tick,
                                             "verified": True, "game_tick_offset": offset}
            positioned, start = {"tick": tick}, 0.0
        else:
            positioned = controller._seek(project, 0)
            start = controller._seek_shot_time(project, 0, positioned)
            if controller._last_seek_details.get("verified"):
                self.seek_offset = controller._last_seek_details.get("game_tick_offset")
        timings["seek"] = time.monotonic() - began
        controller._warmup_timescale = 4  # 2 s of replay settles in about half a second
        try:
            warmed = controller._warm_replay_for_shot(project, start, positioned, calibrate=False)
            if not warmed.get("replay_warmup", {}).get("performed"):
                raise RuntimeError(warmed.get("replay_warmup", {}).get("reason", "not settled"))
            positioned = warmed
        except Exception:
            traceback.print_exc()
            # Fall back to the plain exact jump (also refreshes the clock offset).
            self.seek_offset = None
            positioned = controller._seek(project, 0)
            start = controller._seek_shot_time(project, 0, positioned)
        finally:
            controller._warmup_timescale = 1
            controller._request("demo_timescale 1", allow_error=True)
        timings["settle"] = time.monotonic() - began
        return positioned, start

    def toggle_live(self):
        """Dolly's pause/resume: the replay runs in real time while the Blender camera stays live."""
        if self.state != "ready" or self.seeking:
            return
        def run():
            try:
                status = self.controller.toggle_replay()
                self.live_playing = not bool(status.get("replay_paused", True))
                self.current_tick = self.replay_tick()
            except Exception as error:
                traceback.print_exc()
                self.message = f"Play/pause failed: {error}"
        threading.Thread(target=run, name="renderlock-game-live", daemon=True).start()

    def request_tick(self, tick, exact=False):
        self.wanted_tick = int(tick)
        self.wanted_exact = bool(exact)
        if self.state == "ready" and not self.seeking:
            self.seeking = True
            threading.Thread(target=self._seek_loop, name="renderlock-game-seek", daemon=True).start()

    def _seek_loop(self):
        try:
            while self.state == "ready" and self.wanted_tick is not None and self.wanted_tick != self.current_tick:
                target = self.wanted_tick
                self.message = f"Moving the replay to tick {target}…"
                place = self.position if self.wanted_exact else self.quick_position
                for attempt in range(3):  # a seek right after a replay reload can miss once
                    try:
                        place(max(0, target), self.last_pose)
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
        pose = pose or self.last_pose or self.game_pose()
        captured = self.capture_range(tick - lead, fps, [pose] * (frames + 1))
        if frames not in captured:
            raise RuntimeError(f"The run-up into tick {tick} did not deliver its last frame.")
        self.held_frame = captured[frames]
        self.current_tick = int(tick)

    def capture_range(self, start_tick, fps, poses, cancelled=lambda: False, timeout=600.0, clean=True):
        """Play the camera animation once, fixed-step, and keep each frame's game image.

        poses[k] is the camera for shot time k / fps. Returns {k: (meta, array)}.
        clean=True reloads the replay once first (no leftover effects, for final
        renders); clean=False only settles from where the replay is (previews).
        """
        from dolly.path import Project
        keys = [_keyframe(k / fps, pose) for k, pose in enumerate(poses)]
        project = Project(name="renderlock-render", keyframes=keys, interpolation="linear",
                          rotation_mode="unwrapped", start_tick=int(start_tick), tick_rate=TICK_RATE,
                          standard_aspect=poses[0][7])
        project.validate()
        controller, frames = self.controller, {}
        self.seeking = True
        self.capturing = True
        self.preview = None
        timings, began = {}, time.monotonic()
        self.last_timings = timings
        try:
            self.stream.set_camera(None)
            if clean:
                controller.prepare_native_recording(project)
                warmed, controller._recording_warmup = controller._recording_warmup, None
                controller._recording_replay = None
            else:
                with controller._op_lock:
                    controller.stop()
                    controller._stop_event.clear()
                    positioned, start = self._quick_settle(project, int(start_tick), timings, began)
                warmed = {"project": project.to_dict(), "start": start, "positioned": positioned}
            timings["prepared"] = round(time.monotonic() - began, 2)
            controller.set_export_timing(fps)
            while self.poll_frame() is not None:
                pass  # drain frames from before the shot
            # Dolly's play() reloads the replay again unless a recorder holds a
            # prepared take; hand it the replay positioned above instead.
            controller._prepare_native_shot_replay = lambda *_args, **_kw: warmed
            try:
                controller.play(project, speed=1, rate=60, frozen=False)
            finally:
                del controller._prepare_native_shot_replay
            timings["started"] = round(time.monotonic() - began, 2)
            deadline = time.monotonic() + timeout
            last = len(poses) - 1
            while time.monotonic() < deadline and not cancelled():
                got = self.poll_frame()
                if got is None:
                    if controller.status().get("shot_finished") and last in frames:
                        break
                    time.sleep(0.001)
                    continue
                self.preview = got
                phase = got[0]["phase"]
                k = int(round(phase * fps))
                if abs(phase * fps - k) < 0.05 and 0 <= k <= last and k not in frames:
                    frames[k] = got
                    if self.bake is not None:
                        self.bake["done"] = len(frames)
                if len(frames) == len(poses):
                    break
        finally:
            timings["captured"] = round(time.monotonic() - began, 2)
            print("[RenderLock game] bake timings", timings, flush=True)
            try:
                controller.clear_export_timing()
                controller.stop()
                controller.enter_native_flight(pose=None)
            except Exception:
                traceback.print_exc()
            self.current_tick = None
            self.capturing = False
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


_BAKE_INFO = {}


def shot_signature(scene):
    """Changes whenever anything that the baked frames depend on changes."""
    import hashlib
    import json
    camera = scene.camera
    props = scene.renderlock_game
    parts = [props.start_tick, bpy.path.abspath(props.replay_path), scene.frame_start, scene.frame_end,
             scene.render.fps, scene.render.fps_base, render_size(scene), camera.name if camera else None]
    if camera is not None:
        curves = [fc for target in (camera, camera.data) for fc in _fcurves(target)]
        for fc in curves:
            parts.append([fc.data_path, fc.array_index] + [
                [round(v, 5) for v in (*k.co, *k.handle_left, *k.handle_right)] + [k.interpolation]
                for k in fc.keyframe_points])
        parts.append([round(v, 5) for row in camera.matrix_world for v in row] + [round(camera.data.lens, 4)]
                     if not curves else None)
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def bake_info(scene):
    """bake.json of this scene's replay range, or None."""
    info = cache_folder(scene) / "bake.json"
    if not info.is_file():
        return None
    import json
    stamp = (str(info), info.stat().st_mtime_ns)
    if _BAKE_INFO.get("stamp") != stamp:
        _BAKE_INFO.update(stamp=stamp, bake=json.loads(info.read_text()))
    bake = _BAKE_INFO["bake"]
    if bake.get("start_tick") != scene.renderlock_game.start_tick or bake.get("replay") != \
            bpy.path.abspath(scene.renderlock_game.replay_path):
        return None
    return bake


def bake_is_current(scene, clean=False):
    bake = bake_info(scene)
    return bool(bake and bake.get("signature") == shot_signature(scene)
                and bake.get("captured") == bake.get("requested") and (bake.get("clean") or not clean))


def baked_frame(scene, frame, clean=False):
    """A frame captured by a bake of this scene's replay range, if any."""
    bake = bake_info(scene)
    if bake is None or (clean and not bake.get("clean")):
        return None
    path = cache_folder(scene) / f"frame_{frame:06d}.npy"
    meta = bake["frames"].get(str(frame))
    return (meta, np.load(path)) if meta and path.is_file() else None


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
        self._shown = None

    # Viewport: the view drives the game camera, the newest game frame is drawn.
    def view_update(self, context, depsgraph):
        pass

    def view_draw(self, context, depsgraph):
        region, rv3d = context.region, context.region_data
        if SESSION.state == "ready" and rv3d is not None and rv3d.is_perspective:
            hfov = 2 * math.atan(1.0 / rv3d.window_matrix[0][0])
            SESSION.push_camera(source_pose(rv3d.view_matrix.inverted(), hfov,
                                            region.width / max(1, region.height)))
        if SESSION.capturing:
            # A bake or run-up reads the stream itself; show what it last saw.
            got, shown = SESSION.preview, ("preview", SESSION.preview[0]["seq"] if SESSION.preview else None)
        elif _animation_playing(context):
            frame = context.scene.frame_current
            shown = ("baked", frame)
            got = baked_frame(context.scene, frame) if shown != self._shown else None
        else:
            got = SESSION.poll_frame()
            shown = ("live", got[0]["seq"] if got else None)
        if got is not None and shown == self._shown:
            got = None  # already on screen
        if got is not None:
            self._shown = shown
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
            x, y, w, h = 0, 0, region.width, region.height
            if self._shown and self._shown[0] == "baked":
                # Shot playback: the whole baked frame, letterboxed, never stretched.
                aspect = self._texture.width / max(1, self._texture.height)
                w, h = (region.width, region.width / aspect) if region.width / max(1, region.height) < aspect                     else (region.height * aspect, region.height)
                x, y = (region.width - w) / 2, (region.height - h) / 2
            draw_texture_2d(self._texture, (x, y), w, h)
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
            got = baked_frame(scene, frame, clean=True) if bake_is_current(scene, clean=True) else None
            if got is None:
                # Exact single frame: settle the replay on this tick, then wait for
                # consecutive game frames that show the scene camera.
                pose = camera_object_pose(scene, scene.camera)
                SESSION.last_pose = pose
                tick = scene_tick(scene)
                SESSION.request_tick(tick, exact=True)
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
    selected_key: bpy.props.IntProperty(name="Selected camera frame", default=-1)


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

    play_after: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    render_after: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    clean: bpy.props.BoolProperty(name="Reload replay first", default=True, options={'SKIP_SAVE'},
                                  description="Reload the replay once before baking, so no effects linger "
                                              "from earlier playback (final renders)")
    wait: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'},
                                 description="Block until done (scripts)")

    def execute(self, context):
        scene = context.scene
        if SESSION.state != "ready" or scene.camera is None:
            self.report({'ERROR'}, "Connect to the game and set a scene camera first.")
            return {'CANCELLED'}
        if SESSION.bake is not None:
            self.report({'ERROR'}, "A bake is already running.")
            return {'CANCELLED'}
        fps = scene.render.fps / scene.render.fps_base
        if not 18 <= fps <= 120:
            self.report({'ERROR'}, "Use a scene frame rate from 18 to 120 fps for game frames.")
            return {'CANCELLED'}
        camera = shot_camera(scene)
        if camera is not None:
            fix_shot_rotation(camera)
        original = scene.frame_current
        frames = list(range(scene.frame_start, scene.frame_end + 1))
        poses, previous_yaw = [], None
        SESSION.suppress_follow += 1
        try:
            for frame in frames:
                scene.frame_set(frame)
                pose = list(camera_object_pose(scene, scene.camera))
                if previous_yaw is not None:  # continuous yaw for interpolation
                    pose[4] += round((previous_yaw - pose[4]) / 360) * 360
                previous_yaw = pose[4]
                poses.append(tuple(pose))
            scene.frame_set(original)
        finally:
            SESSION.suppress_follow -= 1
        props = scene.renderlock_game
        SESSION.bake = dict(state="baking", done=0, total=len(frames), error=None, scene=scene.name,
                            play_after=self.play_after, render_after=self.render_after, cancel=False,
                            began=time.monotonic())
        args = (props.start_tick, fps, poses, frames, cache_folder(scene), bpy.path.abspath(props.replay_path),
                self.clean, shot_signature(scene))
        if self.wait:
            _bake_worker(*args)
            return _bake_finished(self) or {'FINISHED'}
        # The game plays the shot in a thread so Blender stays responsive.
        threading.Thread(target=_bake_worker, args=args, name="renderlock-game-bake", daemon=True).start()
        bpy.app.timers.register(_bake_timer, first_interval=0.2)
        self.report({'INFO'}, f"Baking {len(frames)} frames in the game…")
        return {'FINISHED'}


def _bake_worker(start_tick, fps, poses, frames, folder, replay, clean, signature):
    import json
    bake = SESSION.bake
    try:
        captured = SESSION.capture_range(start_tick, fps, poses, cancelled=lambda: bake["cancel"], clean=clean)
        if bake["cancel"]:
            bake["state"] = "cancelled"
            return
        bake["state"] = "saving"
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("frame_*.npy"):
            old.unlink()
        metas = {}
        for k, (meta, array) in captured.items():
            np.save(folder / f"frame_{frames[k]:06d}.npy", np.ascontiguousarray(array))
            metas[str(frames[k])] = {key: meta[key] for key in ("tick", "phase", "present", "seq", "width", "height")}
        (folder / "bake.json").write_text(json.dumps(dict(
            start_tick=start_tick, replay=replay, fps=fps, clean=bool(clean), signature=signature,
            requested=len(frames), captured=len(metas), frames=metas), indent=1))
        bake.update(state="done", done=len(metas), folder=str(folder))
    except Exception as error:
        traceback.print_exc()
        bake.update(state="failed", error=str(error))


def _bake_finished(operator=None):
    """Main thread, after the worker: report and optionally start playback."""
    bake, SESSION.bake = SESSION.bake, None
    if bake is None:
        return None
    if bake["state"] == "failed":
        SESSION.message = f"Bake failed: {bake['error']}"
    elif bake["state"] == "cancelled":
        SESSION.message = "Bake cancelled."
    else:
        missing = bake["total"] - bake["done"]
        SESSION.message = (f"Baked {bake['done']} of {bake['total']} frames in "
                           f"{time.monotonic() - bake['began']:.0f} s"
                           + (f" ({missing} missing)" if missing else "") + ".")
        scene = bpy.data.scenes.get(bake["scene"])
        if bake["play_after"] and scene is not None:
            _play_timeline(scene)
        if bake["render_after"] and scene is not None and not missing:
            _render_animation()
    if operator is not None:
        operator.report({'WARNING'} if bake["state"] != "done" else {'INFO'}, SESSION.message)
    _redraw_views()
    return None


def _bake_timer():
    if SESSION.bake is None:
        return None
    if SESSION.bake["state"] in ("done", "failed", "cancelled"):
        return _bake_finished()
    _redraw_views()
    return 0.2


def _redraw_views():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ('VIEW_3D', 'PROPERTIES'):
                area.tag_redraw()


def _render_animation():
    """Render Animation in a render window (works from timers too)."""
    for window in bpy.context.window_manager.windows:
        area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), window.screen.areas[0])
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.render.render('INVOKE_DEFAULT', animation=True)
        return


def _play_timeline(scene):
    """Start timeline playback from the shot start (works from timers too)."""
    set_frame_quietly(scene, scene.frame_start)
    if _animation_playing():
        return
    for window in bpy.context.window_manager.windows:
        area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
        if area is None:
            continue
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.animation_play()
        return


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
    if scene.render.engine != ENGINE or SESSION.suppress_follow or SESSION.live_playing \
            or SESSION.bake is not None:
        return
    if _animation_playing(bpy.context):
        return  # Play shot previews baked frames; no seeking while the timeline plays
    SESSION.request_tick(scene_tick(scene))


# --------------------------------------------------------------------------- Dolly-style shot

SHOT_CAMERA = "RenderLock Shot"


def _animation_playing(context=None):
    try:
        windows = (context or bpy.context).window_manager.windows
        return any(window.screen and window.screen.is_animation_playing for window in windows)
    except Exception:
        return False


def _fcurves(id_data):
    """F-curves of an ID's action (layered actions in Blender 4.4+)."""
    animation = getattr(id_data, "animation_data", None)
    if animation is None or animation.action is None:
        return []
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        bag = action_get_channelbag_for_slot(animation.action, animation.action_slot)
        return list(bag.fcurves) if bag else []
    except ImportError:
        return list(animation.action.fcurves)


def shot_camera(scene, create=False):
    camera = scene.camera if scene.camera is not None and scene.camera.get("renderlock_shot") else None
    camera = camera or next((o for o in scene.objects if o.get("renderlock_shot")), None)
    if camera is None and create:
        data = bpy.data.cameras.new(SHOT_CAMERA)
        data.sensor_fit = 'HORIZONTAL'
        data.sensor_width = 36.0
        data.clip_start, data.clip_end = 0.05, 2000.0
        camera = bpy.data.objects.new(SHOT_CAMERA, data)
        camera["renderlock_shot"] = True
        camera.rotation_mode = 'XYZ'
        scene.collection.objects.link(camera)
    if camera is not None and scene.camera is not camera:
        scene.camera = camera
    return camera


def shot_keys(camera):
    """Frames of the shot's captured cameras, in order."""
    if camera is None:
        return []
    frames = set()
    for curve in _fcurves(camera):
        if curve.data_path in ("location", "rotation_euler"):
            frames.update(int(round(point.co.x)) for point in curve.keyframe_points)
    return sorted(frames)


def frame_for_tick(scene, tick):
    fps = scene.render.fps / scene.render.fps_base
    props = scene.renderlock_game
    return scene.frame_start + int(round((tick - props.start_tick) / TICK_RATE * fps))


def set_frame_quietly(scene, frame):
    """Move the timeline without making the game seek (the game is already there)."""
    SESSION.suppress_follow += 1
    try:
        scene.frame_set(frame)
    finally:
        SESSION.suppress_follow -= 1


def _view3d(context):
    """(space, region_3d, window region) of the 3D view the operator runs in, or the first one."""
    areas = [context.area] if context.area and context.area.type == 'VIEW_3D' else []
    areas += [a for w in context.window_manager.windows for a in w.screen.areas if a.type == 'VIEW_3D']
    for area in areas:
        space = area.spaces.active
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if space and space.region_3d and region:
            return space, space.region_3d, region
    return None, None, None


def current_view(context):
    """(world matrix, lens mm on a 36 mm sensor) of what the viewport shows now.

    Read this before moving the timeline: a frame change snaps an animated
    camera back onto its keys.
    """
    _space, rv3d, _region = _view3d(context)
    if rv3d is None:
        return None
    if rv3d.view_perspective == 'CAMERA' and context.scene.camera is not None:
        camera = context.scene.camera
        return camera.matrix_world.copy(), camera.data.lens
    hfov = 2 * math.atan(1.0 / rv3d.window_matrix[0][0])
    return rv3d.view_matrix.inverted(), 36.0 / (2 * math.tan(hfov / 2))


def free_view_at(context, matrix, lens):
    """Put the viewport (not the shot camera) at this pose so flying never moves a key."""
    from mathutils import Vector
    space, rv3d, _region = _view3d(context)
    if rv3d is None:
        return
    rv3d.view_perspective = 'PERSP'
    location, rotation, _scale = matrix.decompose()
    rv3d.view_rotation = rotation
    rv3d.view_location = location + rotation @ Vector((0.0, 0.0, -rv3d.view_distance))
    space.lens = 2.0 * lens  # the viewport uses a 72 mm sensor at its default zoom


def upright_euler(rotation, reference):
    """XYZ euler for a 3x3 rotation, on the branch without a half-turn of roll.

    Every orientation has two XYZ eulers, (x, y, z) and (x+pi, pi-y, z+pi).
    Blender interpolates each angle separately, so two keys on different
    branches make the camera roll over in between.
    """
    from mathutils import Euler
    a = rotation.to_euler('XYZ')
    b = Euler((a.x + math.pi, math.pi - a.y, a.z + math.pi), 'XYZ')
    best = min((a, b), key=lambda e: abs(math.remainder(e.y, 2 * math.pi)))
    best.make_compatible(reference)
    return best


def fix_shot_rotation(camera):
    """Move every rotation key onto the upright branch, continuous with the key before it."""
    from mathutils import Euler
    curves = {fc.array_index: fc for fc in _fcurves(camera) if fc.data_path == "rotation_euler"}
    if len(curves) != 3:
        return
    frames = sorted({round(k.co.x, 3) for fc in curves.values() for k in fc.keyframe_points})
    previous = None
    for frame in frames:
        current = Euler([curves[i].evaluate(frame) for i in range(3)], 'XYZ')
        fixed = upright_euler(current.to_matrix(), previous or current)
        for i in range(3):
            delta = fixed[i] - current[i]
            if abs(delta) < 1e-6:
                continue
            for key in curves[i].keyframe_points:
                if abs(key.co.x - frame) < 1e-3:
                    key.co.y += delta
                    key.handle_left.y += delta
                    key.handle_right.y += delta
        previous = fixed
    for fc in curves.values():
        fc.update()


def capture_view(context, frame, view):
    """Key the shot camera at `frame` with `view` (from current_view)."""
    scene = context.scene
    camera = shot_camera(scene, create=True)
    if view is not None:
        matrix, lens = view
        camera.location = matrix.translation
        camera.rotation_euler = upright_euler(matrix.to_3x3().normalized(), camera.rotation_euler.copy())
        camera.data.sensor_fit = 'HORIZONTAL'
        camera.data.sensor_width = 36.0
        camera.data.lens = lens
    camera.keyframe_insert("location", frame=frame)
    camera.keyframe_insert("rotation_euler", frame=frame)
    camera.data.keyframe_insert("lens", frame=frame)
    fix_shot_rotation(camera)
    if view is not None:
        free_view_at(context, *view)  # keep flying from here, like Dolly's free camera
    return camera


def seek_to_frame(scene, frame):
    """Move the timeline and the replay together (works with Follow timeline off too)."""
    set_frame_quietly(scene, frame)
    if SESSION.state == "ready":
        SESSION.request_tick(scene_tick(scene))


def _need_ready(operator):
    if SESSION.state != "ready":
        operator.report({'ERROR'}, "Connect to the game first.")
        return False
    return True


class _ShotOperator:
    @classmethod
    def poll(cls, context):
        return context.engine == ENGINE and SESSION.state == "ready" and SESSION.bake is None


class RENDERLOCK_OT_shot_capture(_ShotOperator, bpy.types.Operator):
    """Capture camera: add a shot camera at the replay moment shown now (first capture starts the shot)"""
    bl_idname = "renderlock.shot_capture"
    bl_label = "Capture camera"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        props = scene.renderlock_game
        tick = SESSION.replay_tick()
        if tick is None:
            self.report({'ERROR'}, "The replay tick is not known yet.")
            return {'CANCELLED'}
        view = current_view(context)
        camera = shot_camera(scene)
        keys = shot_keys(camera)
        if not keys:
            # Like Dolly: the first capture is time zero and sets the shot's start tick.
            props.start_tick = tick
            frame = scene.frame_start
        else:
            frame = frame_for_tick(scene, tick)
            if frame < scene.frame_start:
                self.report({'ERROR'}, "This moment is before the shot start. Move the replay forward.")
                return {'CANCELLED'}
        replaced = frame in keys
        set_frame_quietly(scene, frame)
        SESSION.current_tick = tick
        camera = capture_view(context, frame, view)
        keys = shot_keys(camera)
        scene.frame_end = max(keys[-1], scene.frame_start)
        props.selected_key = frame
        number = keys.index(frame) + 1
        if replaced:
            self.report({'WARNING'}, f"Camera {number} replaced: the replay is still at {match_time(tick)}. "
                                     f"Move the replay forward before capturing the next camera.")
        else:
            self.report({'INFO'}, f"Camera {number} at {match_time(tick)}")
        return {'FINISHED'}


class RENDERLOCK_OT_shot_replace(_ShotOperator, bpy.types.Operator):
    """Replace the selected camera with the current view, keeping its time"""
    bl_idname = "renderlock.shot_replace"
    bl_label = "Replace camera"
    bl_options = {'REGISTER', 'UNDO'}
    frame: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        scene = context.scene
        keys = shot_keys(shot_camera(scene))
        frame = self.frame if self.frame >= 0 else scene.renderlock_game.selected_key
        if frame not in keys:
            self.report({'ERROR'}, "Select a captured camera first.")
            return {'CANCELLED'}
        capture_view(context, frame, current_view(context))
        return {'FINISHED'}


class RENDERLOCK_OT_shot_delete(_ShotOperator, bpy.types.Operator):
    """Delete this camera from the shot"""
    bl_idname = "renderlock.shot_delete"
    bl_label = "Delete camera"
    bl_options = {'REGISTER', 'UNDO'}
    frame: bpy.props.IntProperty()

    def execute(self, context):
        camera = shot_camera(context.scene)
        for target in (camera, camera.data if camera else None):
            for curve in _fcurves(target) if target is not None else []:
                for point in [k for k in curve.keyframe_points if int(round(k.co.x)) == self.frame]:
                    curve.keyframe_points.remove(point)
        keys = shot_keys(camera)
        if keys:
            context.scene.frame_end = max(keys[-1], context.scene.frame_start)
        return {'FINISHED'}


class RENDERLOCK_OT_shot_goto(_ShotOperator, bpy.types.Operator):
    """Go to this camera: the replay moves to its moment and the view looks through the shot"""
    bl_idname = "renderlock.shot_goto"
    bl_label = "Go to camera"
    frame: bpy.props.IntProperty(default=-1)
    step: bpy.props.IntProperty(default=0, description="-1 previous camera, +1 next camera")

    def execute(self, context):
        scene = context.scene
        keys = shot_keys(shot_camera(scene))
        if not keys:
            return {'CANCELLED'}
        frame = self.frame
        if self.step:
            current = scene.frame_current
            later = [k for k in keys if k > current] if self.step > 0 else [k for k in keys if k < current][::-1]
            frame = later[0] if later else (keys[-1] if self.step > 0 else keys[0])
        scene.renderlock_game.selected_key = frame
        seek_to_frame(scene, frame)
        camera = shot_camera(scene)
        if camera is not None:  # fly on from this camera's view; the key itself stays put
            free_view_at(context, camera.matrix_world.copy(), camera.data.lens)
        return {'FINISHED'}


class RENDERLOCK_OT_replay_step(_ShotOperator, bpy.types.Operator):
    """Seek the replay backward or forward"""
    bl_idname = "renderlock.replay_step"
    bl_label = "Seek replay"
    seconds: bpy.props.FloatProperty(default=1.0)

    def execute(self, context):
        scene = context.scene
        fps = scene.render.fps / scene.render.fps_base
        if SESSION.live_playing:
            SESSION.toggle_live()
        seek_to_frame(scene, scene.frame_current + int(round(self.seconds * fps)))
        return {'FINISHED'}


class RENDERLOCK_OT_replay_toggle(_ShotOperator, bpy.types.Operator):
    """Pause / resume replay time (the camera stays live)"""
    bl_idname = "renderlock.replay_toggle"
    bl_label = "Pause / resume replay"

    def execute(self, context):
        SESSION.toggle_live()
        return {'FINISHED'}


class RENDERLOCK_OT_shot_play(_ShotOperator, bpy.types.Operator):
    """Play shot: bake the shot in the game, then play it back in the viewport"""
    bl_idname = "renderlock.shot_play"
    bl_label = "Play shot"

    def execute(self, context):
        scene = context.scene
        if SESSION.live_playing:
            SESSION.toggle_live()
        if len(shot_keys(shot_camera(scene))) < 2:
            self.report({'ERROR'}, "Capture at least two cameras at different replay moments first.")
            return {'CANCELLED'}
        if _animation_playing(context):
            bpy.ops.screen.animation_cancel(restore_frame=False)
        fix_shot_rotation(shot_camera(scene))
        if bake_is_current(scene):
            _play_timeline(scene)  # nothing changed since the last bake
            return {'FINISHED'}
        # Preview: settle from where the replay is (no reload), bake in the
        # background, then play the baked frames in the viewport.
        return bpy.ops.renderlock.game_bake(play_after=True, clean=False)


class RENDERLOCK_OT_shot_render(_ShotOperator, bpy.types.Operator):
    """Render shot: bake the shot from a freshly loaded replay (if needed), then Render Animation"""
    bl_idname = "renderlock.shot_render"
    bl_label = "Render shot"

    def execute(self, context):
        scene = context.scene
        if SESSION.live_playing:
            SESSION.toggle_live()
        if _animation_playing(context):
            bpy.ops.screen.animation_cancel(restore_frame=False)
        fix_shot_rotation(shot_camera(scene))
        if bake_is_current(scene, clean=True):
            _render_animation()
            return {'FINISHED'}
        return bpy.ops.renderlock.game_bake(render_after=True, clean=True)


class RENDERLOCK_OT_shot_stop(_ShotOperator, bpy.types.Operator):
    """Stop shot playback or replay time"""
    bl_idname = "renderlock.shot_stop"
    bl_label = "Stop"

    @classmethod
    def poll(cls, context):
        return context.engine == ENGINE and SESSION.state == "ready"

    def execute(self, context):
        if SESSION.bake is not None:
            SESSION.bake["cancel"] = True
        if _animation_playing(context):
            bpy.ops.screen.animation_cancel(restore_frame=False)
        if SESSION.live_playing:
            SESSION.toggle_live()
        return {'FINISHED'}


# Dolly editor actions -> RenderLock operators (keys follow the user's Dolly settings).
DOLLY_ACTIONS = {
    "capture": ("renderlock.shot_capture", {}),
    "replace": ("renderlock.shot_replace", {}),
    "play_pause": ("renderlock.replay_toggle", {}),
    "play_path": ("renderlock.shot_play", {}),
    "stop": ("renderlock.shot_stop", {}),
    "previous_view": ("renderlock.shot_goto", {"step": -1}),
    "next_view": ("renderlock.shot_goto", {"step": 1}),
    "seek_back": ("renderlock.replay_step", {"seconds": -1.0}),
    "seek_forward": ("renderlock.replay_step", {"seconds": 1.0}),
}
_BLENDER_KEYS = {
    "Mouse4": "BUTTON4MOUSE", "Mouse5": "BUTTON5MOUSE", "MiddleMouse": "MIDDLEMOUSE",
    "Space": "SPACE", "Tab": "TAB", "Enter": "RET", "Escape": "ESC", "Backspace": "BACK_SPACE",
    "Insert": "INSERT", "Delete": "DEL", "Home": "HOME", "End": "END", "PageUp": "PAGE_UP",
    "PageDown": "PAGE_DOWN", "Left": "LEFT_ARROW", "Up": "UP_ARROW", "Right": "RIGHT_ARROW",
    "Down": "DOWN_ARROW", "Comma": "COMMA", "Period": "PERIOD", "Minus": "MINUS", "Equals": "EQUAL",
    "LeftBracket": "LEFT_BRACKET", "RightBracket": "RIGHT_BRACKET",
    **dict(zip("0123456789", ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"))),
}
_KEYMAP_ITEMS = []
KEY_LABELS = {}


def dolly_bindings():
    """The user's Dolly editor bindings (Dolly defaults when Dolly has no settings)."""
    try:
        from dolly.settings import load_settings
        return dict(load_settings().action_bindings)
    except Exception:
        traceback.print_exc()
        from dolly.editor_actions import default_action_bindings
        return dict(default_action_bindings())


def register_keymap():
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="3D View", space_type='VIEW_3D')
    for action, binding in dolly_bindings().items():
        if action not in DOLLY_ACTIONS or binding is None:
            continue
        key = _BLENDER_KEYS.get(binding.key, binding.key)
        idname, properties = DOLLY_ACTIONS[action]
        try:
            item = keymap.keymap_items.new(idname, key, 'PRESS', ctrl=binding.ctrl, alt=binding.alt,
                                           shift=binding.shift)
        except Exception:
            print("[RenderLock game] skipped key", binding.key, "for", action)
            continue
        for name, value in properties.items():
            setattr(item.properties, name, value)
        _KEYMAP_ITEMS.append((keymap, item))
        label = "+".join([m for m, on in (("Ctrl", binding.ctrl), ("Alt", binding.alt), ("Shift", binding.shift)) if on]
                         + [binding.key])
        KEY_LABELS[action] = label


def unregister_keymap():
    for keymap, item in _KEYMAP_ITEMS:
        try:
            keymap.keymap_items.remove(item)
        except Exception:
            pass
    _KEYMAP_ITEMS.clear()
    KEY_LABELS.clear()


def _key(action):
    label = KEY_LABELS.get(action)
    return f"  ({label})" if label else ""


_HIDDEN_OVERLAYS = {}


def _shot_overlays(playing):
    """Hide viewport overlays (camera outlines, gizmos, grid) while a shot plays back; restore after."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            key = space.as_pointer()
            if playing and space.shading.type == 'RENDERED' and key not in _HIDDEN_OVERLAYS:
                _HIDDEN_OVERLAYS[key] = (space.overlay.show_overlays, space.show_gizmo)
                space.overlay.show_overlays = False
                space.show_gizmo = False
            elif not playing and key in _HIDDEN_OVERLAYS:
                space.overlay.show_overlays, space.show_gizmo = _HIDDEN_OVERLAYS.pop(key)
    if not playing:
        _HIDDEN_OVERLAYS.clear()


def _live_timer():
    """While replay time runs, the timeline follows the game (Dolly's P)."""
    try:
        playing = _animation_playing() and any(s.render.engine == ENGINE for s in bpy.data.scenes)
        if playing or _HIDDEN_OVERLAYS:
            _shot_overlays(playing)
    except Exception:
        traceback.print_exc()
    try:
        if SESSION.state == "ready" and SESSION.live_playing:
            tick = SESSION.replay_tick()
            for scene in bpy.data.scenes:
                if scene.render.engine == ENGINE and tick is not None:
                    frame = frame_for_tick(scene, tick)
                    if frame != scene.frame_current:
                        set_frame_quietly(scene, frame)
                    SESSION.current_tick = tick
    except Exception:
        traceback.print_exc()
    return 0.05


class RENDERLOCK_PT_shot(bpy.types.Panel):
    bl_label = "Shot"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RenderLock"

    @classmethod
    def poll(cls, context):
        return context.engine == ENGINE

    def draw(self, context):
        scene = context.scene
        props = scene.renderlock_game
        layout = self.layout
        if SESSION.state not in ("starting", "ready"):
            layout.prop(props, "replay_path", text="")
            layout.operator(RENDERLOCK_OT_game_connect.bl_idname, icon='PLAY')
            if SESSION.message:
                layout.label(text=SESSION.message[:80])
            return
        if SESSION.state == "starting":
            layout.label(text="Starting Deadlock…", icon='TIME')
            layout.label(text=SESSION.message[:80])
            return
        if SESSION.bake is not None:
            bake = SESSION.bake
            box = layout.box()
            box.label(text=f"Baking in the game: {bake['done']} / {bake['total']} frames"
                      if bake["state"] == "baking" else "Saving baked frames…", icon='RENDER_ANIMATION')
            box.operator(RENDERLOCK_OT_shot_stop.bl_idname, text="Cancel", icon='CANCEL')
            return
        tick = SESSION.frame_meta["tick"] if SESSION.frame_meta else SESSION.current_tick
        box = layout.box()
        status = "playing" if SESSION.live_playing else ("moving…" if SESSION.seeking else "paused")
        box.label(text=f"Replay {match_time(tick or 0)}   tick {tick}   {status}", icon='TIME')
        row = box.row(align=True)
        step = row.operator(RENDERLOCK_OT_replay_step.bl_idname, text="-1 s", icon='REW')
        step.seconds = -1.0
        row.operator(RENDERLOCK_OT_replay_toggle.bl_idname, text="",
                     icon='PAUSE' if SESSION.live_playing else 'PLAY')
        step = row.operator(RENDERLOCK_OT_replay_step.bl_idname, text="+1 s", icon='FF')
        step.seconds = 1.0
        box.label(text=f"Keys{_key('seek_back')}{_key('play_pause')}{_key('seek_forward')}")

        layout.operator(RENDERLOCK_OT_shot_capture.bl_idname, text="Capture camera" + _key("capture"),
                        icon='OUTLINER_OB_CAMERA')
        camera = shot_camera(scene)
        keys = shot_keys(camera)
        if keys:
            fps = scene.render.fps / scene.render.fps_base
            first, last = keys[0], keys[-1]
            start = props.start_tick + (first - scene.frame_start) / fps * TICK_RATE
            end = props.start_tick + (last - scene.frame_start) / fps * TICK_RATE
            layout.label(text=f"{len(keys)} cameras · {match_time(start)} → {match_time(end)} "
                              f"({(last - first) / fps:.2f} s)")
            column = layout.column(align=True)
            for index, frame in enumerate(keys, 1):
                row = column.row(align=True)
                tick_at = props.start_tick + (frame - scene.frame_start) / fps * TICK_RATE
                selected = frame == props.selected_key
                go = row.operator(RENDERLOCK_OT_shot_goto.bl_idname, depress=selected,
                                  text=f"Camera {index}   {match_time(tick_at)}")
                go.frame = frame
                row.operator(RENDERLOCK_OT_shot_replace.bl_idname, text="", icon='FILE_REFRESH').frame = frame
                row.operator(RENDERLOCK_OT_shot_delete.bl_idname, text="", icon='X').frame = frame
        else:
            layout.label(text="Fly the viewport, then Capture to start a shot.")
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator(RENDERLOCK_OT_shot_play.bl_idname, text="Play shot" + _key("play_path"), icon='PLAY')
        row.operator(RENDERLOCK_OT_shot_stop.bl_idname, text="", icon='SNAP_FACE')
        layout.operator(RENDERLOCK_OT_shot_render.bl_idname, text="Render shot", icon='RENDER_ANIMATION')
        if keys:
            ready = ("render-ready" if bake_is_current(scene, clean=True)
                     else "preview baked" if bake_is_current(scene) else "not baked yet")
            layout.label(text=f"Shot frames: {ready}", icon='CHECKMARK' if ready != "not baked yet" else 'DOT')
        if SESSION.message:
            layout.label(text=SESSION.message[:80])


class RENDERLOCK_PT_shot_settings(bpy.types.Panel):
    bl_label = "Game settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RenderLock"
    bl_parent_id = "RENDERLOCK_PT_shot"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.engine == ENGINE

    def draw(self, context):
        props = context.scene.renderlock_game
        layout = self.layout
        layout.prop(props, "hide_hud")
        layout.prop(props, "follow_timeline")
        layout.prop(props, "cache_dir")
        if SESSION.state in ("starting", "ready"):
            layout.operator(RENDERLOCK_OT_game_disconnect.bl_idname, icon='CANCEL')


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
           RENDERLOCK_OT_shot_capture, RENDERLOCK_OT_shot_replace, RENDERLOCK_OT_shot_delete,
           RENDERLOCK_OT_shot_goto, RENDERLOCK_OT_replay_step, RENDERLOCK_OT_replay_toggle,
           RENDERLOCK_OT_shot_play, RENDERLOCK_OT_shot_render, RENDERLOCK_OT_shot_stop,
           RenderLockGameEngine, RENDERLOCK_PT_game, RENDERLOCK_PT_shot, RENDERLOCK_PT_shot_settings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.renderlock_game = bpy.props.PointerProperty(type=RenderLockGameSettings)
    for panel in _compatible_panels():
        panel.COMPAT_ENGINES.add(ENGINE)
    for handler in [h for h in bpy.app.handlers.frame_change_post if h.__name__ == "_frame_changed"]:
        bpy.app.handlers.frame_change_post.remove(handler)  # an earlier run of this script
    bpy.app.handlers.frame_change_post.append(_frame_changed)
    register_keymap()
    if not bpy.app.timers.is_registered(_live_timer):
        bpy.app.timers.register(_live_timer, first_interval=0.5, persistent=True)


def unregister():
    unregister_keymap()
    if bpy.app.timers.is_registered(_live_timer):
        bpy.app.timers.unregister(_live_timer)
    for handler in [h for h in bpy.app.handlers.frame_change_post if h.__name__ == "_frame_changed"]:
        bpy.app.handlers.frame_change_post.remove(handler)
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
