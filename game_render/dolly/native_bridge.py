"""Bounded Windows shared-memory transport for the native render camera.

Only the editor writes control bytes; only its launched game writes status.
Sequence counters publish coherent snapshots. The heartbeat is independent of
command snapshots, so a long wait cannot look like an abandoned editor.
"""
from __future__ import annotations

import ctypes
from collections import deque
from copy import deepcopy
import hashlib
import json
import math
import mmap
import os
import re
import struct
import threading
import time
import uuid

from .native_effects import compile_shot, EFFECTS
from .native_errors import NativeBridgeError
from .path import (CONFETTI_SPAWN_HEIGHT_DEFAULT, CONFETTI_SPAWN_HEIGHT_MAX,
                   CONFETTI_SPAWN_HEIGHT_MIN)
from .particles import (PARTICLE_DEFAULT, PARTICLE_IDS, PARTICLE_INTENSITY_DEFAULT,
                        PARTICLE_INTENSITY_MAX, PARTICLE_INTENSITY_MIN)
from .runtime import resource_root
from .media_transport import MediaTransport

ABI = 4
CONFETTI_FLAG = 8
CONFETTI_DESPAWN_FLAG = 32
CONFETTI_HEIGHT_SHIFT = 16
PARTICLE_PRESET_SHIFT = 6
PARTICLE_PRESET_MASK = 0xF
PARTICLE_INTENSITY_SHIFT = 10
PARTICLE_INTENSITY_MASK = 0x3F
PARTICLE_INTENSITY_SCALE = 20


def _intensity_bits(value: float) -> int:
    """Quantise intensity for the six flag bits; zero means 'not sent'."""
    raw = int(round(float(value) * PARTICLE_INTENSITY_SCALE))
    return max(2, min(PARTICLE_INTENSITY_MASK, raw))
CONTROL_BYTES = 2 * 1024 * 1024
# The legacy tail is 24576 bytes (camera list + framing grid). The Object Picker
# block lives in one appended 4 KiB page after it; keep this in step with
# dolly_protocol.hpp kMappingBytes. Replay events append a separate 32 KiB block
# after dolly/object_wire.OBJECT_MAPPING_BYTES; no existing offsets move.
MAPPING_BYTES = CONTROL_BYTES + 24576 + 4096 + 32768
PAYLOAD_OFFSET = 1024
MAX_PAYLOAD_BYTES = CONTROL_BYTES - PAYLOAD_OFFSET
CONTROL = struct.Struct("<8s6IQ2I2d512s")
STATUS = struct.Struct("<8s6IQ3diI14d2dQ256s512s2d2IQd")
CONTROL_MAGIC = b"DLYCAM01"
STATUS_MAGIC = b"DLYSTAT1"
STATES = ("starting", "probe", "armed", "playing", "completed", "stopped", "fault", "unsupported")
CONFETTI_DIAGNOSTICS_OFFSET = CONTROL_BYTES + 22440
CONFETTI_DIAGNOSTICS = struct.Struct("<8s2I15Id")
CONFETTI_DIAGNOSTICS_MAGIC = b"DLYCFT01"
CONFETTI_DIAGNOSTICS_ABI = 1
FOLLOW_ANCHOR_DIAGNOSTICS_OFFSET = CONTROL_BYTES + 23088
FOLLOW_ANCHOR_DIAGNOSTICS = struct.Struct("<8s4I4Q")
CONFETTI_STATES = ("unavailable", "ready", "configured", "running", "waiting", "seeking",
                   "manager_unavailable", "create_failed")
CONFETTI_DIAGNOSTIC_FIELDS = ("state", "handles", "starts", "start_failures", "frames",
                              "running_frames", "resets", "stop_disabled", "stop_reconfigured",
                              "stop_seek", "stop_backward", "stop_invalid", "stop_shutdown",
                              "stop_create_failed", "state_changes", "max_backward_delta")


class NativeStatusBusy(NativeBridgeError):
    """Every bounded status reread saw an odd or torn sequence; poll again."""


