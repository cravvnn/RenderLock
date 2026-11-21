"""Age replay effects through normal simulation before an authored shot starts."""
from copy import deepcopy
import logging
import math
import re
import time

from .demo_packets import packet_index

WARMUP_SECONDS = 2.0
LOG = logging.getLogger("dolly")
_SEEK_CLOCK = re.compile(
    r"Demo Skipping:\s*skipping(?: forward)? to demo tick (\d+)\s+\(game tick (\d+)\)")


def seek_game_tick_offset(output, target):
    """Use the engine's fresh seek response, never a guessed clock offset."""
    offsets = {int(game) - int(demo) for demo, game in _SEEK_CLOCK.findall(output)
               if int(demo) == target and 0 <= int(game) <= 0x7fffffff}
    return offsets.pop() if len(offsets) == 1 else None


class ReplayWarmupMixin:
    def _warm_replay_for_shot(self, project, start, positioned, *, calibrate=False):
        """Seek earlier, simulate normally, and let the engine pause exactly.

        A paused render-frame wait cannot age game particles. The native HOLD
        keeps the opening camera visible while the replay runs; no warm-up frames
        are recorded. Once the target is reached, never issue a correcting seek.
        """
        details = {"performed": False, "seconds": WARMUP_SECONDS,
                   "target_tick": positioned["tick"]}
        result = dict(positioned, replay_warmup=details)
        target = int(positioned["tick"])
        target_seek = deepcopy(self._last_seek_details)
        offset = target_seek.get("game_tick_offset")
        if not self._supports_native_flight():
            details["reason"] = "native_editor_unavailable"
            return result
        if (offset is None or not target_seek.get("verified") or
                target_seek.get("actual_tick", target_seek.get("target_tick")) != target):
            details["reason"] = "seek_clock_unavailable"
            return result
        lead = target - int(round(WARMUP_SECONDS * project.tick_rate))
        if lead < 2:
            details["reason"] = "insufficient_replay_history"
            return result
        if self._console_command_available("demo_pauseatservertick") is not True:
            details["reason"] = "pause_command_unavailable"
            return result
        if self._recorder_active():
            raise RuntimeError("Replay effect settling must finish before recording starts.")
        self._replay_warmup_active = True
        try:
            self._check_position_cancelled()
            session, demo = self._session, self._demo
            bridge = self._native_bridge()
            frame = project.evaluate(start)
            frame["cvars"] = {}
            if calibrate:
                # Keep the existing camera displacement/return guard, and snapshot
                # its lens before it writes. Calibration recovery precedes settling.
                self._snapshot(project)
                self._position_direct_frame(frame, target)
            index = packet_index(demo, check_cancelled=self._check_position_cancelled)
            if index is not None:
                following = index.following(lead)
                if following is None or following >= target:
                    details["reason"] = "no_lead_in_packet"
                    return result
                lead = following
            self._check_position_cancelled()
            self._release_native_camera()
            beginning = self._seek_tick(lead)
            if (beginning["tick"] >= target or
                    self._last_seek_details.get("game_tick_offset") != offset):
                raise RuntimeError("The replay clock changed while preparing effect settling.")
            server_target = target + offset
            if not 0 < server_target <= 0x7fffffff:
                raise RuntimeError("The replay did not expose a valid effect-settling pause tick.")
            self._check_position_cancelled()
            self._native_active = True
            self._native_manual = False
            # ARMED frozen paths hold their first authored camera/lens. They do not
            # advance the path while the game simulates the preceding replay time.
            # Warm only the game's replay effects. Authored DOF/weather and
            # other Dolly effects must begin with the actual shot, not early.
            view_project = type(project).from_dict(project.to_dict())
            view_project.tracks = []
            view_project.setup_values = {}
            view_project.confetti_enabled = False
            bridge.prepare(view_project, start, 1.0, True, demo.name)
            self._detach_spectator_view()
            before = bridge.status()
            self._require_native_demo(before)
            self._message("Letting replay effects settle before the shot…", playing=False)
            goal_owned = False
            observation = {"samples": 0, "first": None, "last": None, "ahead_samples": 0}
            details["pause_observation"] = observation
            self._last_seek_details["replay_warmup"] = details

            def check_context():
                self._check_position_cancelled()
                if self._session is not session or self._demo != demo:
                    raise RuntimeError("The game session changed during effect settling.")
                self._require_connection()

            try:
                check_context()
                self._demo_speed_changed = True
                goal_owned = True
                # resume CLEARS a previous goal. Arm the stock one-shot command last.
                self._request(f"demo_timescale 1; demo_resume; demo_pauseatservertick {server_target}")
                deadline = time.perf_counter() + 8.0
                last_frame = int(before["frame_count"])
                first_paused_frame = None
                stable_frames = 0
                while True:
                    check_context()
                    current = bridge.status()
                    check_context()
                    self._require_native_demo(current)
                    sample = {"tick": current["tick"], "paused": current["paused"],
                              "frame": int(current["frame_count"])}
                    observation["samples"] += 1
                    if observation["first"] is None:
                        observation["first"] = sample
                    observation["last"] = sample
                    if current["tick"] > target:
                        observation.setdefault("first_ahead", sample)
                        observation["last_ahead"] = sample
                        observation["ahead_samples"] += 1
                    if time.perf_counter() >= deadline:
                        raise RuntimeError("The replay did not pause at the shot start during effect settling.")
                    # The running render tick can briefly lead the stock pause
                    # endpoint. Wait inside the same budget, then require two
                    # fresh exact paused views; a future tick is never accepted.
                    if current["paused"] and current["tick"] == target:
                        if sample["frame"] > last_frame:
                            stable_frames += 1
                            if first_paused_frame is None:
                                first_paused_frame = sample["frame"]
                    else:
                        stable_frames = 0
                        first_paused_frame = None
                    last_frame = max(last_frame, sample["frame"])
                    if stable_frames >= 2:
                        paused = current
                        break
                    self._stop_event.wait(.01)
                check_context()
                checked = self._require_demo(timeout=max(.001, deadline - time.perf_counter()))
                observation["console_tick"] = checked["tick"]
                check_context()
                final = bridge.status()
                check_context()
                self._require_native_demo(final)
                observation["final"] = {"tick": final["tick"], "paused": final["paused"],
                                        "frame": int(final["frame_count"])}
                if checked["tick"] != target or paused["tick"] != target:
                    raise RuntimeError("The replay missed the shot start during effect settling.")
                if (not final["paused"] or final["tick"] != target or
                        time.perf_counter() >= deadline):
                    raise RuntimeError("The replay moved after preparing the settled shot start.")
                if self._recorder_active():
                    raise RuntimeError("Replay effect settling must finish before recording starts.")
                check_context()
                goal_owned = False  # The one-shot goal reached its paused endpoint.
                details.update(performed=True, verified=True, lead_tick=beginning["tick"],
                               simulated_seconds=(target - beginning["tick"]) / project.tick_rate,
                               paused_tick=target, server_pause_tick=server_target,
                               pause_confirmation={"fresh_frames": stable_frames,
                                   "first_frame": first_paused_frame, "last_frame": paused["frame_count"],
                                   "tick": target},
                               no_final_seek=True, calibration=deepcopy(self._camera_calibration))
                self._last_seek_details = dict(target_seek, replay_warmup=deepcopy(details))
                return result
            finally:
                LOG.info("Replay effect settling target=%s server_goal=%s verified=%s "
                         "pause_confirmation=%s observation=%s", target, server_target,
                         details.get("verified", False), details.get("pause_confirmation"), observation)
                if goal_owned and self._session is session and self._demo == demo and self._alive():
                    # Cancellation/timeout must not leave a future goal armed. Never
                    # send cleanup commands into a different replay or session.
                    self._require_demo(require_tick=False)
                    if self._session is session and self._demo == demo and self._alive():
                        self._request("demo_resume; demo_pause")
        finally:
            self._replay_warmup_active = False

    @staticmethod
    def _check_warmed_native_view(native, frame, tick, *, attached=False):
        if not native.get("paused") or native.get("tick") != tick:
            raise RuntimeError("The replay moved after preparing the settled shot start.")
        pose = native.get("applied_pose")
        if not isinstance(pose, (tuple, list)) or len(pose) != 7:
            raise RuntimeError("The settled shot camera has no valid native readback.")
        # Attached paths resolve their world pose in the native target callback.
        # Their normal typed attachment gates remain responsible for that pose.
        if not attached:
            fields = ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
            for i, name in enumerate(fields):
                delta = abs(pose[i] - frame[name])
                if name in ("pitch", "yaw", "roll"):
                    delta = abs((pose[i] - frame[name] + 180) % 360 - 180)
                if not math.isfinite(delta) or delta > (.001 if name == "aspect_ratio" else 1.0):
                    raise RuntimeError("The native camera did not match the settled shot start.")
