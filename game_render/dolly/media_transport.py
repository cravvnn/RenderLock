"""Commands for the optional native recorder and ReShade runtime."""
from __future__ import annotations

import ctypes
import mmap
import os
import struct
from copy import deepcopy

from . import media_wire as wire
from .native_errors import NativeBridgeError


class MediaTransport:
    def _init_media(self):
        self._media_mapping = None
        self._media_sequence = 0
        self._media_last_command = ""
        self._source_audio_recording = False
        self._media_last = {"available": False, "state": "idle", "ack": 0,
                            "reshade": {"state": 0, "open": False}}

    def _media_atomic(self, offset, value=None):
        if self._atomic32 is None:
            if value is None:
                return struct.unpack_from("<I", self._media_mapping, offset)[0]
            struct.pack_into("<I", self._media_mapping, offset, value)
        else:
            slot = ctypes.c_int32.from_buffer(self._media_mapping, offset)
            if value is None:
                return self._read32(ctypes.byref(slot), 0, 0) & 0xffffffff
            self._atomic32(ctypes.byref(slot), ctypes.c_int32(value))

    def _ensure_media(self):
        self._check_open()
        if self._media_mapping is not None:
            return
        name = "Local\\DeadlockDollyNative_" + self.token + ".media"
        if self._mapping_factory is None:
            if os.name != "nt":
                raise NativeBridgeError("Video and ReShade require Windows")
            mapping = mmap.mmap(-1, wire.MAPPING_BYTES, tagname=name, access=mmap.ACCESS_WRITE)
        else:
            mapping = self._mapping_factory(name, wire.MAPPING_BYTES)
        try:
            if len(mapping) != wire.MAPPING_BYTES:
                raise NativeBridgeError("Unexpected media mapping size")
            mapping[:] = b"\0" * wire.MAPPING_BYTES
        except BaseException:
            close = getattr(mapping, "close", None)
            if close:
                close()
            raise
        self._media_mapping = mapping

    def media_status(self):
        with self._lock:
            if self._media_mapping is None or self._closed:
                return deepcopy(self._media_last)
            for _ in range(3):
                before = self._media_atomic(wire.STATUS_OFFSET + 8)
                if before & 1:
                    continue
                data = bytes(self._media_mapping[wire.STATUS_OFFSET:wire.STATUS_OFFSET + wire.STATUS.size])
                if before == self._media_atomic(wire.STATUS_OFFSET + 8):
                    try:
                        self._media_last = wire.unpack_status(data)
                        return deepcopy(self._media_last)
                    except ValueError as exc:
                        raise NativeBridgeError(str(exc)) from exc
            # A transient concurrent publication must not interrupt a recording.
            return deepcopy(self._media_last)

    def video_status(self):
        return self.media_status()

    def _media_command(self, command, *, wait=True, timeout=5, required=True, **options):
        with self._operations:
            with self._lock:
                self._ensure_media()
                seq = self._media_sequence + 2
                if seq > 0xfffffffe:
                    seq = 2
                packet = wire.pack_command(seq, command, **options)
                self._media_atomic(8, seq - 1)
                self._media_mapping[:8] = packet[:8]
                self._media_mapping[12:len(packet)] = packet[12:]
                self._media_atomic(8, seq)
                self._media_sequence = seq
                self._media_last_command = command
            if not wait:
                return None
            deadline = self._clock() + timeout
            while self._clock() < deadline:
                self._check_open()
                status = self.media_status()
                if status.get("ack") == seq:
                    if status.get("command_error"):
                        raise NativeBridgeError(status.get("command_message") or "Native media command was rejected")
                    return status
                # A dead game never acknowledges; end the wait instead of
                # holding _operations for the full timeout.
                self._check_game()
                self._sleep(.02)
            if not required:
                return None
            raise NativeBridgeError("Native media did not acknowledge the request. Reconnect with the complete matching Dolly build.")

    def start_video(self, path, *, fps=60, bitrate=20000000, encoder=0, codec=0,
                    quality=0, preset=0, ffmpeg_path="", fixed_step=False, depth=False,
                    depth_exr=False, shot_only=False, white_clear=False, max_frames=0, camera_track=False,
                    camera_only=False):
        return self._media_command("start_video", path=str(path), fps=fps, bitrate=bitrate,
                                   encoder=encoder, codec=codec, quality=quality,
                                   preset=preset, ffmpeg_path=str(ffmpeg_path) if ffmpeg_path else "",
                                   fixed_step=bool(fixed_step), depth=bool(depth),
                                   depth_exr=bool(depth_exr), shot_only=bool(shot_only),
                                   white_clear=bool(white_clear), max_frames=max_frames, camera_track=camera_track,
                                   camera_only=camera_only)

    def stop_video(self, cancel=False):
        if type(cancel) is not bool:
            raise ValueError("Video cancel must be a boolean")
        return self._media_command("cancel_video" if cancel else "stop_video")

    def configure_reshade(self, dll_path, config_path):
        return self._media_command("configure_reshade", path=str(dll_path), config_path=str(config_path))

    def disable_reshade(self):
        return self._media_command("disable_reshade")

    def toggle_reshade(self):
        return self._media_command("toggle_reshade")

    def start_source_audio(self, csv_path):
        status = self._media_command("start_source_audio", path=str(csv_path))
        self._source_audio_recording = True
        return status

    def stop_source_audio(self):
        status = self._media_command("stop_source_audio")
        self._source_audio_recording = False
        return status

    def silence_ui(self, timeout=0.25):
        """Arm Dolly's interface-voice window and confirm it before the pause.

        This runs immediately before a replay pause/resume, so it must resolve
        quickly: the window has to be open before the game processes the pause,
        and a slow media worker must never delay the pause itself. The window is
        self-expiring, and a build without the sound hooks simply keeps the game's
        own cue. Returns True when the native side acknowledged the arm.
        """
        try:
            return self._media_command("silence_ui", timeout=timeout, required=False) is not None
        except Exception:
            return False

    def _close_media(self):
        """Request finalization while the game worker still has our heartbeat."""
        with self._operations:
            self._finish_media_and_unmap()

    def _media_game_exited(self):
        exited = getattr(self, "_game_exited", None)
        return exited is not None and bool(exited())

    def _finish_media_and_unmap(self):
        import logging
        if self._media_mapping is None:
            return
        try:
            if self._media_game_exited():
                # Nothing can acknowledge a finalize request any more; the
                # mapping is still released below.
                logging.getLogger(__name__).warning("Deadlock exited; skipped native media finalization.")
                return
            state = self.media_status()
            unconfirmed_audio = (self._media_last_command == "start_source_audio"
                                 and state.get("ack") != self._media_sequence)
            unconfirmed_start = (self._media_last_command == "start_video"
                                 and state.get("ack") != self._media_sequence)
            if self._source_audio_recording or unconfirmed_audio:
                try:
                    self.stop_source_audio()
                except (RuntimeError, OSError, ValueError):
                    logging.getLogger(__name__).exception("Could not finish source audio before disconnect")
            if state.get("state") in ("starting", "recording", "finalizing") or unconfirmed_start:
                try:
                    self.stop_video()
                    deadline = self._clock() + 10
                    while self._clock() < deadline:
                        if self.media_status().get("state") not in ("starting", "recording", "finalizing"):
                            break
                        if self._media_game_exited():
                            break
                        self._sleep(.05)
                    else:
                        logging.getLogger(__name__).warning("Video is still finalizing; keep Deadlock open until the output is complete.")
                except (RuntimeError, OSError, ValueError):
                    logging.getLogger(__name__).exception("Could not finish video before disconnect")
            if state.get("reshade", {}).get("state"):
                self.disable_reshade()
        except (RuntimeError, OSError, ValueError):
            logging.getLogger(__name__).exception("Could not finish native media before disconnect; game heartbeat recovery will request finalization")
        finally:
            with self._lock:
                mapping, self._media_mapping = self._media_mapping, None
                close = getattr(mapping, "close", None)
                if close:
                    close()