def _winerror_from(exc):
    """Find a Windows error code anywhere in a wrapped exception chain."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        code = getattr(exc, "winerror", None)
        if isinstance(code, int):
            return code
        exc = exc.__cause__ or exc.__context__
    return None


def _load_atomic_library():
    """Load the verified Dolly helper, without invoking its game factory.

    Windows x64 implements the Interlocked APIs as compiler intrinsics; they
    cannot be assumed to exist as kernel32 exports for ctypes. Dolly exports
    small wrappers compiled with those intrinsics and their memory barriers.
    Loading this DLL initializes no game hooks: only CreateInterface can start
    the separate game-side loader, and the editor never calls that export.
    """
    native = resource_root() / "native"
    dll = native / "bin" / "win64" / "DollyNative.dll"
    try:
        metadata = json.loads((native / "build_info.json").read_text(encoding="utf-8"))
        data = dll.read_bytes()
    except (OSError, ValueError) as exc:
        raise NativeBridgeError(
            f"The native camera helper is missing or invalid at {dll}: {exc}. "
            "Extract the complete Dolly package or rebuild it.") from exc
    if (not isinstance(metadata, dict) or type(metadata.get("abi")) is not int
            or metadata["abi"] != ABI
            or metadata.get("sha256") != hashlib.sha256(data).hexdigest()):
        raise NativeBridgeError("The native camera helper failed its ABI or SHA-256 check. Extract the complete matching Dolly package.")
    offset = struct.unpack_from("<I", data, 60)[0] if len(data) >= 64 else len(data)
    if (data[:2] != b"MZ" or offset < 64 or offset + 26 > len(data)
            or data[offset:offset + 4] != b"PE\0\0"
            or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664
            or not struct.unpack_from("<H", data, offset + 22)[0] & 0x2000
            or struct.unpack_from("<H", data, offset + 24)[0] != 0x20B):
        raise NativeBridgeError("The native camera helper must be a Windows x64 DLL.")
    try:
        # An absolute path and restricted dependency search avoid selecting
        # an unrelated DLL from the editor's working directory or PATH.
        # RenderLock: load a verified copy named by its hash, so a running editor
        # (Blender) never locks the build output against a native rebuild.
        loaded = resource_root() / "build" / "loaded" / f"DollyNative-{metadata['sha256'][:16]}.dll"
        if not loaded.is_file() or hashlib.sha256(loaded.read_bytes()).hexdigest() != metadata["sha256"]:
            loaded.parent.mkdir(parents=True, exist_ok=True)
            partial = loaded.with_suffix(".partial")
            partial.write_bytes(data)
            partial.replace(loaded)
        library = ctypes.WinDLL(str(loaded.resolve()), use_last_error=True, winmode=0x1100)
        protocol = library.DollyNativeProtocolVersion
        protocol.argtypes = []
        protocol.restype = ctypes.c_uint32
        if protocol() != ABI:
            raise NativeBridgeError("The loaded native camera helper has a different protocol. Restart Dolly with the complete updated package.")
        return library
    except (OSError, AttributeError, ImportError) as exc:
        code = _winerror_from(exc)
        if code == 4551:
            raise NativeBridgeError(
                "Windows Application Control blocked DollyNative.dll (WinError 4551). "
                "Right-click the Dolly ZIP, choose Properties > Unblock before extracting, "
                "and extract the complete package again. If it persists, allow Dolly in "
                "Windows Security or Smart App Control, or use a machine where Application "
                "Control is not enforced. This is a Windows policy, not a missing Dolly file.") from exc
        if code == 126:
            raise NativeBridgeError(
                f"The native camera helper or one of its dependencies is missing from {dll} "
                "(WinError 126). Extract the complete Dolly package.") from exc
        if code == 193:
            raise NativeBridgeError(
                f"The native camera helper at {dll} is not a valid Windows x64 DLL "
                "(WinError 193). Extract the complete package.") from exc
        raise NativeBridgeError(
            f"The native camera helper could not load from {dll}: {exc}. "
            "Rebuild Dolly or extract the complete updated package.") from exc


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'} and finite")
    return result


def _pid(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 0xFFFFFFFF:
        raise ValueError("Native camera requires a valid process ID")
    return value


class NativeBridge(MediaTransport):
    @classmethod
    def create(cls, *, mapping_factory=None, clock=time.monotonic,
               sleep=time.sleep, start_heartbeat=True, editor_pid=None, token=None):
        """Create a private session mapping before starting its development game.

        The injectable factory/clock are for portable transport tests; the
        production mapping is always a Windows pagefile-backed named mapping.
        """
        editor_pid = _pid(os.getpid() if editor_pid is None else editor_pid)
        token = uuid.uuid4().hex if token is None else token
        if not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{32}", token) is None:
            raise ValueError("Native camera session token must be 32 lowercase hex digits")
        name = "Local\\DeadlockDollyNative_" + token
        if mapping_factory is None:
            if os.name != "nt":
                raise NativeBridgeError("Native camera sessions require 64-bit Windows")
            mapping = mmap.mmap(-1, MAPPING_BYTES, tagname=name, access=mmap.ACCESS_WRITE)
        else:
            mapping = mapping_factory(name, MAPPING_BYTES)
        try:
            return cls(mapping, token, editor_pid, clock, sleep, start_heartbeat,
                       mapping_factory=mapping_factory)
        except Exception:
            close = getattr(mapping, "close", None)
            if close is not None:
                close()
            raise

    def __init__(self, mapping, token, editor_pid, clock, sleep, start_heartbeat,
                 *, mapping_factory=None):
        if len(mapping) != MAPPING_BYTES:
            raise NativeBridgeError("Native camera mapping has an unexpected size")
        self._mapping = mapping
        self._mapping_factory = mapping_factory
        self._init_media()
        self._viewer_mapping = None
        self._viewer_sequence = 0
        self._viewer_enabled = False
        self._viewer_error = ""
        self._preview_mapping = None
        self._preview_sequence = 0
        self._preview_enabled = False
        self._preview_error = ""
        self.token = token
        self.editor_pid = editor_pid
        self.game_pid = 0
        self._game_exited = None
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_lock = threading.Lock()
        self._thread = None
        self._closed = False
        self._command = 0
        self._sequence = 0
        self._heartbeat = 0
        self._flags = 0
        self._frozen = False
        self._relief = True
        self._confetti_enabled = False
        self._confetti_spawn_height = round(CONFETTI_SPAWN_HEIGHT_DEFAULT)
        self._confetti_despawn_on_ground = False
        self._particles_preset = PARTICLE_DEFAULT
        self._particles_intensity = PARTICLE_INTENSITY_DEFAULT
        self._start = 0.0
        self._speed = 1.0
        self._demo = b""
        self._prepared = False
        self._manual = False
        self._editor_values = {}
        self._editor_sequence = 0
        self._editor_owner_sequence = 0
        self._editor_ack = 0
        self._graphics_samples = deque(maxlen=120)
        self._graphics_sample_at = -math.inf
        self._graphics_error = ""
        self._input_samples = deque(maxlen=120)
        self._input_sample_at = -math.inf
        self._input_error = ""
        self._view_samples = deque(maxlen=120)
        self._view_sample_at = -math.inf
        self._editor_view_poll_at = -math.inf
        self._mode = 0
        self._atomic32 = self._atomic64 = None
        self._read32 = None
        self._atomic_library = None
        if os.name == "nt" and isinstance(mapping, mmap.mmap):
            self._atomic_library = _load_atomic_library()
            try:
                self._atomic32 = self._atomic_library.DollyAtomicExchange32
                self._atomic64 = self._atomic_library.DollyAtomicExchange64
                self._read32 = self._atomic_library.DollyAtomicCompareExchange32
            except AttributeError as exc:
                raise NativeBridgeError("This native camera helper is from an older build. Rebuild Dolly and replace the complete package, including _internal.") from exc
            self._atomic32.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_int32]
            self._atomic32.restype = ctypes.c_int32
            self._atomic64.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64]
            self._atomic64.restype = ctypes.c_int64
            self._read32.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int32]
            self._read32.restype = ctypes.c_int32
        mapping[:] = b"\0" * MAPPING_BYTES
        self._publish(0, b"", increment=False)
        self._beat()
        if start_heartbeat:
            self._thread = threading.Thread(target=self._heartbeat_loop,
                                            name="dolly-native-heartbeat", daemon=True)
            self._thread.start()

    def _store(self, offset, value, bits=32):
        atomic = self._atomic32 if bits == 32 else self._atomic64
        if atomic is None:
            struct.pack_into("<I" if bits == 32 else "<Q", self._mapping, offset, value)
        else:
            typ = ctypes.c_int32 if bits == 32 else ctypes.c_int64
            slot = typ.from_buffer(self._mapping, offset)
            atomic(ctypes.byref(slot), typ(value))

    def _check_open(self):
        if self._closed:
            raise NativeBridgeError("Native camera session is closed")

    def _load_sequence(self, offset=CONTROL_BYTES + 8):
        if self._read32 is None:
            return struct.unpack_from("<I", self._mapping, offset)[0]
        slot = ctypes.c_int32.from_buffer(self._mapping, offset)
        return self._read32(ctypes.byref(slot), 0, 0) & 0xFFFFFFFF

    def _beat(self):
        # The heartbeat slot is independent of every command snapshot, so it
        # must not queue behind the controller lock: a long lock hold once
        # starved the beat until the native worker ended an active take.
        with self._heartbeat_lock:
            if self._closed or self._heartbeat_stop.is_set():
                return
            self._heartbeat = (self._heartbeat + 1) & 0xFFFFFFFFFFFFFFFF
            self._store(32, self._heartbeat, 64)

    def _heartbeat_loop(self):
        last = time.monotonic()
        while not self._heartbeat_stop.wait(0.1):
            self._beat()
            now = time.monotonic()
            if now - last > 0.5:
                import logging
                logging.getLogger(__name__).warning(
                    "Native heartbeat paused for %.2f s; an active recording kept running.",
                    now - last)
            last = now

    def _publish(self, mode, payload=b"", *, increment=True):
        with self._lock:
            self._check_open()
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("Native camera path exceeds the shared-memory capacity")
            if increment and self._command == 0xFFFFFFFF:
                raise NativeBridgeError("Native camera command counter exhausted; relaunch the session")
            command = self._command + int(increment)
            odd = (self._sequence + 1) & 0xFFFFFFFF
            even = (odd + 1) & 0xFFFFFFFF
            header = CONTROL.pack(CONTROL_MAGIC, ABI, odd, command, mode,
                                  self.editor_pid, self.game_pid, 0, self._flags,
                                  len(payload), self._start, self._speed, self._demo)
            self._store(12, odd)
            # Neither the header copy nor the sequence publication touches the
            # aligned heartbeat at 32:40; it belongs to the heartbeat writer.
            self._mapping[:12] = header[:12]
            self._mapping[16:32] = header[16:32]
            self._mapping[40:CONTROL.size] = header[40:]
            if payload:
                self._mapping[PAYLOAD_OFFSET:PAYLOAD_OFFSET + len(payload)] = payload
            self._store(12, even)
            self._sequence = even
            self._command = command
            self._mode = mode
            return command

    def bind_game(self, pid, exited=None):
        """Attach to the game process. ``exited`` reports whether it has ended.

        Acknowledgment waits poll it, so a game that dies mid-command ends the
        wait on the next poll instead of after the full timeout (which would
        hold ``_operations`` and delay the exit watcher's close).
        """
        pid = _pid(pid)
        with self._operations, self._lock:
            self._check_open()
            if self.game_pid:
                if self.game_pid != pid:
                    raise NativeBridgeError("Native camera cannot attach to another game process")
                return
            self.game_pid = pid
            self._game_exited = exited
            self._publish(self._mode, increment=False)

    def _check_game(self):
        exited = self._game_exited
        if exited is not None and exited():
            raise NativeBridgeError("Deadlock exited before the native camera answered.")

    def _confetti_diagnostics(self):
        """Optional bounded Dolly-side confetti counters, or None when unwritten."""
        for _ in range(4):
            first = self._load_sequence(CONFETTI_DIAGNOSTICS_OFFSET + 8)
            if first & 1:
                continue
            data = bytes(self._mapping[CONFETTI_DIAGNOSTICS_OFFSET:
                                       CONFETTI_DIAGNOSTICS_OFFSET + CONFETTI_DIAGNOSTICS.size])
            if first != self._load_sequence(CONFETTI_DIAGNOSTICS_OFFSET + 8):
                continue
            if not any(data):
                return None  # Older helpers never publish this optional block.
            values = CONFETTI_DIAGNOSTICS.unpack(data)
            magic, sequence, abi = values[:3]
            if (magic != CONFETTI_DIAGNOSTICS_MAGIC or sequence != first
                    or abi != CONFETTI_DIAGNOSTICS_ABI):
                raise NativeBridgeError(
                    "Native confetti diagnostics do not match this editor build")
            if values[3] >= len(CONFETTI_STATES) or values[4] > 6:
                raise NativeBridgeError("Native confetti diagnostics are invalid")
            result = dict(zip(CONFETTI_DIAGNOSTIC_FIELDS, values[3:]))
            result["state_name"] = CONFETTI_STATES[result["state"]]
            return result
        return None

    def _follow_anchor_diagnostics(self):
        for _ in range(4):
            first = self._load_sequence(FOLLOW_ANCHOR_DIAGNOSTICS_OFFSET + 8)
            if first & 1:
                continue
            data = bytes(self._mapping[FOLLOW_ANCHOR_DIAGNOSTICS_OFFSET:
                                      FOLLOW_ANCHOR_DIAGNOSTICS_OFFSET + FOLLOW_ANCHOR_DIAGNOSTICS.size])
            if first != self._load_sequence(FOLLOW_ANCHOR_DIAGNOSTICS_OFFSET + 8):
                continue
            if not any(data):
                return None
            magic, sequence, abi, flags, target, scopes, blends, corrections, rejected = FOLLOW_ANCHOR_DIAGNOSTICS.unpack(data)
            if magic != b"DLYFANC1" or sequence != first or abi != 1 or flags & ~1:
                raise NativeBridgeError("Native Follow camera diagnostics do not match this editor build")
            return {"installed": bool(flags & 1), "target_handle": target,
                    "scopes": scopes, "blends": blends,
                    "corrections": corrections, "rejected": rejected}
        return None

    @staticmethod
    def _optional_status(reader):
        """Telemetry failure belongs to its feature, not the camera protocol."""
        try:
            return reader()
        except (NativeBridgeError, OSError, ValueError, struct.error) as exc:
            return {"error": str(exc)}

    def capabilities(self):
        """Read feature admission without invalidating an independent view."""
        from . import native_capabilities as wire
        def read():
            for _ in range(4):
                first = self._load_sequence(wire.OFFSET + 8)
                if first:
                    self._capabilities_seen = True
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.OFFSET:wire.OFFSET + wire.WIRE.size])
                if first != self._load_sequence(wire.OFFSET + 8):
                    continue
                if struct.unpack_from('<I', data, 8)[0] != first:
                    continue
                if any(data):
                    self._capabilities_seen = True
                result = wire.unpack(data, self.game_pid)
                if result is None and getattr(self, '_capabilities_seen', False):
                    raise NativeBridgeError('Native capability report disappeared')
                return result
            raise NativeBridgeError('Native capability report is busy; retry the feature')
        with self._lock:
            self._check_open()
            return self._optional_status(read)

    def capability_available(self, feature):
        from .native_capabilities import available
        return available(self.capabilities(), feature)

    def require_capability(self, feature):
        from .native_capabilities import available
        report = self.capabilities()
        if not available(report, feature):
            reason = report.get('error') if isinstance(report, dict) else None
            if not reason:
                reason = ', '.join(name.replace('_', ' ') + ': ' + value['state']
                                   + ' (' + value['reason'].replace('_', ' ') + ')'
                                   for name, value in report.items()
                                   if value['state'] not in ('ready', 'not_required'))
            label = 'Game Follow' if feature == 'follow' else 'Native camera'
            raise NativeBridgeError(label + ' is unavailable: ' + str(reason))

    def status(self):
        """Return one coherent, validated native status snapshot."""
        with self._lock:
            self._check_open()
            self._sample_graphics()
            self._sample_input()
            for _ in range(8):
                first = self._load_sequence()
                if first & 1:
                    self._sleep(0.0005)
                    continue
                data = bytes(self._mapping[CONTROL_BYTES:CONTROL_BYTES + STATUS.size])
                second = self._load_sequence()
                if first == second and not second & 1 and struct.unpack_from("<I", data, 8)[0] == first:
                    break
                self._sleep(0.0005)
            else:
                raise NativeStatusBusy("Native camera status is busy; retry the operation")
            if data == b"\0" * STATUS.size:
                return {"state": "starting", "state_code": 0, "abi": ABI,
                        "game_pid": self.game_pid, "ack_command": 0, "complete": False,
                        "frame_count": 0, "phase": 0.0,
                        "message": "Waiting for the native camera plugin",
                        "confetti_diagnostics": None}
            values = STATUS.unpack(data)
            magic, sequence, abi, state, pid, ack, error = values[:7]
            if magic != STATUS_MAGIC or abi != ABI:
                raise NativeBridgeError("Native camera protocol does not match this editor build")
            if not self.game_pid or pid != self.game_pid:
                raise NativeBridgeError("Native camera status belongs to a different game process")
            if state >= len(STATES) or ack > self._command:
                raise NativeBridgeError("Native camera returned an invalid state or command acknowledgment")
            frame_count, real_time, engine_time, phase, tick, paused = values[7:13]
            original, applied = list(values[13:20]), list(values[20:27])
            original_fov, applied_fov, hook_calls, message, demo, maximum_ms, interval_ms = values[27:34]
            effect_count, effect_error, effect_frames, effect_phase = values[34:]
            if effect_count > len(EFFECTS) or effect_error > 3:
                raise NativeBridgeError("Native effect status is invalid")
            numbers = [effect_phase, real_time, engine_time, phase, *original, *applied,
                       original_fov, applied_fov, maximum_ms, interval_ms]
            if not all(math.isfinite(value) for value in numbers) or paused not in (0, 1):
                raise NativeBridgeError("Native camera returned non-finite view or timing data")
            result = {"state": STATES[state], "state_code": state, "abi": abi,
                    "game_pid": pid, "ack_command": ack, "error": error,
                    "complete": state == 4, "frame_count": frame_count,
                    "real_time": real_time, "engine_time": engine_time,
                    "phase": phase, "tick": tick, "paused": bool(paused),
                    "original_pose": original, "applied_pose": applied,
                    "original_fov": original_fov, "applied_fov": applied_fov,
                    "hook_calls": hook_calls,
                    "effect_count": effect_count, "effect_error": effect_error,
                    "effect_frames": effect_frames, "effect_phase": effect_phase,
                    "message": message.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
                    "demo_name": demo.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
                    "max_frame_interval_ms": maximum_ms, "frame_interval_ms": interval_ms,
                    "confetti_diagnostics": self._optional_status(self._confetti_diagnostics),
                    "follow_anchor_diagnostics": self._optional_status(self._follow_anchor_diagnostics),
                    "capabilities": self.capabilities()}
            now = self._clock()
            if now - self._view_sample_at >= 1:
                # Reuse this validated read, including outside path playback.
                # Compare game-provided and applied poses with input/graphics
                # observations; no additional game reads or writes are needed.
                self._view_sample_at = now
                self._view_samples.append({"sampled_at": now, **deepcopy(result)})
            return result

    def _wait(self, command, states, timeout):
        deadline = self._clock() + timeout
        while True:
            # A writer holding the status sequence odd is still progress: poll
            # it like an unacknowledged snapshot. Other status errors end here.
            try:
                snapshot = self.status()
            except NativeStatusBusy as busy:
                pending = str(busy)
            else:
                acknowledged = snapshot["ack_command"] == command
                # A previous command's fault remains visible until the callback
                # processes this command. In particular, Release and a fresh Hold
                # must be allowed to recover instead of failing on that old error.
                # Unsupported is a session-wide startup/build failure, independent
                # of command acknowledgments, and cannot be recovered by a shot.
                if snapshot["state"] == "unsupported" or (acknowledged and snapshot["state"] == "fault"):
                    raise NativeBridgeError(snapshot.get("message") or "Native camera refused this game build or view")
                if acknowledged and snapshot["state"] in states:
                    return snapshot
                pending = snapshot.get("message", snapshot["state"])
            self._check_game()
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise NativeBridgeError("Native camera did not acknowledge the command in time: " + pending)
            self._sleep(min(0.01, remaining))

    def prepare(self, project, start, speed, frozen, demo_name, timeout=5, *, pov=False):
        start = _number(start, "Start time")
        speed = _number(speed, "Playback speed", positive=True)
        timeout = _number(timeout, "Native acknowledgment timeout")
        if not isinstance(frozen, bool):
            raise ValueError("Frozen preview must be a boolean")
        if not isinstance(demo_name, str) or not demo_name or any(c in demo_name for c in "\0\r\n"):
            raise ValueError("Native camera requires the current replay name")
        demo = demo_name.encode("utf-8")
        if len(demo) >= 512:
            raise ValueError("Replay name is too long for the native camera protocol")
        payload = compile_shot(project)
        if type(pov) is not bool or (pov and (frozen or project.tracks or project.setup_values
                                             or any(key.source != "free" for key in project.keyframes))):
            raise ValueError("POV segments cannot override the game's camera or effects")
        if start > project.duration:
            raise ValueError("Native start time exceeds the shot duration")
        with self._operations:
            self._check_open()
            if not self.game_pid:
                raise NativeBridgeError("Native camera requires its launched game process")
            self._prepared = False
            self._manual = False
            self._start, self._speed, self._demo = start, speed, demo
            self._frozen = bool(frozen)
            self._pov = pov
            self._confetti_enabled = project.confetti_enabled
            self._confetti_spawn_height = round(project.confetti_spawn_height)
            self._confetti_despawn_on_ground = project.confetti_despawn_on_ground
            self._particles_preset = project.particles_preset
            self._particles_intensity = float(project.particles_intensity)
            self._flags = self._compose_flags()
            command = self._publish(1, payload)
            try:
                result = self._wait(command, {"armed"}, timeout)
            except Exception:
                self._publish(0)
                raise
            self._prepared = True
            return result

    def _compose_flags(self):
        confetti = 0
        if self._confetti_enabled:
            confetti = (CONFETTI_FLAG |
                        (CONFETTI_DESPAWN_FLAG if self._confetti_despawn_on_ground else 0) |
                        (self._confetti_spawn_height << CONFETTI_HEIGHT_SHIFT) |
                        (PARTICLE_IDS.index(self._particles_preset) << PARTICLE_PRESET_SHIFT) |
                        (_intensity_bits(self._particles_intensity) << PARTICLE_INTENSITY_SHIFT))
        return (1 if self._frozen else 0) | (16 if getattr(self, "_pov", False) else 2) | (0 if self._relief else 4) | confetti

    def set_seek_relief(self, enabled):
        """Enable or disable the native render relief applied while seeking.

        Republishes the current control flags without advancing the command so
        the change applies immediately, including during the game's own seeks.
        """
        with self._operations, self._lock:
            self._check_open()
            self._relief = bool(enabled)
            self._flags = self._compose_flags()
            self._publish(self._mode, increment=False)

    def set_confetti(self, enabled, spawn_height=CONFETTI_SPAWN_HEIGHT_DEFAULT,
                     despawn_on_ground=False, *, preset=None, intensity=None):
        """Apply particle controls immediately to the active native view."""
        if not isinstance(enabled, bool) or not isinstance(despawn_on_ground, bool):
            raise ValueError("Particle switches must be booleans")
        height = _number(spawn_height, "Particle spawn height", positive=True)
        if not CONFETTI_SPAWN_HEIGHT_MIN <= height <= CONFETTI_SPAWN_HEIGHT_MAX:
            raise ValueError(
                f"Particle spawn height must be between {CONFETTI_SPAWN_HEIGHT_MIN:g} "
                f"and {CONFETTI_SPAWN_HEIGHT_MAX:g}")
        if preset is not None and preset not in PARTICLE_IDS:
            raise ValueError("Choose a particle preset from the library")
        level = None
        if intensity is not None:
            level = _number(intensity, "Particle intensity", positive=True)
            if not PARTICLE_INTENSITY_MIN <= level <= PARTICLE_INTENSITY_MAX:
                raise ValueError(
                    f"Particle intensity must be between {PARTICLE_INTENSITY_MIN:g} "
                    f"and {PARTICLE_INTENSITY_MAX:g}")
        with self._operations, self._lock:
            self._check_open()
            self._confetti_enabled = enabled
            self._confetti_spawn_height = round(height)
            self._confetti_despawn_on_ground = despawn_on_ground
            if preset is not None:
                self._particles_preset = preset
            if level is not None:
                self._particles_intensity = level
            self._flags = self._compose_flags()
            self._publish(self._mode, increment=False)

    def set_particles(self, enabled, spawn_height, despawn_on_ground, preset, intensity):
        """Preset-aware entry point used by the editor's Particles panel."""
        self.set_confetti(enabled, spawn_height, despawn_on_ground,
                          preset=preset, intensity=intensity)

    def play(self, timeout=3):
        timeout = _number(timeout, "Native acknowledgment timeout")
        with self._operations:
            self._check_open()
            if not self._prepared:
                raise NativeBridgeError("Prepare a native camera path before playing")
            if self._editor_values.get("enabled"):
                # Authored footage starts with the panel hidden. F8 can reopen
                # it; manual integration is inactive while the path plays.
                self.configure_editor(owner="flight")
            command = self._publish(2)
            try:
                return self._wait(command, {"playing", "completed"}, timeout)
            except Exception:
                self._prepared = False
                self._publish(0)
                if self._editor_values.get("enabled"):
                    self.configure_editor(owner="panel")
                raise

    def release(self, timeout=3):
        timeout = _number(timeout, "Native acknowledgment timeout")
        with self._operations:
            self._check_open()
            self._prepared = False
            self._manual = False
            command = self._publish(0)
            if not self.game_pid:
                return self.status()
            # Only Stopped follows native effect restoration. Probe means no
            # native command exists, so it cannot acknowledge this release.
            return self._wait(command, {"stopped"}, timeout)

    def hold(self, timeout=3):
        """Freeze the native callback's latest phase without a read/write race."""
        timeout = _number(timeout, "Native acknowledgment timeout")
        with self._operations:
            self._check_open()
            if not self._prepared and not self._manual:
                raise NativeBridgeError("Prepare a native camera path before holding")
            command = self._publish(3)
            return self._wait(command, {"armed"}, timeout)

    def _wait_editor_input(self, timeout, cancelled=None, *, require_paused=True):
        """Wait for the actual game window's input/renderer, not foreground focus."""
        required = ("enabled", "ready", "overlay_available", "input_available")
        if require_paused:
            required += ("paused",)
        deadline = self._clock() + timeout
        editor = {}
        while True:
            if cancelled is not None and cancelled():
                raise NativeBridgeError("Opening the paused camera was cancelled.")
            try:
                native = self.status()
            except NativeStatusBusy:
                pass  # A writer mid-publication is progress, as in _wait.
            else:
                if native["state"] == "unsupported":
                    raise NativeBridgeError(native.get("message") or "Native camera does not support this game build")
                try:
                    editor = self.editor_status()
                except NativeBridgeError as exc:
                    if "being updated" not in str(exc):
                        raise
                else:
                    if all(editor.get(key) for key in required):
                        return editor
            self._check_game()
            remaining = deadline - self._clock()
            if remaining <= 0:
                missing = [label for key, label in (("overlay_available", "DX11 panel"),
                           ("input_available", "game input"), ("ready", "replay view"),
                           ("paused", "paused replay"))
                           if key in required and not editor.get(key)]
                raise NativeBridgeError("Timed out waiting for " + ", ".join(missing or ["editor initialization"]) +
                    ". Let the replay finish loading, then retry Paused camera. Export diagnostics if it persists.")
            self._sleep(min(0.02, remaining))

    def start_flight(self, demo_name, pose=None, timeout=8, *, cancelled=None, owner="flight",
                     playback=False, phase=0.0):
        """Enter native manual flight, optionally seeded from a displayed view.

        ``playback`` selects the replay state to wait for: ``False`` requires
        the paused camera flow, ``True`` arms against a replay that keeps
        playing, and ``None`` follows the replay's current state.
        """
        timeout = _number(timeout, "Native acknowledgment timeout")
        phase = _number(phase, "Manual camera phase")
        if phase < 0:
            raise ValueError("Manual camera phase must be nonnegative")
        if owner not in ("flight", "panel"):
            raise ValueError("Native flight owner must be flight or panel")
        if playback is not None and not isinstance(playback, bool):
            raise ValueError("Native flight playback mode must be a boolean or None")
        if cancelled is not None and not callable(cancelled):
            raise ValueError("Native flight cancellation must be callable")
        if not isinstance(demo_name, str) or not demo_name or any(c in demo_name for c in "\0\r\n"):
            raise ValueError("Native flight requires the current replay name")
        demo = demo_name.encode("utf-8")
        if len(demo) >= 512:
            raise ValueError("Replay name is too long")
        payload = b""
        if pose is not None:
            try:
                numbers = tuple(float(x) for x in pose)
            except (TypeError, ValueError) as exc:
                raise ValueError("Manual camera requires seven finite values") from exc
            if len(numbers) != 7 or not all(math.isfinite(x) and abs(x) <= 1e8 for x in numbers) or not .25 <= numbers[6] <= 8:
                raise ValueError("Manual camera pose is invalid")
            payload = struct.pack("<7d", *numbers)
        with self._operations:
            self._check_open()
            if not self.game_pid:
                raise NativeBridgeError("Native flight requires its launched game process")
            self._prepared = False
            self._manual = False
            self._demo, self._start, self._speed = demo, phase, 1.0
            self._frozen = playback is not True
            self._pov = False
            self._flags = self._compose_flags()
            self.configure_editor(enabled=True, owner="panel")
            try:
                self._wait_editor_input(timeout, cancelled, require_paused=playback is False)
                if cancelled is not None and cancelled():
                    raise NativeBridgeError("Opening the paused camera was cancelled.")
                self.configure_editor(owner=owner)
                command = self._publish(4, payload)
                result = self._wait(command, {"armed"}, timeout)
            except Exception:
                self._publish(0)
                self.configure_editor(owner="panel")
                raise
            self._manual = True
            return result

    def configure_editor(self, **values):
        """Publish UI/input settings without replacing the native camera path."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            changed = dict(self._editor_values)
            changed.update(values)
            owner_sequence = self._editor_owner_sequence + int("owner" in values)
            if owner_sequence > 0xffffffff:
                raise NativeBridgeError("Editor owner counter exhausted; relaunch Dolly")
            odd = (self._editor_sequence + 1) & 0xffffffff
            even = (odd + 1) & 0xffffffff
            data = wire.pack_config(odd, owner_sequence, self._editor_ack, changed)
            offset = wire.CONFIG_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_sequence, self._editor_owner_sequence = even, owner_sequence
            self._editor_values = changed

    def configure_editor_dof(self, values, *, enabled):
        """Optional shot-settings block; does not mutate camera/effect runtime."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_dof_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_dof(odd, enabled, values)
            offset = wire.DOF_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_dof_sequence = even

    def configure_editor_citadel_dof(self, sensor_size, focus_distance, *, available, enabled):
        """Optional Citadel DOF block; does not mutate camera/effect runtime."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_citadel_dof_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_citadel_dof(odd, available, enabled, sensor_size, focus_distance)
            offset = wire.CITADEL_DOF_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_citadel_dof_sequence = even

    def configure_editor_follow(self, settings, *, available=False, active=False, pending=False, show_hud=False):
        """Publish Follow controls only; camera settings are applied by Controller."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            available = bool(available and self.capability_available('follow'))
            previous = getattr(self, '_editor_follow_sequence', 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_follow(odd, settings, available=available, active=active, pending=pending, show_hud=show_hud)
            offset = wire.FOLLOW_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_follow_sequence = even

    def configure_editor_cameras(self, project, revision, first=0, *, can_undo=False, can_redo=False):
        """Bounded camera-list page. Existing editor ABI and offsets stay unchanged."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, '_editor_cameras_sequence', 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_camera_list(odd, project, revision, first,
                                         can_undo=can_undo, can_redo=can_redo)
            offset = wire.CAMERA_LIST_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_cameras_sequence = even

    def configure_shader_loading(self, loading):
        """Publish a noninteractive startup notice independently of editor ownership."""
        from .lite_mode import REPLAY_INFO, REPLAY_INFO_OFFSET, pack_replay_info
        from .path import Project
        with self._lock:
            self._check_open()
            self._shader_loading = bool(loading)
            previous = getattr(self, "_lite_replay_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = bytes(self._mapping[REPLAY_INFO_OFFSET:REPLAY_INFO_OFFSET + REPLAY_INFO.size])
            if data[:8] != b'DLYLITE1':
                data = pack_replay_info(odd, Project(), 1)
            values = list(REPLAY_INFO.unpack(data))
            values[1] = odd
            values[6] = (values[6] & ~4) | (4 if loading else 0)
            data = REPLAY_INFO.pack(*values)
            self._store(REPLAY_INFO_OFFSET + 8, odd)
            self._mapping[REPLAY_INFO_OFFSET:REPLAY_INFO_OFFSET + 8] = data[:8]
            self._mapping[REPLAY_INFO_OFFSET + 12:REPLAY_INFO_OFFSET + len(data)] = data[12:]
            self._store(REPLAY_INFO_OFFSET + 8, even)
            self._lite_replay_sequence = even

    def configure_lite_replay(self, project, revision, first=0, total_ticks=None):
        from .lite_mode import pack_replay_info, REPLAY_INFO_OFFSET
        with self._lock:
            self._check_open()
            previous = getattr(self, "_lite_replay_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = pack_replay_info(odd, project, revision, first, total_ticks)
            if getattr(self, "_shader_loading", False):
                from .lite_mode import REPLAY_INFO
                values = list(REPLAY_INFO.unpack(data))
                values[6] |= 4
                data = REPLAY_INFO.pack(*values)
            offset = REPLAY_INFO_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset+8] = data[:8]
            self._mapping[offset+12:offset+len(data)] = data[12:]
            self._store(offset + 8, even)
            self._lite_replay_sequence = even

    def configure_editor_cinematic(self, token, revision, state):
        from . import editor_wire as wire
        if not 0 < token <= 0xffffffff or not 0 <= revision <= 0xffffffff or state not in (1, 2, 3):
            raise ValueError("Invalid cinematic preparation result")
        with self._lock:
            self._check_open()
            previous = getattr(self, '_cinematic_sequence', 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.CINEMATIC_STATUS.pack(b'DLYCIN01', odd, 1, token, revision, state)
            offset = wire.CINEMATIC_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._cinematic_sequence = even

    def configure_replay_events(self, index):
        from . import replay_event_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, '_replay_events_sequence', 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack(odd, index)
            offset = wire.OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset+8] = data[:8]
            self._mapping[offset+12:offset+len(data)] = data[12:]
            self._store(offset + 8, even)
            self._replay_events_sequence = even

    def configure_framing_grid(self, *, enabled=False, binding=None):
        """Optional framing-guide block; enabled state and its bindable action."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_framing_grid_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_framing_grid(odd, enabled, binding)
            offset = wire.FRAMING_GRID_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_framing_grid_sequence = even

    def configure_level_view(self, *, binding=None, standard_aspect=None):
        """Optional level-view block: the key binding and the normal framing.

        Native keeps the live-only reset; this block only carries the shortcut
        and the project's normal aspect so nothing round-trips through Python
        while the camera is being levelled.
        """
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_level_view_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_level_view(odd, binding, standard_aspect)
            offset = wire.LEVEL_VIEW_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_level_view_sequence = even

    def configure_object_picker(self, objects, *, active=False, preview=False, selected=-1,
                                distance=None, binding=None):
        """Optional Object Picker block: the placed list and its bindable action.

        The block lives in one appended 4 KiB page (object_wire.OBJECT_BASE); the
        mapping is sized to include it. ``objects`` is the project's placed list.
        """
        from . import object_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_object_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            distance = wire.DISTANCE_DEFAULT if distance is None else distance
            reserved = 0
            if binding is not None:
                reserved = binding.vk | binding.modifiers << 16
            data = bytearray(wire.pack_config(odd, objects, active=active, preview=preview,
                                              selected=selected, distance=distance))
            # Patch the bindable action into the reserved header field (the wire
            # packer leaves it zero so the layout stays a pure function of state).
            reserved_offset = wire.OBJECT_CONFIG_HEADER.size - 4
            struct.pack_into("<I", data, reserved_offset, reserved)
            offset = wire.OBJECT_CONFIG_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_object_sequence = even

    def configure_editor_attach(self, offsets, attach=None, preview=False, snap_request=0, picker=False):
        """Optional attach block: schema offsets plus the selected key's state."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_attach_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_attach(odd, offsets, attach, preview=bool(preview),
                                    snap_request=snap_request, picker=bool(picker))
            offset = wire.ATTACH_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_attach_sequence = even
            self._editor_attach_diagnostics = {"sequence": even, "offsets": dict(offsets),
                "selection": deepcopy(attach), "preview": bool(preview), "snap_request": snap_request,
                "picker": bool(picker)}

    def editor_bone_picker(self):
        """Optional native picker session/result; old DLLs leave it unwritten."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            for _ in range(4):
                first = self._load_sequence(wire.PICKER_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.PICKER_OFFSET:wire.PICKER_OFFSET + wire.PICKER_RESULT.size])
                if first == self._load_sequence(wire.PICKER_OFFSET + 8):
                    return wire.unpack_picker(data)
            raise NativeBridgeError("The Bone Picker is updating; retry in a moment.")

    def editor_object_status(self):
        """Optional native Object Picker status; old DLLs leave it unwritten."""
        from . import object_wire as wire
        with self._lock:
            self._check_open()
            for _ in range(4):
                first = self._load_sequence(wire.OBJECT_STATUS_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.OBJECT_STATUS_OFFSET:
                                           wire.OBJECT_STATUS_OFFSET + wire.OBJECT_STATUS.size])
                if first == self._load_sequence(wire.OBJECT_STATUS_OFFSET + 8):
                    return wire.unpack_status(data)
            raise NativeBridgeError("The Object Picker is updating; retry in a moment.")

    def editor_roster(self):
        """Optional native player roster for the attach target picker."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            for _ in range(4):
                first = self._load_sequence(wire.ROSTER_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.ROSTER_OFFSET:wire.ROSTER_OFFSET + wire.ROSTER_BYTES])
                if first != self._load_sequence(wire.ROSTER_OFFSET + 8):
                    continue
                return wire.unpack_roster(data)
            raise NativeStatusBusy("The native attach roster is busy; retry in a moment.")

    def set_prop_probe(self, request):
        """Experimental: queue one native prop-persistence probe request.

        ``request`` is 'create' or 'recount'. This is a probe, not a feature; it
        is only used by the bounded persistence test harness.
        """
        from . import editor_wire as wire
        code = {"create": wire.PROP_PROBE_CREATE, "recount": wire.PROP_PROBE_RECOUNT}.get(request)
        if code is None:
            raise ValueError("Prop probe request must be 'create' or 'recount'")
        with self._lock:
            self._check_open()
            previous = getattr(self, "_editor_prop_probe_sequence", 0)
            odd, even = (previous + 1) & 0xffffffff, (previous + 2) & 0xffffffff
            data = wire.pack_prop_probe(odd, code)
            offset = wire.PROP_PROBE_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_prop_probe_sequence = even

    def editor_prop_probe_status(self):
        """Read the native prop-probe result (counts, handle, error)."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            offset = wire.PROP_PROBE_OFFSET + wire.PROP_PROBE_CONFIG.size
            for _ in range(4):
                first = self._load_sequence(offset + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[offset:offset + wire.PROP_PROBE_STATUS.size])
                if first == self._load_sequence(offset + 8):
                    return wire.unpack_prop_probe_status(data)
            raise NativeBridgeError("The prop probe is updating; retry in a moment.")

    def editor_bones(self):
        """Bounded bone names for the natively resolved selected player."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            for _ in range(4):
                first = self._load_sequence(wire.BONES_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.BONES_OFFSET:wire.BONES_OFFSET + wire.BONES_BYTES])
                if first == self._load_sequence(wire.BONES_OFFSET + 8):
                    return wire.unpack_bones(data) if any(data) else None
            raise NativeBridgeError("The native bone picker is busy; retry in a moment.")

    def editor_attach_result(self):
        """Latest native snap / attached-fly offsets, or None while unwritten."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            for _ in range(4):
                first = self._load_sequence(wire.ATTACH_RESULT_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.ATTACH_RESULT_OFFSET:
                                           wire.ATTACH_RESULT_OFFSET + wire.ATTACH_RESULT.size])
                if first != self._load_sequence(wire.ATTACH_RESULT_OFFSET + 8):
                    continue
                if not any(data):
                    return None
                return wire.unpack_attach_result(data)
            raise NativeBridgeError("The native attach result is busy; retry in a moment.")

    def editor_status(self):
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            self._sample_graphics()
            # Manual flight polls this channel, not status(). Keep its camera
            # history alongside graphics samples even when no shot is playing.
            # A busy/invalid camera snapshot must not block editor recovery.
            now = self._clock()
            if now - self._editor_view_poll_at >= 1:
                self._editor_view_poll_at = now
                try:
                    self.status()
                except (NativeBridgeError, OSError, ValueError):
                    pass
            for _ in range(4):
                first = self._load_sequence(wire.STATUS_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.STATUS_OFFSET:wire.STATUS_OFFSET + wire.STATUS_BYTES])
                second = self._load_sequence(wire.STATUS_OFFSET + 8)
                if first == second and not second & 1 and struct.unpack_from("<I", data, 8)[0] == first:
                    try:
                        return wire.unpack_status(data, self._editor_ack)
                    except ValueError as exc:
                        raise NativeBridgeError(str(exc)) from exc
            raise NativeBridgeError("Native editor status is being updated")

    def acknowledge_editor_event(self, sequence):
        with self._lock:
            self._check_open()
            if type(sequence) is not int or sequence != self._editor_ack + 1:
                raise NativeBridgeError("Native editor actions must be acknowledged in order")
            self._editor_ack = sequence
            self.configure_editor()

    def _sample_graphics(self, *, force=False):
        """Read at most once a second; probe errors cannot stop camera input."""
        if self._closed:
            return
        now = self._clock()
        if not force and now - self._graphics_sample_at < 1:
            return
        self._graphics_sample_at = now
        from . import graphics_diagnostics as wire
        try:
            for _ in range(2):
                before = self._load_sequence(wire.OFFSET + 8)
                if before & 1:
                    continue
                data = bytes(self._mapping[wire.OFFSET:wire.OFFSET + wire.WIRE.size])
                after = self._load_sequence(wire.OFFSET + 8)
                if before != after or struct.unpack_from("<I", data, 8)[0] != before:
                    continue
                sample = wire.unpack(data)
                if sample is not None and (not self._graphics_samples or
                        sample["sample"] != self._graphics_samples[-1]["sample"]):
                    # First observation time on the same monotonic bridge
                    # clock as input samples; leave cached times unchanged.
                    sample["sampled_at"] = now
                    self._graphics_samples.append(sample)
                self._graphics_error = ""
                return
            self._graphics_error = "Graphics snapshot was being updated; retained previous samples."
        except (NativeBridgeError, OSError, ValueError, struct.error) as exc:
            self._graphics_error = str(exc)

    def latest_graphics(self):
        """Most recent cached renderer observation, without forcing a new read."""
        with self._lock:
            return deepcopy(self._graphics_samples[-1]) if self._graphics_samples else None

    def graphics_diagnostics(self):
        with self._lock:
            self._sample_graphics(force=True)
            samples = deepcopy(list(self._graphics_samples))
            return {"cached_after_close": self._closed, "samples": samples,
                    "latest": samples[-1] if samples else None,
                    "read_error": self._graphics_error,
                    "note": "Read-only asynchronous observations; sampled_at is the first observation in monotonic bridge-clock seconds, not the native frame time. "
                            "No renderer state or camera timing is changed."}

    def _sample_input(self, *, force=False):
        """Bound optional telemetry reads; an input probe cannot fault the camera."""
        if self._closed:
            return
        now = self._clock()
        if not force and now - self._input_sample_at < 1:
            return
        self._input_sample_at = now
        from . import input_diagnostics as wire
        try:
            for _ in range(2):
                before = self._load_sequence(wire.OFFSET + 8)
                if before & 1:
                    continue
                data = bytes(self._mapping[wire.OFFSET:wire.OFFSET + wire.WIRE.size])
                after = self._load_sequence(wire.OFFSET + 8)
                if before != after or struct.unpack_from("<I", data, 8)[0] != before:
                    continue
                sample = wire.unpack(data)
                if sample is not None and (not self._input_samples or
                        sample["sequence"] != self._input_samples[-1]["sequence"]):
                    sample["sampled_at"] = now
                    self._input_samples.append(sample)
                self._input_error = ""
                return
            self._input_error = "Input snapshot was being updated; retained previous samples."
        except (NativeBridgeError, OSError, ValueError, struct.error) as exc:
            self._input_error = str(exc)

    def input_diagnostics(self):
        with self._lock:
            self._sample_input(force=True)
            samples = deepcopy(list(self._input_samples))
            return {"cached_after_close": self._closed, "samples": samples,
                    "latest": samples[-1] if samples else None,
                    "read_error": self._input_error,
                    "note": "Read-only observations; cursor_clipped records Dolly's last successful cursor request. "
                            "Packet counters cover configured, focused input. "
                            "sampled_at is the first observation in monotonic bridge-clock seconds, not the native input event time. "
                            "No mouse registration, cursor state or camera timing is changed."}

    def _viewer_store_sequence(self, value):
        from . import visualization_wire as wire
        if self._atomic32 is None:
            struct.pack_into("<I", self._viewer_mapping, wire.SEQUENCE_OFFSET, value)
        else:
            slot = ctypes.c_int32.from_buffer(self._viewer_mapping, wire.SEQUENCE_OFFSET)
            self._atomic32(ctypes.byref(slot), ctypes.c_int32(value))

    def _viewer_write(self, packet):
        """Commit through the helper's release barrier, leaving no torn packet."""
        from . import visualization_wire as wire
        sequence = struct.unpack_from("<I", packet, wire.SEQUENCE_OFFSET)[0]
        self._viewer_store_sequence(sequence - 1)
        self._viewer_mapping[:wire.SEQUENCE_OFFSET] = packet[:wire.SEQUENCE_OFFSET]
        self._viewer_mapping[wire.SEQUENCE_OFFSET + 4:len(packet)] = packet[wire.SEQUENCE_OFFSET + 4:]
        self._viewer_store_sequence(sequence)
        self._viewer_sequence = sequence
        self._viewer_enabled = bool(struct.unpack_from("<I", packet, 16)[0])

    def publish_visualization(self, project=None, *, enabled=True, selected_camera=0, revision=0, session_id=0):
        """Publish optional in-game guides without changing camera transport.

        Call only after a shot, selection, or visibility change; compilation
        runs on the editor side and never streams samples during playback.
        Viewer failures remain diagnostic and cannot fail camera operations.
        """
        from . import visualization_wire as wire
        with self._lock:
            try:
                self._check_open()
                sequence = self._viewer_sequence + 2
                if sequence > 0xFFFFFFFE:
                    sequence = 2
                packet = wire.build_visualization(project, sequence=sequence,
                                                   enabled=enabled, selected_camera=selected_camera,
                                                   revision=revision, session_id=session_id)
                if self._viewer_mapping is None:
                    # A disabled or empty shot needs no additional OS object.
                    if not struct.unpack_from("<I", packet, 16)[0]:
                        self._viewer_enabled = False
                        self._viewer_error = ""
                        return True
                    name = "Local\\DeadlockDollyNative_" + self.token + ".viewer"
                    if self._mapping_factory is None:
                        if os.name != "nt":
                            raise NativeBridgeError("Path viewer transport requires Windows")
                        mapping = mmap.mmap(-1, wire.MAPPING_BYTES, tagname=name, access=mmap.ACCESS_WRITE)
                    else:
                        mapping = self._mapping_factory(name, wire.MAPPING_BYTES)
                    if len(mapping) != wire.MAPPING_BYTES:
                        close = getattr(mapping, "close", None)
                        if close is not None:
                            close()
                        raise NativeBridgeError("Path viewer mapping has an unexpected size")
                    self._viewer_mapping = mapping
                    mapping[:] = b"\0" * wire.MAPPING_BYTES
                self._viewer_write(packet)
                self._viewer_error = ""
                return True
            except Exception as exc:
                self._viewer_error = str(exc)
                # Invalid edits must not leave an earlier path misleadingly
                # visible. A clearing packet is independent of shot parsing.
                if self._viewer_mapping is not None:
                    try:
                        sequence = self._viewer_sequence + 2
                        if sequence > 0xFFFFFFFE:
                            sequence = 2
                        self._viewer_write(wire.build_visualization(None, sequence=sequence, enabled=False))
                    except Exception:
                        self._viewer_enabled = False
                return False

    def _close_visualization(self):
        """Clear before unmapping: the game may still hold its read-only view."""
        if self._viewer_mapping is None:
            return
        from . import visualization_wire as wire
        mapping = self._viewer_mapping
        try:
            sequence = self._viewer_sequence + 2
            if sequence > 0xFFFFFFFE:
                sequence = 2
            self._viewer_store_sequence(sequence - 1)
            mapping[:8] = b"\0" * 8
            mapping[12:] = b"\0" * (wire.MAPPING_BYTES - 12)
            self._viewer_store_sequence(sequence)
            self._viewer_enabled = False
        except Exception as exc:
            self._viewer_error = "Path viewer close: " + str(exc)
        finally:
            self._viewer_mapping = None
            close = getattr(mapping, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    self._viewer_error = "Path viewer close: " + str(exc)

    def _preview_store_sequence(self, value):
        from . import visualization_preview_wire as wire
        if self._atomic32 is None:
            struct.pack_into("<I", self._preview_mapping, wire.SEQUENCE_OFFSET, value)
        else:
            slot = ctypes.c_int32.from_buffer(self._preview_mapping, wire.SEQUENCE_OFFSET)
            self._atomic32(ctypes.byref(slot), ctypes.c_int32(value))

    def _preview_write(self, packet):
        from . import visualization_preview_wire as wire
        sequence = struct.unpack_from("<I", packet, wire.SEQUENCE_OFFSET)[0]
        self._preview_store_sequence(sequence-1)
        self._preview_mapping[:wire.SEQUENCE_OFFSET] = packet[:wire.SEQUENCE_OFFSET]
        self._preview_mapping[wire.SEQUENCE_OFFSET+4:len(packet)] = packet[wire.SEQUENCE_OFFSET+4:]
        self._preview_store_sequence(sequence)
        self._preview_sequence = sequence
        self._preview_enabled = bool(struct.unpack_from("<I", packet, 16)[0])

    def publish_visualization_preview(self, packet=None, **identity):
        """Publish optional render-only bytes without touching committed guides.

        ``packet`` is already compiled by the bounded gesture lifecycle. Only
        its envelope sequence changes for a stationary-drag heartbeat.
        """
        from . import visualization_preview_wire as wire
        with self._lock:
            try:
                self._check_open()
                sequence = self._preview_sequence+2
                if sequence > 0xFFFFFFFE:
                    sequence = 2
                wrapped = wire.build_preview(packet, sequence=sequence, **identity)
                if self._preview_mapping is None:
                    if packet is None:
                        self._preview_error = ""
                        return True
                    name = "Local\\DeadlockDollyNative_"+self.token+".viewer.preview"
                    if self._mapping_factory is None:
                        if os.name != "nt":
                            raise NativeBridgeError("Path preview transport requires Windows")
                        mapping = mmap.mmap(-1, wire.MAPPING_BYTES, tagname=name, access=mmap.ACCESS_WRITE)
                    else:
                        mapping = self._mapping_factory(name, wire.MAPPING_BYTES)
                    if len(mapping) != wire.MAPPING_BYTES:
                        close = getattr(mapping, "close", None)
                        if close is not None:
                            close()
                        raise NativeBridgeError("Path preview mapping has an unexpected size")
                    self._preview_mapping = mapping
                    mapping[:] = b"\0"*wire.MAPPING_BYTES
                self._preview_write(wrapped)
                self._preview_error = ""
                return True
            except Exception as exc:
                self._preview_error = str(exc)
                # Draft failure clears only the separate optional mapping.
                if self._preview_mapping is not None:
                    try:
                        sequence = self._preview_sequence+2
                        if sequence > 0xFFFFFFFE:
                            sequence = 2
                        self._preview_write(wire.build_preview(sequence=sequence))
                    except Exception:
                        self._preview_enabled = False
                return False

    def _close_visualization_preview(self):
        if self._preview_mapping is None:
            return
        from . import visualization_preview_wire as wire
        mapping = self._preview_mapping
        try:
            sequence = self._preview_sequence+2
            if sequence > 0xFFFFFFFE:
                sequence = 2
            self._preview_store_sequence(sequence-1)
            packet = wire.build_preview(sequence=sequence)
            mapping[:8] = packet[:8]
            mapping[12:] = packet[12:]+b"\0"*(wire.MAPPING_BYTES-len(packet))
            self._preview_store_sequence(sequence)
            self._preview_enabled = False
        except Exception as exc:
            self._preview_error = "Path preview close: "+str(exc)
        finally:
            self._preview_mapping = None
            close = getattr(mapping, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    self._preview_error = "Path preview close: "+str(exc)

    def visualization_preview_diagnostics(self):
        with self._lock:
            return {"mapping_open": self._preview_mapping is not None,
                    "enabled": self._preview_enabled, "revision": self._preview_sequence,
                    "error": self._preview_error}

    def visualization_diagnostics(self):
        with self._lock:
            return {"mapping_open": self._viewer_mapping is not None,
                    "enabled": self._viewer_enabled, "revision": self._viewer_sequence,
                    "error": self._viewer_error}

    def diagnostics(self):
        try:
            result = self.status()
            try:
                result["editor"] = self.editor_status()
            except (NativeBridgeError, ValueError) as exc:
                result["editor"] = {"message": str(exc)}
        except (NativeBridgeError, OSError, ValueError) as exc:
            result = {"state": "unavailable", "message": str(exc)}
        with self._lock:
            result["attach_config"] = deepcopy(getattr(self, "_editor_attach_diagnostics", None))
        for key, reader in (("attach_roster", self.editor_roster),
                            ("attach_bones", self.editor_bones),
                            ("attach_result", self.editor_attach_result)):
            try:
                result[key] = reader()
            except (NativeBridgeError, OSError, ValueError) as exc:
                result[key] = {"error": str(exc)}
        result["graphics"] = self.graphics_diagnostics()
        result["input"] = self.input_diagnostics()
        result["visualization"] = self.visualization_diagnostics()
        result["visualization_preview"] = self.visualization_preview_diagnostics()
        try:
            result["media"] = self.media_status()
        except (NativeBridgeError, OSError, ValueError) as exc:
            result["media"] = {"error": str(exc)}
        with self._lock:
            result["view_history"] = {
                "samples": deepcopy(list(self._view_samples)),
                "cached_after_close": self._closed,
                "note": "At most one validated view observation per second, including manual flight. "
                        "original_pose is the game-provided view before Dolly's override; "
                        "it is not a separate measurement of engine camera caches. "
                        "sampled_at uses the same monotonic observation clock as input and graphics."}
        return result

    def close(self):
        """Release once, stop heartbeats, and close the mapping even after faults."""
        with self._operations:
            if self._closed:
                return
            try:
                self._close_media()
                with self._lock:
                    self._sample_graphics(force=True)
                    self._sample_input(force=True)
            finally:
                try:
                    self.release(timeout=0.25)
                except (NativeBridgeError, OSError, ValueError):
                    pass
                finally:
                    self._heartbeat_stop.set()
                    if self._thread is not None and self._thread is not threading.current_thread():
                        self._thread.join(timeout=0.5)
                    with self._lock, self._heartbeat_lock:
                        self._close_visualization_preview()
                        self._close_visualization()
                        self._closed = True
                        close = getattr(self._mapping, "close", None)
                        if close is not None:
                            close()
