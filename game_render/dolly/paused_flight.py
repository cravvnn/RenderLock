"""Paused-camera flight and manual movement shared by the controller.

Methods moved verbatim out of the controller so manual paused-camera movement
owns its navigation dependency. The controller re-exports the readback interval
constant for existing callers and tests.
"""
from __future__ import annotations

import logging
from copy import deepcopy
import threading
import time

from .navigation import CameraMotion, move_camera
from .pacing import FrameWait

LOG = logging.getLogger("dolly")
CAMERA_READBACK_INTERVAL = .25


class PausedFlightMixin:
    @staticmethod
    def _flight_options(move_speed, turn_speed, rate):
        # Reuse the movement model's bounds before launching a worker or
        # issuing any camera command. The frame is inert at zero elapsed time.
        move_camera({"x": 0, "y": 0, "z": 0, "pitch": 0, "yaw": 0},
                    CameraMotion(), 0, move_speed=move_speed, turn_speed=turn_speed)
        if isinstance(rate, bool) or rate not in (30, 60, 120):
            raise ValueError("Choose a command rate of 30, 60, or 120.")
        return float(move_speed), float(turn_speed), float(rate)

    def start_paused_flight(self, input_source=None, move_speed=240, turn_speed=60, rate=60):
        """Run one bounded console writer using wall time, never replay time.

        input_source is called on the worker and must be thread-safe. It must
        return CameraMotion and must not call GUI functions.
        """
        if self._supports_native_flight():
            self._flight_options(move_speed, turn_speed, rate)
            return self.enter_native_flight()
        if not callable(input_source):
            raise ValueError("Paused camera input must be callable.")
        move_speed, turn_speed, rate = self._flight_options(move_speed, turn_speed, rate)
        with self._op_lock:
            self._halt()
            self._check_paused_tick()
            self._stop_event.clear()
            self._reset_motion_observations(self._paused_pose)
            with self._state_lock:
                self._paused_details.update(move_speed=move_speed, turn_speed=turn_speed,
                                            requested_updates_per_second=rate, updates=0)
                self._paused_details.pop("error", None)
            self._message("Paused camera movement active. Escape stops movement; the replay stays paused.",
                          playing=False, paused_flight=True)
            self._thread = threading.Thread(target=self._run_paused_flight,
                args=(input_source, move_speed, turn_speed, rate), daemon=True,
                name="DollyPausedCamera")
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                with self._state_lock:
                    self._state["paused_flight"] = False
                raise

    def _write_paused_pose(self, frame):
        self._check_paused_tick()
        if self._stop_event.is_set():
            return False
        sent_at = time.perf_counter()
        sampled = sent_at >= self._next_camera_readback
        command = self._position_commands(frame) + ("; spec_pos" if sampled else "") + "; demo_goto"
        output = self._request(command)
        received_at = time.perf_counter()
        self._check_paused_tick(output)
        if sampled:
            self._next_camera_readback = received_at + CAMERA_READBACK_INTERVAL
        self._observe_motion_frame(frame, output, sent_at, received_at, self._paused_tick,
                                   sampled=sampled, manual=True)
        self._set_paused_pose(frame, self._paused_tick)
        return True

    def _run_paused_flight(self, input_source, move_speed, turn_speed, rate):
        began = previous = time.perf_counter()
        next_frame = began
        failure = None
        escape = False
        updates = 0
        waiter = FrameWait()
        try:
            while not self._stop_event.is_set():
                now = time.perf_counter()
                motion = input_source()
                if not isinstance(motion, CameraMotion):
                    raise ValueError("Paused camera input must return CameraMotion.")
                if motion.stop:
                    escape = True
                    break
                # Delta includes command latency. move_camera caps long stalls
                # so a delayed response cannot create a large camera jump.
                frame = move_camera(self._paused_pose, motion, max(0.0, now - previous),
                                    move_speed=move_speed, turn_speed=turn_speed)
                previous = now
                if frame != self._paused_pose:
                    if not self._write_paused_pose(frame):
                        break
                    updates += 1
                else:
                    # Check external resume/seek even when no input is held.
                    self._check_paused_tick()
                elapsed = max(0.0, time.perf_counter() - began)
                with self._state_lock:
                    self._paused_details.update(updates=updates, elapsed_seconds=elapsed,
                        achieved_updates_per_second=updates / elapsed if elapsed else 0)
                next_frame += 1 / rate
                now = time.perf_counter()
                if next_frame < now:
                    next_frame = now
                waiter.wait(max(0, next_frame - now), self._stop_event)
        except Exception as exc:
            LOG.exception("Paused camera movement stopped")
            failure = str(exc)
            self._paused_details["error"] = failure
            self._invalidate_paused_camera()
        finally:
            with self._state_lock:
                self._paused_details.update(frame_wait_backend=waiter.backend,
                                             frame_wait_error=waiter.error)
            waiter.close()
            with self._state_lock:
                self._state["paused_flight"] = False
            if failure:
                self._message(failure)
            elif escape:
                self._message("Paused camera movement stopped. Replay and current view remain paused.")

    def stop_paused_flight(self):
        """End manual movement without restoring the current lens or seeking."""
        with self._op_lock:
            if self.status().get("paused_flight"):
                self._halt()
            self._message("Paused camera movement stopped. Current camera settings are held.", paused_flight=False)

    def nudge_paused_camera(self, motion, seconds=.1, move_speed=240, turn_speed=60):
        if self._supports_native_flight():
            with self._op_lock:
                self._halt(native_action="native_hold")
                self._check_paused_tick()
                native = self._native_bridge()
                frame = self._native_pose(native.status())
                frame = move_camera(frame, motion, seconds, move_speed=move_speed, turn_speed=turn_speed)
                self.enter_native_flight(pose=frame)
                self._halt(native_action="native_hold")
                return deepcopy(self._paused_pose)
        with self._op_lock:
            self._halt()
            self._check_paused_tick()
            frame = move_camera(self._paused_pose, motion, seconds,
                                move_speed=move_speed, turn_speed=turn_speed)
            self._stop_event.clear()
            if frame != self._paused_pose:
                self._write_paused_pose(frame)
            return deepcopy(self._paused_pose)
