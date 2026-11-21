"""Fixed-step export timing and render-scale control shared by the controller.

Methods moved verbatim out of the controller so the fixed-step export timing and
render-scale snapshot own their own module while remaining on the Controller
instance. No behavior change.
"""
from __future__ import annotations

import math

from .camera_commands import numeric
from .convar_response import read_cvar_value


class ExportTimingMixin:
    def set_export_resolution(self):
        """Use a verified full-target depth pass, independent of render scaling.

        Reduced viewport rendering is later overwritten by an unverified upscale
        pass. Keep this snapshot separate from playback: Play calls Stop before
        dispatching the take, and must not undo the recording's resolution.
        """
        if self._console is None or not self._console.is_connected or not self._alive():
            raise RuntimeError("Connect to the game before depth export.")
        if getattr(self, "_export_resolution", None) is None:
            original = float(read_cvar_value("mat_viewportscale", self._request("mat_viewportscale")))
            if not math.isfinite(original) or original <= 0:
                raise RuntimeError("Could not read the game's render scale before depth export.")
            self._export_resolution = original
        self._request("mat_viewportscale 1")
        if read_cvar_value("mat_viewportscale", self._request("mat_viewportscale")) != 1:
            raise RuntimeError("Depth export needs 100% render scale, but the game did not accept it.")
        self._message("Depth export and its layers use 100% render scale; the previous scale is restored after each take.")

    def clear_export_resolution(self):
        original = getattr(self, "_export_resolution", None)
        if original is None:
            return
        if self._alive():
            if self._console is None or not self._console.is_connected:
                raise RuntimeError("Reconnect and finish the recording to restore the game render scale.")
            self._request("mat_viewportscale " + numeric(original))
            actual = read_cvar_value("mat_viewportscale", self._request("mat_viewportscale"))
            if not math.isclose(actual, original, rel_tol=1e-6):
                raise RuntimeError("Could not restore the game's render scale. Reconnect and finish the recording to retry.")
        self._export_resolution = None

    def set_export_timing(self, fps, speed=1.0):
        """Enter deterministic fixed-step engine timing for an export.

        ``host_framerate`` makes each rendered frame advance a fixed slice of
        engine time (independent of wall-clock), ``r_wait_on_present`` keeps the
        renderer from coalescing frames. The engine ignores ``demo_timescale``
        for this fixed step, so use fps / speed to advance speed / fps replay
        seconds per output frame. :meth:`play` preserves the selected speed.
        Previous values are restored by :meth:`clear_export_timing`.
        """
        fps = int(fps)
        if fps not in (30, 60, 120, 300, 600):
            raise ValueError("Fixed-step export requires 30, 60, 120, 300 or 600 FPS.")
        try:
            speed = float(speed)
        except (TypeError, ValueError) as exc:
            raise ValueError("Export speed must be a number between 0.05 and 4.") from exc
        if not math.isfinite(speed) or not .05 <= speed <= 4:
            raise ValueError("Export speed must be between 0.05 and 4.")
        previous = getattr(self, "_export_timing", None)
        if previous is not None:
            if previous.get("restore_pending"):
                raise RuntimeError("Restore the previous export timing before starting another export.")
            return
        if self._console is None or not self._console.is_connected or not self._alive():
            raise RuntimeError("Connect to the game before fixed-step export.")
        snapshot = {name: float(read_cvar_value(name, self._request(name)))
                    for name in ("host_framerate", "r_wait_on_present")}
        engine_fps = fps / speed
        self._export_timing = {"fps": fps, "speed": speed, "restore": snapshot}
        try:
            self._request(
                f"host_framerate {numeric(engine_fps)}; r_wait_on_present 1; demo_timescale {numeric(speed)}")
            actual = read_cvar_value("host_framerate", self._request("host_framerate"))
            synchronized = read_cvar_value("r_wait_on_present", self._request("r_wait_on_present"))
            if not math.isclose(actual, engine_fps, rel_tol=1e-6) or synchronized != 1:
                raise RuntimeError("The game did not accept fixed-step export timing. Choose a lower video FPS or a higher export speed.")
        except Exception:
            self.clear_export_timing()
            raise
        self._message(
            f"Fixed-step export at {fps} FPS, {numeric(speed)}x; each rendered frame is captured.")

    def clear_export_timing(self):
        state = getattr(self, "_export_timing", None)
        if state is None:
            return
        if not self._alive():
            self._export_timing = None
            return
        state["restore_pending"] = True
        if self._console is None or not self._console.is_connected:
            raise RuntimeError("Reconnect and finish the recording to restore export timing.")
        restore = state.get("restore") or {}
        host = restore.get("host_framerate")
        wait = restore.get("r_wait_on_present")
        commands = ["host_framerate " + (numeric(host) if host else "0"),
                    "r_wait_on_present " + (numeric(wait) if wait is not None else "0"),
                    "demo_timescale 1"]
        self._request("; ".join(commands))
        for name, expected in restore.items():
            actual = read_cvar_value(name, self._request(name))
            if not math.isclose(actual, expected, rel_tol=1e-6):
                raise RuntimeError("Could not restore export timing. Finish the recording again to retry.")
        self._export_timing = None

    def suspend_export_timing(self):
        """Restore normal engine pacing for a paused-camera calibration.

        Fixed-step export timing (``host_framerate`` + ``r_wait_on_present``)
        can make a paused camera apply only part of a position command, which
        the position check must reject. Keep the configured export timing and
        re-apply it with :meth:`resume_export_timing` before the shot
        dispatches.
        """
        state = getattr(self, "_export_timing", None)
        if state is None or state.get("suspended"):
            return
        if state.get("restore_pending"):
            raise RuntimeError("Restore the previous export timing before continuing playback.")
        if self._console is None or not self._console.is_connected or not self._alive():
            return
        restore = state.get("restore") or {}
        host = restore.get("host_framerate")
        wait = restore.get("r_wait_on_present")
        commands = ["host_framerate " + (numeric(host) if host else "0"),
                    "r_wait_on_present " + (numeric(wait) if wait is not None else "0")]
        if self._demo_speed_changed:
            commands.append("demo_timescale 1")
        self._request("; ".join(commands))
        for name, expected in restore.items():
            actual = read_cvar_value(name, self._request(name))
            if not math.isclose(actual, expected, rel_tol=1e-6):
                raise RuntimeError("The game did not suspend export timing. Finish the recording to restore it.")
        state["suspended"] = True

    def resume_export_timing(self):
        state = getattr(self, "_export_timing", None)
        if state is None or not state.get("suspended"):
            return
        if state.get("restore_pending"):
            raise RuntimeError("Restore the previous export timing before continuing playback.")
        if self._console is None or not self._console.is_connected or not self._alive():
            return
        engine_fps = state["fps"] / state["speed"]
        self._request(f"host_framerate {numeric(engine_fps)}; r_wait_on_present 1; "
                      f"demo_timescale {numeric(state['speed'])}")
        actual = read_cvar_value("host_framerate", self._request("host_framerate"))
        synchronized = read_cvar_value("r_wait_on_present", self._request("r_wait_on_present"))
        if not math.isclose(actual, engine_fps, rel_tol=1e-6) or synchronized != 1:
            raise RuntimeError("The game did not resume export timing. Finish the recording to restore it.")
        state["suspended"] = False
