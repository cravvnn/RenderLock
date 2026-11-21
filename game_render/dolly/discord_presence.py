"""Optional, coarse activity sharing; project and replay names stay private."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time

from . import __version__
from .discord_ipc import DiscordRpc

APPLICATION_ID = "1564091346917138512"  # Public application identity; never a token.
UPDATE_INTERVAL = 15.0
RECONNECT_INTERVAL = 30.0
LOG = logging.getLogger("dolly")


@dataclass(frozen=True)
class Activity:
    details: str
    state: str = "Local replay filmmaking"


def activity_for(status, video, *, cameras=0, processing_players=False):
    """Build a whitelist of phrases, never forwarding logs, paths or names."""
    if processing_players:
        return Activity("Processing the Players layer")
    state = video.get("state")
    if state == "finalizing":
        return Activity("Saving a video export")
    if state in ("starting", "recording"):
        return Activity("Exporting video")
    if status.get("playing"):
        return Activity("Previewing a campath")
    stage = status.get("startup_stage", "not_launched")
    if status.get("game_running") and stage not in ("editing_ready", "replay_ready", "failed", "cancelled", "game_closed"):
        return Activity("Preparing a replay")
    if status.get("game_ui_visible"):
        return Activity("Browsing a replay")
    if type(cameras) is int and cameras > 0:
        count = min(cameras, 9999)
        return Activity("Editing a campath", f"{count} camera{'s' if count != 1 else ''} in the shot")
    return Activity("Ready to create")


class RichPresence:
    def __init__(self, *, application_id=APPLICATION_ID, rpc_factory=DiscordRpc, clock=time.monotonic):
        self._application_id = application_id
        self._factory = rpc_factory
        self._clock = clock
        self._started = int(time.time())
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancelled = threading.Event()
        self._thread = None
        self._enabled = False
        self._activity = Activity("Ready to create")
        self._status = "Off"
        self._rpc = None
        self._last_activity = None
        self._next_update = self._next_connect = 0.0

    @property
    def status(self):
        with self._lock:
            return self._status

    def _set_status(self, value):
        with self._lock:
            self._status = value

    def update(self, activity, *, enabled):
        if not isinstance(activity, Activity) or type(enabled) is not bool:
            raise ValueError("Invalid Discord presence state")
        with self._lock:
            if self._stop.is_set():
                return
            changed = enabled != self._enabled or activity != self._activity
            if enabled != self._enabled:
                self._next_connect = 0.0
            self._enabled, self._activity = enabled, activity
            if enabled:
                self._cancelled.clear()
            else:
                self._cancelled.set()
        if changed:
            self._wake.set()

    def start(self):
        if self._thread is None and not self._stop.is_set():
            self._thread = threading.Thread(target=self._run, name="dolly-discord-presence", daemon=True)
            self._thread.start()

    def _disconnect(self):
        rpc, self._rpc = self._rpc, None
        self._last_activity = None
        if rpc is not None:
            rpc.close()

    def _step(self):
        with self._lock:
            enabled, activity = self._enabled, self._activity
        if not enabled:
            self._disconnect()
            self._set_status("Off")
            return
        now = self._clock()
        if self._rpc is None:
            if now < self._next_connect:
                return
            self._next_connect = now + RECONNECT_INTERVAL
            self._rpc = self._factory(self._application_id, self._cancelled)
            self._next_update = 0.0
        self._rpc.poll()
        if activity != self._last_activity and now >= self._next_update:
            payload = {"type": 0, "details": activity.details, "state": activity.state,
                       "timestamps": {"start": self._started},
                       "assets": {"large_image": "https://raw.githubusercontent.com/cravvnn/deadlock-dolly/main/assets/dolly.png",
                                  "large_text": f"Deadlock Dolly {__version__}"},
                       "buttons": [{"label": "Get Dolly", "url": "https://github.com/cravvnn/deadlock-dolly/releases"},
                                   {"label": "Editing community", "url": "https://discord.gg/deadlockediting"}]}
            self._rpc.set_activity(payload, self._cancelled)
            self._last_activity = activity
            self._next_update = self._clock() + UPDATE_INTERVAL
            self._set_status("Sharing activity")

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    self._step()
                except Exception:
                    self._disconnect()
                    self._next_connect = self._clock() + RECONNECT_INTERVAL
                    self._set_status("Waiting for Discord")
                    LOG.debug("Discord presence unavailable; editing is unaffected", exc_info=True)
                self._wake.wait(0.25)
                self._wake.clear()
        finally:
            self._disconnect()

    def close(self):
        self._stop.set()
        self._cancelled.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.75)
