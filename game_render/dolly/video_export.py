"""Normal-scene MP4 recording through the session's native DirectX 11 bridge.

Recording follows real time at the current game resolution. This module does
not change replay timing or claim fixed-step/offline rendering. UI mutations
run on Dolly's operation worker; status reads only inspect bridge telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from . import encoder_select, player_layer
from .console import ConsoleTimeout
from .ffmpeg_run import last_line, remove_partial, run_ffmpeg, scaled_timeout
from .runtime import external_program_environment
from .display import client_size


ACTIVE_STATES = frozenset(("starting", "recording", "finalizing"))
TERMINAL_STATES = frozenset(("idle", "completed", "cancelled", "failed"))
# How the native recorder's error starts when it discards a take with missing
# frames (its error code is 0x8007000D).
LOST_FRAMES_ERROR = "Export lost frames and was discarded"
BITRATE_PRESETS = {"10 Mbps": 10_000_000, "20 Mbps": 20_000_000, "40 Mbps": 40_000_000}
# Output containers offered by the Export tab. The lossless codec and the
# built-in Media Foundation writer each force their own container.
CONTAINER_CHOICES = (("mp4", "MP4 video"), ("mkv", "Matroska video (MKV)"),
                     ("mov", "QuickTime movie (MOV)"))
CONTAINER_LABEL_TO_KEY = {label: key for key, label in CONTAINER_CHOICES}
CONTAINER_KEYS = frozenset(key for key, _label in CONTAINER_CHOICES)
DEFAULT_CONTAINER = "mp4"

# key -> (label, native encoder, native codec, requires ffmpeg). The numeric
# encoder/codec values match dolly::video::Encoder/Codec in dolly_video.hpp.
# "auto" prefers hardware FFmpeg (NVENC) when an ffmpeg path is available and
# otherwise uses the built-in Media Foundation encoder.
CODEC_CHOICES = (
    ("auto", "Auto (hardware when available)", 0, 0, False),
    ("h264_nvenc", "NVIDIA H.264 (NVENC)", 1, 1, True),
    ("hevc_nvenc", "NVIDIA HEVC (NVENC)", 1, 2, True),
    ("h264_qsv", "Intel H.264 (Quick Sync)", 1, 6, True),
    ("hevc_qsv", "Intel HEVC (Quick Sync)", 1, 7, True),
    ("h264_amf", "AMD H.264 (AMF)", 1, 8, True),
    ("hevc_amf", "AMD HEVC (AMF)", 1, 9, True),
    ("h264_mf", "H.264 via ffmpeg Media Foundation", 1, 3, True),
    ("libx264", "Software H.264 (x264, GPL build)", 1, 4, True),
    ("libx265", "Software HEVC (x265, GPL build)", 1, 5, True),
    ("lossless", "Lossless FFV1 (.mkv)", 1, 10, True),
    ("builtin", "Built-in Windows Media Foundation", 0, 0, False),
)
CODEC_BY_KEY = {key: (label, enc, codec, needs) for key, label, enc, codec, needs in CODEC_CHOICES}
CODEC_LABEL_TO_KEY = {label: key for key, label, *_ in CODEC_CHOICES}
DEFAULT_CODEC_KEY = "auto"
LOSSLESS_CODEC_ID = 10
QUALITY_RANGE = (0, 51)
PRESET_RANGE = (0, 7)
# Depth preview encoding maps the native codec id to a bundled-ffmpeg encoder.
# libx264/libx265 are not in the LGPL bundle; libopenh264/hevc_mf stand in.
PREVIEW_CODECS = {1: "h264_nvenc", 2: "hevc_nvenc", 3: "h264_mf", 4: "libopenh264",
                  5: "hevc_mf", 6: "h264_qsv", 7: "hevc_qsv", 8: "h264_amf",
                  9: "hevc_amf", 10: "ffv1"}
LOG = logging.getLogger(__name__)


def resolve_ffmpeg(configured: "Path | None") -> "Path | None":
    """Return a usable ffmpeg.exe path or None; never raises."""
    if not configured:
        return None
    try:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None
    except (OSError, TypeError, ValueError):
        return None


def bundled_ffmpeg_path() -> "Path | None":
    """Locate the pinned FFmpeg shipped with a packaged Dolly, if present."""
    try:
        from .runtime import resource_root
        root = resource_root()
    except (ImportError, OSError, ValueError):
        return None
    for candidate in (root / "third_party" / "ffmpeg" / "bin" / "ffmpeg.exe",
                      root / "_internal" / "third_party" / "ffmpeg" / "bin" / "ffmpeg.exe"):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


_ENCODER_LIST_CACHE: dict[tuple[str, int], "frozenset[str] | None"] = {}


def ffmpeg_encoder_names(ffmpeg: "Path | None") -> "frozenset[str] | None":
    """Read the encoder names a configured FFmpeg actually provides.

    Returns None when the list cannot be read; callers must not treat that as
    "the encoder is missing", because the native writer may still handle it.
    """
    if ffmpeg is None:
        return None
    try:
        path = Path(ffmpeg)
        key = (str(path), path.stat().st_mtime_ns)
    except (OSError, TypeError, ValueError):
        return None
    if key in _ENCODER_LIST_CACHE:
        return _ENCODER_LIST_CACHE[key]
    names: "set[str] | None" = None
    try:
        result = subprocess.run(
            [str(path), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        names = None
    else:
        if result.returncode == 0:
            names = set()
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[0].startswith("V"):
                    names.add(fields[1])
    value = frozenset(names) if names is not None else None
    _ENCODER_LIST_CACHE[key] = value
    return value


def resolve_backend(options: "VideoOptions") -> tuple[int, int]:
    """Choose the native (encoder, codec) pair for validated options.

    Auto maps the machine's GPU vendor to that vendor's FFmpeg encoder instead
    of assuming NVENC, and keeps the built-in encoder when FFmpeg is absent.
    """
    _label, encoder, codec_id, needs_ffmpeg = CODEC_BY_KEY[options.codec]
    if options.codec == "auto":
        if not options.ffmpeg_path:
            return 0, 0
        key, _resolved_label = encoder_select.auto_encoder()
        _key_label, encoder, codec_id, _needs = CODEC_BY_KEY[key]
        return encoder, codec_id
    if needs_ffmpeg and not options.ffmpeg_path:
        return 0, 0
    return encoder, codec_id


def resolve_container(codec_key: str, container: str | None = None, *,
                      ffmpeg_available: bool = True) -> str:
    """The container actually used for a codec, never an invalid combination."""
    _label, _encoder, codec_id, _needs = CODEC_BY_KEY.get(codec_key, CODEC_BY_KEY[DEFAULT_CODEC_KEY])
    if codec_id == LOSSLESS_CODEC_ID:
        return "mkv"
    if codec_key == "builtin" or (codec_key == "auto" and not ffmpeg_available):
        # The built-in Media Foundation writer only creates MP4 files.
        return "mp4"
    return container if container in CONTAINER_KEYS else DEFAULT_CONTAINER


def video_suffix(codec_key: str, container: str | None = None, *,
                 ffmpeg_available: bool = True) -> str:
    """Container suffix for a codec and optional chosen container."""
    return "." + resolve_container(codec_key, container, ffmpeg_available=ffmpeg_available)


# Characters Windows refuses in a filename, plus unpaired surrogates, which no
# filesystem path can encode. Everything else (accents, combining marks, CJK,
# emoji) is kept as typed.
_UNSAFE_STEM = re.compile('[<>:"/\\\\|?*\x00-\x1f\ud800-\udfff]')
# Opening one of these names (with any extension) reaches a device, not a file.
_DEVICE_NAMES = frozenset(("CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                           *(f"{port}{number}" for port in ("COM", "LPT")
                             for number in "0123456789¹²³")))


def safe_stem(name: str, fallback: str = "shot") -> str:
    """A filename stem from a shot name that Windows saves as an ordinary file."""
    stem = _UNSAFE_STEM.sub("_", str(name)).strip(" .")
    if not stem:
        return fallback
    if stem.split(".", 1)[0].rstrip(" ").upper() in _DEVICE_NAMES:
        stem = "_" + stem
    return stem


def default_video_path(shot_name: str, directory: Path | None = None, *, now: datetime | None = None) -> Path:
    """Choose an unused filename without creating directories or files."""
    if directory is None:
        videos = Path.home() / "Videos"
        directory = videos if videos.is_dir() else Path.home()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(shot_name)).strip(" .")[:80] or "Dolly"
    # Prefixing with Dolly avoids Windows device basenames such as CON.mp4.
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"Dolly_{name}_{stamp}"
    candidate = Path(directory) / (stem + ".mp4")
    sequence = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = Path(directory) / f"{stem}_{sequence}.mp4"
        sequence += 1
    return candidate


# Windows MAX_PATH: a file path holds at most 259 characters and a new folder
# 247 (CreateDirectoryW keeps 12 for an 8.3 name). The game's recorder and
# Dolly's own take-folder writes use plain paths, not \\?\ long paths.
MAX_FILE_PATH = 259
MAX_FOLDER_PATH = 247


def take_folder_entries(folder: Path, name: str, options: "VideoOptions") -> list[tuple[Path, bool]]:
    """The longest paths a take creates in its folder, as (path, is_folder)."""
    suffix = Path(name).suffix
    entries = [(folder, True), (folder / (name + ".shot.json"), False)]
    if options.camera_track:
        entries.append((folder / (name + ".camera-track.json.pending-4294967295-18446744073709551615"), False))
    if options.depth:
        depth = folder / "depth"
        entries += [(depth, True), (depth / "manifest.json.part", False),
                    (depth / "preview_65535x65535.raw", False)]
        if options.depth_exr:
            entries += [(depth / "exr", True), (depth / "exr" / "00000000.exr.part", False)]
    for layer in options.layers:
        layer_dir = folder / layer
        entries += [(layer_dir, True), (layer_dir / (layer + "_white" + suffix + ".shot.json"), False),
                    (layer_dir / (layer + ".partial.mov"), False)]
        if layer == "players":
            # player_encode stages the master under a uuid4 hex token.
            entries.append((layer_dir / ("players." + "0" * 32 + ".partial.mov"), False))
    if options.bundle_audio:
        mux = folder / "dolly-mux-xxxxxxxx"  # tempfile's eight-character name
        entries += [(mux, True), (mux / ("with_audio" + suffix), False)]
    return entries


def _check_take_folder_length(folder: Path, name: str, options: "VideoOptions") -> None:
    """Refuse a take whose folder files would exceed MAX_PATH mid-export."""
    limit = min((MAX_FOLDER_PATH if is_folder else MAX_FILE_PATH) - (len(str(entry)) - len(str(folder)))
                for entry, is_folder in take_folder_entries(folder, name, options))
    if len(str(folder)) > limit:
        raise ValueError(
            f"The take folder path is too long for Windows ({len(str(folder))} characters; "
            f"at most {limit} for this export): {folder}. Choose a shorter filename or "
            "save to a folder closer to the drive root.")


@dataclass(frozen=True)
class VideoOptions:
    path: Path
    fps: int = 60
    bitrate: int = 20_000_000
    codec: str = DEFAULT_CODEC_KEY
    quality: int = 0
    preset: int = 0
    ffmpeg_path: "Path | None" = None
    fixed_step: bool = True
    # Replay slow-motion for fixed-step export (same range as playback speed).
    speed: float = 1.0
    # Paired depth master. When on, the take records into its own folder: the
    # color video plus one subfolder per layer, with the depth layer holding
    # depth.mov, manifest.json and preview.mp4. Off by default; requires a
    # frame-paired game capture with a verified scene sample per frame.
    depth: bool = False
    # Also write the float EXR precision master under the depth layer's exr/.
    # Off by default: the depth.mov is the deliverable, the EXRs are for VFX.
    depth_exr: bool = False
    # Additional isolated layer takes recorded beside the color take, one per
    # selected layer ("world", "players", "effects"). Each layer records its
    # own video in a subfolder of the take; fixed-step keeps them aligned with
    # the color take frame for frame.
    layers: tuple[str, ...] = ()
    # Matte pass for a layer take: force the engine's color clear to white so
    # the black and white passes can be combined into a real alpha channel.
    white_clear: bool = False
    # Capture only frames that carry a replay time (set automatically for
    # depth/layer takes; the white matte pass sets it explicitly).
    shot_only: bool = False
    # Inherited by the extra passes of a depth take so their geometry matches.
    full_resolution: bool = False
    # Group the video and optional audio deliverables in one take folder.
    bundle_audio: bool = False
    # Optional hard native admission cap for bounded checks; 0 is unlimited.
    max_frames: int = 0
    players_scratch: "Path | None" = None
    camera_track: bool = False
    # Output container: "mp4", "mkv" or "mov". Empty keeps the historical
    # codec-derived choice; validated() records the resolved container.
    container: str = ""
    camera_only: bool = False

    def validated(self) -> VideoOptions:
        if type(self.camera_only) is not bool:
            raise ValueError("Camera-only capture must be on or off.")
        if self.camera_only:
            from .media_wire import pack_command
            path = Path(self.path).expanduser().absolute()
            if (self.speed != 1 or self.layers or self.bundle_audio or self.container
                    or self.codec != "auto" or self.ffmpeg_path is not None
                    or not path.parent.is_dir() or path.exists() or path.is_symlink()):
                raise ValueError("Camera-only export requires normal speed, no media/layers, and a new JSON path.")
            pack_command(2, "start_video", path=str(path), fps=self.fps,
                         bitrate=self.bitrate, fixed_step=self.fixed_step,
                         shot_only=self.shot_only, camera_track=self.camera_track,
                         camera_only=True, max_frames=self.max_frames, depth=self.depth,
                         depth_exr=self.depth_exr, white_clear=self.white_clear)
            return replace(self, path=path)
        if type(self.camera_track) is not bool:
            raise ValueError("Camera track must be on or off.")
        if type(self.max_frames) is not int or not 0 <= self.max_frames <= 65535:
            raise ValueError("Video frame limit must be an integer between 0 and 65535.")
        if type(self.fps) is not int or self.fps not in (30, 60, 120, 300, 600):
            raise ValueError("Choose a video frame rate of 30, 60, 120, 300 or 600 FPS.")
        if type(self.bitrate) is not int or self.bitrate not in BITRATE_PRESETS.values():
            raise ValueError("Choose a video bitrate of 10, 20, or 40 Mbps.")
        if self.codec not in CODEC_BY_KEY:
            raise ValueError("Choose a supported video codec.")
        _label, _encoder, codec_id, needs_ffmpeg = CODEC_BY_KEY[self.codec]
        if type(self.quality) is not int or not QUALITY_RANGE[0] <= self.quality <= QUALITY_RANGE[1]:
            raise ValueError("Choose a video quality between 0 and 51.")
        if type(self.preset) is not int or not PRESET_RANGE[0] <= self.preset <= PRESET_RANGE[1]:
            raise ValueError("Choose a supported encoder preset.")
        raw = str(self.path)
        if not raw.strip() or any(ord(char) < 32 for char in raw):
            raise ValueError("Choose an output file without control characters.")
        path = Path(self.path).expanduser().absolute()
        ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
        container = resolve_container(self.codec, self.container, ffmpeg_available=bool(ffmpeg))
        suffix = "." + container
        if path.suffix.lower() != suffix:
            raise ValueError(f"The output filename must end in {suffix}.")
        if not path.parent.is_dir() and not ((self.depth or self.layers)
                                             and path.parent.parent.is_dir()):
            raise ValueError("Choose an existing output folder.")
        if path.exists() or path.is_symlink():
            raise ValueError("That output file already exists. Choose a new filename.")
        if type(self.fixed_step) is not bool:
            raise ValueError("Fixed-step export must be on or off.")
        if type(self.depth) is not bool:
            raise ValueError("Depth export must be on or off.")
        if type(self.depth_exr) is not bool:
            raise ValueError("Depth EXR export must be on or off.")
        if self.depth_exr and not self.depth:
            raise ValueError("Choose the depth master before adding its EXR sequence.")
        if type(self.layers) is not tuple or any(type(name) is not str for name in self.layers):
            raise ValueError("Layer exports must be a tuple of layer names.")
        if len(set(self.layers)) != len(self.layers):
            raise ValueError("Choose each layer export once.")
        unknown = sorted(set(self.layers) - {"world", "players", "effects"})
        if unknown:
            raise ValueError("Unknown layer export: " + ", ".join(unknown))
        if self.layers and not self.fixed_step:
            raise ValueError("Layer exports need Fixed-step export so every take stays aligned.")
        if type(self.white_clear) is not bool:
            raise ValueError("The white matte background must be on or off.")
        if type(self.full_resolution) is not bool:
            raise ValueError("Full-resolution export must be on or off.")
        if type(self.bundle_audio) is not bool:
            raise ValueError("Audio folder export must be on or off.")
        # Fixed-step audio is supported by the engine's wav-only movie recorder:
        # DollyNative splits its engine-frame clock from its WAV clock, so the
        # reference is frame-locked to the slow-motion video.
        if type(self.shot_only) is not bool:
            raise ValueError("Shot-only capture must be on or off.")
        if isinstance(self.speed, bool) or not isinstance(self.speed, (int, float)):
            raise ValueError("Export speed must be a number between 0.05 and 4.")
        speed = float(self.speed)
        if not 0.05 <= speed <= 4:
            raise ValueError("Export speed must be between 0.05 and 4.")
        if needs_ffmpeg and not ffmpeg:
            raise ValueError("Select an ffmpeg.exe for the chosen encoder.")
        # The native writer also creates the file exclusively. This early
        # check gives a useful error; it is not the overwrite safety boundary.
        scratch = self.players_scratch
        if 'players' in self.layers:
            from .player_scratch import checked_directory
            scratch = checked_directory(scratch or path.parent)
        result = VideoOptions(path, self.fps, self.bitrate, self.codec, self.quality, self.preset,
                              ffmpeg, self.fixed_step, speed, self.depth, self.depth_exr,
                              self.layers, self.white_clear, self.shot_only,
                              self.full_resolution or self.depth, self.bundle_audio, self.max_frames, scratch, self.camera_track,
                              container=container)
        if ffmpeg is not None:
            # Reject an encoder the configured FFmpeg does not provide before
            # the replay is reloaded and the native recorder is armed. The
            # bundled LGPL build intentionally omits libx264/libx265, but the
            # encoder list is still offered; failing here leaves the game
            # untouched instead of stopping the FFmpeg pipe on frame one.
            resolved_key = None
            if self.codec == "auto":
                backend_encoder, _backend_codec = resolve_backend(result)
                if backend_encoder == 1:
                    resolved_key, _label = encoder_select.auto_encoder()
            elif CODEC_BY_KEY[self.codec][3]:
                resolved_key = self.codec
            if resolved_key is not None:
                encoder_name = "ffv1" if resolved_key == "lossless" else resolved_key
                available = ffmpeg_encoder_names(ffmpeg)
                if available is not None and encoder_name not in available:
                    raise ValueError(
                        f"The selected encoder '{encoder_name}' is not available in the "
                        "configured FFmpeg build. Choose Auto, a hardware encoder, or the "
                        "built-in writer, or point Dolly at an FFmpeg build that includes it.")
        if self.depth:
            encoder, _codec_id = resolve_backend(result)
            if encoder != 1:
                raise ValueError("The depth master needs an FFmpeg encoder; choose Auto or a "
                                 "hardware encoder, or clear the depth master.")
        if self.layers:
            encoder, _codec_id = resolve_backend(result)
            if encoder != 1 or ffmpeg is None:
                raise ValueError("Layer exports need an FFmpeg encoder for the alpha matte; "
                                 "choose Auto or a hardware encoder.")
        if self.depth or self.layers or self.bundle_audio:
            folder = path.with_suffix("")
            if folder.exists() or folder.is_symlink():
                raise ValueError("A take folder with this name already exists. Choose a new filename.")
            _check_take_folder_length(folder, path.name, result)
        return result


def recording_ready(status: dict) -> bool:
    return bool(status.get("connected") and status.get("game_running")
                and status.get("camera_backend") == "native"
                and status.get("startup_stage") in ("editing_ready", "replay_ready"))


def format_video_status(status: dict) -> str:
    state = str(status.get("state", "idle"))
    if state == "failed":
        return "Recording failed: " + str(status.get("error") or "Native recording stopped unexpectedly.")
    if state in ACTIVE_STATES and status.get("status_unavailable"):
        return str(status.get("message") or "Recorder status unavailable; finish or discard to retry.")
    if state == "idle":
        return "Ready to record."
    if state == "starting":
        return str(status.get("message") or "Starting recorder…")
    if state == "finalizing":
        return "Finishing MP4…"
    if state == "cancelled":
        return "Recording discarded."
    if state not in ("recording", "completed"):
        return "Video recorder unavailable."
    if state == 'recording' and status.get('capture_waiting') in ('storage', 'gpu', 'finishing'):
        label = {'storage': 'Waiting for storage', 'gpu': 'Waiting for GPU readback',
                 'finishing': 'Finishing Players capture'}[status['capture_waiting']]
        return (f"{label} · {int(status.get('frames_written', 0)):,} / "
                f"{int(status.get('capture_total', 0)):,} frames · {int(status.get('fps', 0))} FPS output")
    duration = max(0.0, float(status.get("duration", 0)))
    frames = max(0, int(status.get("frames_written", 0)))
    dropped = max(0, int(status.get("frames_dropped", status.get("dropped", 0))))
    width, height = int(status.get("width", 0)), int(status.get("height", 0))
    details = [f"{duration:.1f} s", f"{frames:,} frames"]
    if width > 0 and height > 0:
        details.append(f"{width} × {height}")
    if dropped:
        details.append(f"{dropped:,} missed frames")
    codec = str(status.get("codec") or "").strip()
    if codec:
        details.append(codec)
    return ("Recording · " if state == "recording" else "Saved · ") + " · ".join(details)


class VideoExport:
    """Small lifecycle adapter; the native encoder owns the output file."""

    def __init__(self, controller):
        self.controller = controller
        self._bridge = None
        self._lock = threading.RLock()
        self._last = {"state": "idle"}
        self._start_pending = False
        self._start_ack = None
        self._capture_only = False
        self._capture_stop_request: str | None = None
        self._capture_cancel_pending = False
        session, demo = getattr(controller, "_session", None), getattr(controller, "_demo", None)
        self._runtime_context = (session, demo) if session is not None and demo is not None else None
        self._depth_options: VideoOptions | None = None
        self._finish_pending = False
        self._finished_status: dict | None = None
        self.output_path: Path | None = None
        # The folder that receives new takes (the take folder's parent when a
        # layered take created one) and the current layered folder, if any.
        self.output_directory: Path | None = None
        self._layer_folder: Path | None = None

    @property
    def finish_pending(self) -> bool:
        """The operation worker still owns output finishing or restoration."""
        timing = getattr(self.controller, "_export_timing", None)
        with self._lock:
            return self._finish_pending or (isinstance(timing, dict) and bool(timing.get("restore_pending")))

    def status(self) -> dict:
        with self._lock:
            bridge = self._bridge
            last = dict(self._last)
            pending, start_ack = self._start_pending, self._start_ack
        if bridge is None:
            if self._capture_only and self.controller.status().get("game_running") is False:
                if not self._same_export_owner():
                    return {**last, "error": "Players capture cleanup is pending because its editing session changed."}
                self._capture_only = False
                self.controller.mark_recording_pending(False)
                self._last = {"state": "failed", "error": "Deadlock closed during player capture."}
                return dict(self._last)
            if self._capture_only and self._same_export_owner():
                report = player_layer.capture_pacing(self.controller.deployment_directory(), self._capture_stop_request)
                if report and report['pid'] == self.controller.game_pid():
                    last.update(capture_waiting=None if report['error'] else report['stage'], frames_written=report['written'],
                                capture_total=report['cap'], scratch_bytes=report['bytes'])
                    if report['error']:
                        last.update(state="failed", error="Players capture failed; finishing GPU cleanup. Incomplete output will not be exported.")
                        with self._lock:
                            self._last = dict(last)
                            self._finish_pending = True
            return last
        try:
            current = dict(bridge.video_status())
            if current.get("state") not in ACTIVE_STATES | TERMINAL_STATES:
                raise RuntimeError("The native recorder returned an unsupported state.")
            if pending:
                if current.get("ack") == start_ack and current.get("state") not in ACTIVE_STATES:
                    # A timed-out start may still execute later. Old idle or
                    # completed telemetry cannot authorize a second start.
                    current = last
                elif current.get("command_error"):
                    current = {**current, "state": "failed", "error": str(current.get("command_message") or "The native recorder rejected the request.")}
                    with self._lock:
                        self._start_pending = False
                else:
                    with self._lock:
                        self._start_pending = False
            elif (current.get("command_error") and last.get("state") == "failed"
                  and current.get("ack") == last.get("ack")):
                current = last
            if (current.get("state") in ACTIVE_STATES
                    and self.controller.status().get("game_running") is False):
                with self._lock:
                    self._start_pending = False
                raise RuntimeError("Deadlock closed before MP4 finalization was confirmed. The recording may be incomplete.")
        except (RuntimeError, ValueError, OSError) as exc:
            with self._lock:
                uncertain = self._start_pending
            if uncertain:
                current = {**last, "state": "starting", "error": str(exc),
                           "message": "Recorder status unavailable. Finish or discard before recording again."}
            elif last.get("state") in ACTIVE_STATES:
                try:
                    exited = self.controller.status().get("game_running") is False
                except (RuntimeError, ValueError, OSError):
                    exited = False  # Unreadable ownership is not native completion.
                current = {**last, "state": "failed" if exited else last["state"],
                           "error": str(exc), "status_unavailable": not exited,
                           "message": "Recorder status unavailable. Finish or discard before recording again."}
            else:
                return last
        with self._lock:
            if current.get("state") == "completed" and self._finished_status is not None:
                for key in ("depth_preview", "audit"):
                    if key in self._finished_status:
                        current[key] = self._finished_status[key]
            if last.get("codec"):
                current.setdefault("codec", last["codec"])
            self._last = current
            pending = self._start_pending
        if not pending and current.get("state") in TERMINAL_STATES:
            mark = getattr(self.controller, "mark_recording_pending", None)
            if callable(mark):
                mark(False)
        return dict(current)

    def _set_export_timing(self, fps, speed):
        setter = getattr(self.controller, "set_export_timing", None)
        if callable(setter):
            setter(fps, speed)

    def _clear_export_timing(self, *, required=False):
        clear = getattr(self.controller, "clear_export_timing", None)
        if callable(clear):
            try:
                clear()
            except (RuntimeError, ValueError, OSError):
                if required:
                    raise

    def _clear_export_resolution(self):
        clear = getattr(self.controller, "clear_export_resolution", None)
        if callable(clear):
            clear()

    def start(self, options: VideoOptions, project=None, *, frozen=False,
              before_record=None, capture_only=False, pov=False) -> dict:
        if self.status().get("state") in ACTIVE_STATES or self.finish_pending:
            raise RuntimeError("Finish the current recording before starting another.")
        try:
            return self._start(options, project, frozen=frozen, before_record=before_record,
                               capture_only=capture_only, pov=pov)
        except Exception:
            try:
                self._clear_export_resolution()
            finally:
                if pov:
                    self._clear_export_timing()
                    self.controller.finish_pov_recording()
            raise

    def _resolve_recording_backend(self, options: VideoOptions) -> tuple[int, int, str]:
        """Resolve the encoder for this take and log the automatic choice."""
        encoder, codec_id = resolve_backend(options)
        codec_name = CODEC_BY_KEY[options.codec][0]
        if options.codec != "auto":
            return encoder, codec_id, codec_name
        if encoder == 0:
            return encoder, codec_id, "Auto → Built-in Windows Media Foundation"
        key, _label = encoder_select.auto_encoder()
        resolved_label, encoder, codec_id, _needs = CODEC_BY_KEY[key]
        LOG.info("Auto encoder: %s selected for %s (vendor=%s).", key,
                 encoder_select.describe_adapters(), encoder_select.detect_vendor() or "unknown")
        return encoder, codec_id, f"Auto → {resolved_label}"

    def _start(self, options: VideoOptions, project=None, *, frozen=False,
               before_record=None, capture_only=False, pov=False) -> dict:
        options = options.validated()
        if options.camera_only and (project is None or frozen or capture_only or pov):
            raise ValueError("Camera-only export requires a normal-speed native camera path.")
        if self.status().get("state") in ACTIVE_STATES or self.finish_pending:
            raise RuntimeError("Finish the current recording before starting another.")
        if not recording_ready(self.controller.status()):
            raise RuntimeError("Launch a DirectX 11 replay through Dolly and wait for the native editor before recording.")
        bridge = self.controller._native_bridge()
        if bridge is None:
            raise RuntimeError("The native recorder is not connected.")
        encoder, codec_id, codec_name = (0, 0, "")
        if not capture_only and not options.camera_only:
            encoder, codec_id, codec_name = self._resolve_recording_backend(options)
        if "players" in options.layers:
            # Reject an impossible Players pass before spending time on Color,
            # Depth and World. Recheck measured dimensions/frame count at the
            # actual handoff: this estimate cannot reserve future disk space.
            deployment = self.controller.deployment_directory()
            dimensions = client_size(self.controller.game_pid())
            if deployment is None or dimensions is None or project is None:
                raise RuntimeError("Could not check Players temporary space. Show the game window "
                                   "and choose a camera path before exporting.")
            frames = math.ceil(project.duration * options.fps / options.speed - 1e-6) + 2
            player_layer.check_capture_space(options.players_scratch or deployment, *dimensions, frames)
        prepare = getattr(self.controller, "prepare_native_recording", None)
        self._pov = bool(pov)
        if pov:
            self.controller.prepare_pov_recording(project)
        elif callable(prepare):
            prepare(project, frozen=frozen)
        session, demo = getattr(self.controller, "_session", None), getattr(self.controller, "_demo", None)
        self._runtime_context = (session, demo) if session is not None and demo is not None else None
        if options.full_resolution:
            self.controller.set_export_resolution()
        # Read the recorder's acknowledgement before this take creates its
        # folder or applies fixed-step timing, so a failed read leaves neither.
        initial_ack = None if capture_only else bridge.video_status().get("ack")
        # Layered and audio takes keep their video and sidecars in one folder.
        folder = options.path.with_suffix("") if (options.depth or options.layers or options.bundle_audio) else None
        color_path = options.path
        if folder is not None:
            try:
                folder.mkdir()
            except FileExistsError as exc:
                raise RuntimeError("A take folder with this name already exists. Choose a new filename.") from exc
            except OSError as exc:
                raise RuntimeError("The recording folder could not be created: " + str(exc)) from exc
            color_path = folder / options.path.name
        if options.fixed_step:
            self._set_export_timing(options.fps, options.speed)
        try:
            if before_record is not None:
                before_record()
        except Exception:
            self._clear_export_timing()
            if folder is not None:
                try:
                    folder.rmdir()
                except OSError:
                    pass
            raise
        if capture_only:
            # The arm callback created this request. Pin it now, before a later
            # marker replacement could be mistaken for this capture at Stop.
            try:
                capture_request = player_layer.capture_request_id(
                    self.controller.deployment_directory())
            except (RuntimeError, ValueError, OSError, TypeError):
                capture_request = None
            with self._lock:
                self._finish_pending = False
                self._finished_status = None
                self._capture_only = True
                self._capture_stop_request = capture_request
                self._capture_cancel_pending = False
                self._bridge = None
                self._last = {"state": "recording", "fps": options.fps}
                self.output_path = options.path.with_suffix(".mov")
                self.output_directory = options.path.parent
                self._layer_folder = None
            self.controller.mark_recording_pending(True, player_layer=True)
            return self.status()
        self._capture_only = False
        with self._lock:
            self._bridge = bridge
            self._last = {"state": "starting", "codec": codec_name}
            self.output_path = color_path
            self.output_directory = folder.parent if folder is not None else color_path.parent
            self._layer_folder = folder
            self._start_pending = True
            self._start_ack = initial_ack
            self._depth_options = options if options.depth else None
            self._finish_pending = True
            self._finished_status = None
        mark = getattr(self.controller, "mark_recording_pending", None)
        if callable(mark):
            mark(True)
        try:
            limited = {"max_frames": options.max_frames} if options.max_frames else {}
            if options.camera_track:
                limited["camera_track"] = True
            if options.camera_only:
                limited["camera_only"] = True
            bridge.start_video(str(color_path), fps=options.fps, bitrate=options.bitrate,
                               encoder=encoder, codec=codec_id, quality=options.quality,
                               preset=options.preset,
                               ffmpeg_path=str(options.ffmpeg_path) if encoder == 1 and options.ffmpeg_path else "",
                               fixed_step=options.fixed_step, depth=options.depth,
                               depth_exr=options.depth_exr,
                               shot_only=bool(options.depth or options.layers or options.shot_only
                                              or (project is not None and not frozen)),
                               white_clear=options.white_clear, **limited)
        except Exception as exc:
            if folder is not None:
                try:
                    folder.rmdir()
                except OSError:
                    pass
            self._clear_export_timing()
            with self._lock:
                self._last = {"state": "starting", "error": str(exc),
                              "message": "Start not confirmed. Finish or discard before recording again."}
            raise
        with self._lock:
            self._start_pending = False
        return self.status()

    def _same_export_owner(self) -> bool:
        context = self._runtime_context
        return (context is not None and getattr(self.controller, "_session", None) is context[0]
                and getattr(self.controller, "_demo", None) == context[1])

    def _require_export_context(self, *, capture_readback: bool = False) -> None:
        """Never restore this take's runtime values into another session/replay."""
        if not self._same_export_owner():
            raise RuntimeError("The recording's editing session or selected replay changed; its runtime restoration is pending.")
        if self.controller.status().get("game_running") is False:
            return  # The same originating game exited; its restore methods do no writes.
        if capture_readback:
            # The renderer may be draining Players writes and unable to answer
            # netcon. Validate the same owned replay through its native readback
            # before the file stop request; use console checks after draining.
            bridge = self.controller._native_bridge()
            if bridge is None:
                raise RuntimeError("Players capture stop is pending: the native bridge is unavailable.")
            self.controller._require_native_demo(bridge.status(), allow_idle=True)
        else:
            self.controller._require_demo(require_tick=False)
        if not self._same_export_owner():
            raise RuntimeError("The recording's editing session or selected replay changed during verification; restoration is pending.")
        if self._capture_only or self._capture_stop_request is not None:
            self._require_player_stop_marker(self.controller.deployment_directory())

    def _require_player_stop_marker(self, deployment) -> Path:
        pending = "Players capture stop is pending: "
        if deployment is None:
            raise RuntimeError(pending + "the capture directory is unavailable while Deadlock is running.")
        if self._capture_stop_request is None:
            raise RuntimeError(pending + "the request identity was not confirmed when it started.")
        marker = Path(deployment) / player_layer.MARKER_NAME
        if marker.is_file():
            if self._capture_stop_request != player_layer.capture_request_id(deployment):
                raise RuntimeError(pending + "the request changed; no stop or restoration was sent to the new capture.")
        return marker

    def _request_player_stop(self, deployment) -> None:
        marker = self._require_player_stop_marker(deployment)
        if marker.is_file():
            player_layer.request_stop(deployment)

    def _confirm_player_stop(self, timeout: float, *, cancel: bool) -> str | None:
        """Keep capture ownership until this exact request finishes draining."""
        from . import player_layer

        self._capture_cancel_pending = self._capture_cancel_pending or cancel
        with self._lock:
            self._finish_pending = True

        def game_exited():
            try:
                return self.controller.status().get("game_running") is False
            except (RuntimeError, ValueError, OSError):
                return False

        self._require_export_context(capture_readback=True)
        if game_exited():
            return
        try:
            deployment = self.controller.deployment_directory()
            self._request_player_stop(deployment)
            status = player_layer.wait_for_capture(
                deployment, timeout=max(0.0, min(float(timeout), 5.0 if cancel else 120.0)),
                request_id=self._capture_stop_request)
            terminal = status.startswith(("complete", "failed"))
            if status.startswith("rejected"):
                # A local scratch-ack validation error can also say rejected
                # while native capture is still armed. Only a native rejection
                # stamped with this request can acknowledge shutdown.
                terminal = "request " + self._capture_stop_request in status.splitlines()
            if not terminal:
                raise RuntimeError("The native capture has not acknowledged stop" +
                                   (": " + status if status else "."))
            self._require_export_context(capture_readback=True)
            return status
        except (RuntimeError, ValueError, OSError) as exc:
            if game_exited():
                return
            raise RuntimeError("Players capture stop is pending: " + str(exc) +
                               " Raw capture data is kept. Finish or discard again to retry; "
                               "a new capture is blocked until cleanup is confirmed.") from exc

    def _wait_player_restore_context(self) -> None:
        """After stop acknowledgement, let a busy console recover before writes.

        Keep the reservation and originals across at most three read attempts.
        Each attempt first verifies native ownership and the pinned request;
        every non-timeout error remains an immediate refusal. Existing console
        query timeouts bound each attempt, on the operation worker, not Tk.
        """
        for attempt in range(3):
            self._require_export_context(capture_readback=True)
            try:
                self._require_export_context()
                return
            except ConsoleTimeout as exc:
                last_timeout = exc
                if attempt < 2:
                    time.sleep(.1)
        raise RuntimeError(
            "Players capture acknowledged stop, but replay verification is still unavailable: "
            + str(last_timeout) + " Raw capture data and restoration settings are kept. "
            "Use Finish or discard again after Deadlock responds.") from last_timeout

    def stop(self, *, cancel: bool = False, timeout: float = 120.0) -> dict:
        """Request shutdown and wait on the operation worker, never on Tk.

        The native encoder itself waits up to 120 s for FFmpeg to finalize a
        large take before it reports a terminal state, so the desktop wait must
        not give up sooner: a shorter window reported "still finishing" on big
        or drop-heavy takes while finalization was still progressing normally.
        """
        stop_error = None
        try:
            with self._lock:
                bridge = self._bridge
            if self._capture_only:
                capture_cancel = cancel or self._capture_cancel_pending
                capture_status = self._confirm_player_stop(timeout, cancel=capture_cancel)
                self._wait_player_restore_context()
                with self._lock:
                    self._capture_only = False
                    failure = (capture_status if capture_status and capture_status.startswith(("failed", "rejected"))
                               else self._last.get("error") if self._last.get("state") == "failed" else None)
                    self._last = ({"state": "failed", "error": failure} if failure else
                                  {"state": "cancelled" if capture_cancel else "completed"})
                    self._capture_cancel_pending = False
                self.controller.mark_recording_pending(False)
                self._require_export_context()
                current = self.controller.status()
                if current.get("game_running") is True and current.get("playing") is False:
                    restoration = self.controller._finish_native_playback()
                    if restoration:
                        raise RuntimeError(restoration)
                return self.status()
            current = self.status()
            if bridge is None:
                return current
            if current.get("state") not in ACTIVE_STATES:
                return self._finish_terminal(current, cancel=cancel)
            bridge.stop_video(cancel=cancel)
            with self._lock:
                self._start_pending = False
            deadline = time.monotonic() + timeout
            while True:
                current = self.status()
                if current.get("state") not in ACTIVE_STATES:
                    if current.get("state") == "failed":
                        self._depth_options = None
                        self._discard_empty_layer_folder()
                        raise RuntimeError(str(current.get("error") or "MP4 finalization failed."))
                    return self._finish_terminal(current, cancel=cancel)
                if time.monotonic() >= deadline:
                    raise RuntimeError("The video recorder is still finishing. Wait for its status before starting another recording or closing the game.")
                time.sleep(0.05)
        except Exception as exc:
            stop_error = exc
            raise
        finally:
            # An unacknowledged capture still owns timing, size and camera.
            # Keep originals and reservation for retry, including normal Finish.
            with self._lock:
                unconfirmed = (self._capture_only or self._start_pending
                               or self._last.get("state") in ACTIVE_STATES)
            restorations = [] if unconfirmed else [
                ("timing", lambda: self._clear_export_timing(required=True),
                 lambda: getattr(self.controller, "_export_timing", None) is not None),
                ("render scale", self._clear_export_resolution,
                 lambda: getattr(self.controller, "_export_resolution", None) is not None),
            ]
            if not unconfirmed and getattr(self, "_pov", False):
                restorations.append(("POV", self.controller.finish_pov_recording,
                                     lambda: getattr(self.controller, "_pov_active", False)))
            restoration_errors = []
            for label, restore, pending in restorations:
                try:
                    if pending():
                        self._require_export_context()
                    restore()
                except Exception as exc:
                    restoration_errors.append(label + ": " + str(exc))
            if restoration_errors:
                with self._lock:
                    self._finish_pending = True
                message = "Export restoration remains pending: " + " ".join(restoration_errors)
                if stop_error is not None:
                    message = str(stop_error) + " " + message
                raise RuntimeError(message) from stop_error
            with self._lock:
                if not self._capture_only and not self._start_pending and self._last.get("state") in TERMINAL_STATES:
                    self._capture_stop_request = None
                    self._finish_pending = False

    def _finish_terminal(self, status: dict, *, cancel: bool) -> dict:
        # A native frame cap or shutdown can finish before Stop is requested.
        # Run desktop finishing on the worker in that case too, exactly once.
        if status.get("state") == "completed" and not cancel:
            with self._lock:
                finished = self._finished_status
            if finished is not None:
                return {**status, **finished}
            self._finish_layered_sidecar()
            status = self._encode_depth_preview(status)
            status = self._audit_take(status)
            with self._lock:
                self._finished_status = dict(status)
        else:
            self._depth_options = None
            if cancel or status.get("state") in ("cancelled", "failed"):
                self._discard_empty_layer_folder()
        return status

    def _audit_take(self, status: dict) -> dict:
        """Attach a read-only take-folder validation report to the status.

        Diagnostics only: findings are logged for support and surfaced by the
        UI, but a validator problem can never fail a finished recording.
        """
        folder = self._layer_folder
        if folder is None:
            return status
        from .export_audit import audit_take
        try:
            if not folder.is_dir():
                return status
            report = audit_take(folder)
        except Exception:  # noqa: BLE001 - diagnostics must never fail an export
            LOG.exception("Take audit could not run")
            return status
        for finding in report["findings"]:
            LOG.warning("Take audit finding: %s", finding)
        result = dict(status)
        result["audit"] = report
        return result

    def _discard_empty_layer_folder(self):
        """Remove only a take folder this session created and left empty."""
        with self._lock:
            folder = self._layer_folder
        if folder is None:
            return
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass

    def _finish_layered_sidecar(self):
        """Move the native sidecar next to the color video's take-folder root."""
        with self._lock:
            output = self.output_path
            folder = self._layer_folder
        if output is None or folder is None:
            return
        sidecar = Path(str(output) + ".shot.json")
        try:
            if sidecar.is_file() and not (folder / "shot.json").exists():
                os.replace(sidecar, folder / "shot.json")
        except OSError as exc:
            LOG.warning("Shot metadata could not be moved into the take folder: %s", exc)

    def _encode_depth_preview(self, status: dict) -> dict:
        """Encode the depth layer's normalized raw stream into a preview video.

        The depth.mov is the master and the optional EXR sequence is the float
        precision master. The preview is best-effort: a missing encoder or
        failure only logs, and the raw stream is kept for diagnosis.
        """
        with self._lock:
            options, self._depth_options = self._depth_options, None
            output = self.output_path
        if options is None or output is None:
            return status
        directory = Path(str(output)).parent / "depth"
        raw = None
        try:
            for candidate in directory.iterdir():
                match = re.fullmatch(r"preview_(\d+)x(\d+)\.raw", candidate.name)
                if match:
                    raw = (candidate, int(match.group(1)), int(match.group(2)))
                    break
        except OSError:
            return status
        if raw is None:
            return status
        source, width, height = raw
        ffmpeg = resolve_ffmpeg(options.ffmpeg_path) or bundled_ffmpeg_path()
        if ffmpeg is None:
            LOG.warning("Depth preview skipped: ffmpeg is unavailable; depth.mov and any EXR sequence are complete.")
            return status
        encoder, codec_id = resolve_backend(options)
        if encoder == 0:
            codec_id = 3  # Media Foundation built-in: encode the preview via ffmpeg MF.
        codec = PREVIEW_CODECS.get(codec_id, "h264_mf")
        suffix = ".mkv" if codec == "ffv1" else ".mp4"
        target = directory / ("preview" + suffix)
        args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pixel_format", "gray",
                "-video_size", f"{width}x{height}", "-framerate", str(options.fps),
                "-i", str(source), "-c:v", codec]
        if codec != "ffv1":
            args += ["-pix_fmt", "yuv420p", "-b:v", str(options.bitrate)]
        args.append(str(target))
        try:
            frames = max(1, source.stat().st_size // max(1, width * height))
            with external_program_environment() as environment:
                # A failed or killed encoder leaves no empty or truncated preview.
                result = run_ffmpeg(args, timeout=scaled_timeout(120.0, 0.5, frames),
                                    env=environment, partial=target)
            encoded = result.returncode == 0 and target.is_file()
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.warning("Depth preview could not be encoded: %s", exc)
            remove_partial(target)
            return status
        if not encoded:
            LOG.warning("Depth preview encoder failed: %s", last_line(result))
            return status
        try:
            source.unlink()
        except OSError:
            LOG.warning("Depth preview encoded but its raw stream could not be removed: %s", source)
        LOG.info("Depth preview saved to %s", target)
        return {**status, "depth_preview": str(target)}
