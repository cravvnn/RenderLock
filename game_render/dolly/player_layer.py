"""Players-only layer capture.

The recorder's isolated layer takes separate scene classes with
``sc_setclassflags``: keeping ``SkinnedObject`` keeps every skinned unit,
including NPCs, and hiding everything else drops player-owned parts that live
in other classes (weapons, attachments, carried objects). The native player
capture instead selects the exact draws owned by player pawns -- classified
through the scene graph (scene object -> scene node -> owner pawn) -- and
records them with real coverage alpha, so the layer contains real players plus
their equipment and nothing else. The world stays in the scene, so scenery
occlusion is preserved.

This module owns the file protocol with the native capture (marker, status,
bundle and per-frame metadata) and the diagnostic preview/encode path that
matches the verified research proofs.
"""
from __future__ import annotations

import os
import json
import math
import re
import struct
import shutil
import subprocess
import time
import zlib
import uuid
import tempfile
import threading
from pathlib import Path

from .ffmpeg_color import REC709_FILTER, REC709_OUTPUT_ARGS
from .runtime import external_program_environment

CAPTURE_MODE = "color-sequence-v3"
OWNER_LAYOUT_CLASS = "CGameSceneNode"
OWNER_FIELD_NAME = "m_pOwner"
PLAYER_CLASS_NAME = "CitadelPlayerPawn"
ENTITY_LAYOUT_CLASS = "C_BaseEntity"
ENTITY_SCENE_NODE_FIELD = "m_pGameSceneNode"
SCENE_NODE_FIELD_OFFSET = 816
MARKER_NAME = "dolly_owner_start.txt"
STATUS_NAME = "dolly_owner_status.txt"
PACING_NAME = "dolly_owner_pacing.txt"
STOP_NAME = "dolly_owner_stop.txt"
BUNDLE_NAME = "dolly_owner_color_frame.bin"
META_NAME = "dolly_owner_frame_meta.bin"
BUNDLE_MAGIC = 0x314641524c4f4344
MIN_FRAMES, MAX_FRAMES = 2, 2_000_000
# The native capture supports 600 FPS output. Fixed-step divides that output
# clock by Export speed, so 120 FPS at 0.1x asks the engine for 1200 frames/s
# and cannot keep its shot-frame cadence. Keep the hard preflight at the
# capture's supported ceiling; the status message still explains slower cases.
MAX_ENGINE_FPS = 600.0
# Keep small capture reports through deployment cleanup. Never include image
# bundles, raw process/GPU events, or arbitrary files from the game directory.
DIAGNOSTIC_NAMES = (STATUS_NAME, MARKER_NAME, PACING_NAME, "dolly_owner_stall.txt",
                    "dolly_owner_draws.txt", "dolly_owner_captures.txt",
                    "dolly_owner_producers.txt", "dolly_owner_frame_stats.txt",
                    "dolly_minimap_guard.txt")
DIAGNOSTIC_LIMIT = 64 * 1024


def preserve_diagnostics(deployment: Path, session: Path) -> None:
    """Best-effort bounded reports; failure must never block game cleanup."""
    for name in DIAGNOSTIC_NAMES:
        try:
            with (Path(deployment) / name).open("rb") as source:
                data = source.read(DIAGNOSTIC_LIMIT)
            destination = Path(session) / name
            temporary = destination.with_name(name + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
        except OSError:
            pass


# The owner offset is read from the game's schema each run, so it may be small
# or move after a game update; only an implausible value is refused.
_MIN_OFFSET, _MAX_OFFSET = 8, 0x4000

_OWNER_FIELD = re.compile(r"^\s*(\+?)\s*(\d+)\s+(\d+)\s+(\S+)\s+(m_\S+)\s+(.+?)\s*$", re.M)


def _field_offset(layout_text: str, field: str) -> int:
    for plus, outer, offset, _kind, name, _type in _OWNER_FIELD.findall(layout_text):
        if not plus and int(outer) == 0 and name == field:
            value = int(offset)
            if _MIN_OFFSET <= value <= _MAX_OFFSET:
                return value
            raise RuntimeError("%s is outside the reviewed offset range." % field)
    raise RuntimeError("The game did not report %s; the player layer is unavailable." % field)


def parse_owner_offset(layout_text: str) -> int:
    """Return CGameSceneNode.m_pOwner's byte offset from schema console text.

    The offset is read from the game itself (``schema_detailed_class_layout
    CGameSceneNode``) so the capture never hardcodes a schema layout.
    """
    return _field_offset(layout_text, OWNER_FIELD_NAME)


def parse_scene_node_offset(layout_text: str) -> int:
    """Return C_BaseEntity.m_pGameSceneNode's byte offset (back-link check)."""
    return _field_offset(layout_text, ENTITY_SCENE_NODE_FIELD)


def marker_text(owner_offset: int, frames: int, back_offset: int = SCENE_NODE_FIELD_OFFSET,
                *, fps: int = 60, request_id: str = "", speed: float = 1.0) -> str:
    """Marker for a players-only capture: no fixed handles, class-discovered."""
    for value in (owner_offset, back_offset):
        if not _MIN_OFFSET <= value <= _MAX_OFFSET:
            raise ValueError("A schema offset is outside the reviewed range.")
    if not MIN_FRAMES <= frames <= MAX_FRAMES:
        raise ValueError("Player layer capture needs 2..%d frames." % MAX_FRAMES)
    if fps not in (30, 60, 120, 300, 600):
        raise ValueError("Unsupported player layer FPS.")
    if not math.isfinite(speed) or not .05 <= speed <= 4:
        raise ValueError("Player layer export speed must be between 0.05 and 4.")
    if not request_id and (fps != 60 or speed != 1):
        raise ValueError("A custom capture clock requires a capture request ID.")
    if request_id and not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("Invalid capture request ID.")
    suffix = (" fps %d request %s" % (fps, request_id)) if request_id else ""
    if speed != 1:
        suffix += " speed %.9g" % speed
    return "%s 0 %d players %d %d%s\n" % (CAPTURE_MODE, frames, owner_offset, back_offset, suffix)


def reference_frame_count(color_path: Path, fps: int) -> int:
    """Require the completed color take's measured length before arming Players."""
    color_path = Path(color_path)
    sidecar = color_path.parent / "shot.json"
    try:
        if not color_path.is_file() or not 0 < sidecar.stat().st_size <= 40 * 1024 * 1024:
            raise ValueError("missing color take or invalid metadata size")
        # Match the movie-audio reader's metadata budget, including frame rows.
        # Bound the actual read too: a file can grow after stat().
        with sidecar.open("rb") as source:
            payload = source.read(40 * 1024 * 1024 + 1)
        if len(payload) > 40 * 1024 * 1024:
            raise ValueError("color metadata exceeds the read budget")
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid metadata")
        frames = data.get("frames_written")
        if (data.get("format") != "deadlock-dolly-shot" or data.get("version") != 1
                or data.get("video_file") != color_path.name or data.get("fps") != fps
                or data.get("fixed_step") is not True or type(frames) is not int
                or not MIN_FRAMES <= frames <= MAX_FRAMES
                or data.get("first_frame") != 0 or data.get("last_frame") != frames - 1):
            raise ValueError("color take metadata does not match this export")
        return frames
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise RuntimeError("The players layer needs the completed color take's frame metadata. "
                           "Record a new color take before exporting Players.") from exc


def validate_engine_rate(fps: int, speed: float) -> float:
    """Refuse a Players fixed-step clock the native capture cannot sustain."""
    if type(fps) is not int or fps not in (30, 60, 120, 300, 600):
        raise RuntimeError("The Players layer needs a supported video FPS.")
    if not math.isfinite(speed) or not .05 <= speed <= 4:
        raise RuntimeError("The Players layer needs a valid export speed.")
    rate = fps / speed
    if rate > MAX_ENGINE_FPS:
        raise RuntimeError(
            "The Players layer cannot run this fixed-step clock: %d FPS at %.2gx asks "
            "the engine for %.0f frames/s (limit %.0f). Lower Video FPS or raise Export "
            "speed closer to 1x, then record a new color take with the new settings."
            % (fps, speed, rate, MAX_ENGINE_FPS))
    return rate


def check_capture_space(deployment: Path, width: int, height: int, frames: int) -> int:
    """Preflight the known raw bundle size on the game deployment's volume."""
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 16384 or not 1 <= height <= 16384 or
            type(frames) is not int or not MIN_FRAMES <= frames <= MAX_FRAMES):
        raise RuntimeError("The Players layer needs the completed color take's capture dimensions.")
    required = frames * (64 + width * height * 8)
    # Keep room for diagnostics and the filesystem; output MOV may be elsewhere.
    reserve = 1024 ** 3
    free = shutil.disk_usage(deployment).free
    if free < required + reserve:
        shortfall = required + reserve - free
        raise RuntimeError(
            "Players layer capture needs about %.1f GiB of raw temporary space on the scratch drive "
            "(plus 1 GiB free); only %.1f GiB is available, so it is short by about %.1f GiB. "
            "The Players layer stores every frame as an uncompressed image during capture. "
            "Free space on that drive, shorten the camera path, or lower the FPS or resolution, "
            "then try again. The final MOV also needs space on its output drive."
            % (required / 1024 ** 3, free / 1024 ** 3, shortfall / 1024 ** 3))
    return required


def begin_capture(deployment: Path, owner_offset: int, frames: int,
                  back_offset: int = SCENE_NODE_FIELD_OFFSET, *, fps: int = 60, speed: float = 1.0,
                  scratch_root=None, output=None) -> Path:
    """Write the capture marker beside the deployed native DLL."""
    marker = Path(deployment) / MARKER_NAME
    temporary = marker.with_name(marker.name + ".tmp")
    request_id = uuid.uuid4().hex
    text = marker_text(owner_offset, frames, back_offset, fps=fps, request_id=request_id, speed=speed)
    if scratch_root is not None:
        from . import player_scratch
        folder, request_id = player_scratch.create(scratch_root, fps=fps, frames=frames, output=output)
        text = marker_text(owner_offset, frames, back_offset, fps=fps, request_id=request_id, speed=1.0)
        text = text.replace('color-sequence-v3', 'color-sequence-v4').strip()
        text += ' speed %.9g scratch %s\n' % (speed, str(folder).encode('utf-8').hex())
    temporary.write_text(text, encoding="ascii")
    stop = Path(deployment) / STOP_NAME
    try:
        stop.unlink()
    except FileNotFoundError:
        pass
    os.replace(temporary, marker)
    return marker


def request_stop(deployment: Path) -> Path:
    """Ask a running capture to finish now, at the frames it already sealed.

    The take can end before the frame cap (a replay returning to the hideout,
    a stopped recording), so the recorder drops this file and the capture
    drains its captured frames instead of waiting out its budget. Incomplete
    shot captures are reported as failed rather than encoded at a shorter duration.
    """
    stop = Path(deployment) / STOP_NAME
    temporary = stop.with_name(stop.name + ".tmp")
    temporary.write_text("stop\n", encoding="ascii")
    os.replace(temporary, stop)
    return stop


def capture_request_id(deployment: Path) -> str:
    """Pin the capture identity before asking its native owner to stop."""
    text = (Path(deployment) / MARKER_NAME).read_text(encoding="ascii")
    match = re.search(r"\brequest ([0-9a-f]{32})(?=\s|$)", text)
    if not match:
        raise RuntimeError("The Players capture request identity is unavailable.")
    return match[1]


def capture_pacing(deployment: Path, request_id: str | None) -> dict:
    """Read bounded progress for exactly the armed request, never another take."""
    if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
        return {}
    try:
        with (Path(deployment) / PACING_NAME).open('rb') as source:
            raw = source.read(2049)
        if len(raw) > 2048:
            return {}
        data = json.loads(raw)
        if (not isinstance(data, dict) or type(data.get('format')) is not int
                or data['format'] != 1 or data.get('request') != request_id):
            return {}
        if capture_request_id(deployment) != request_id:
            return {}
        fields = ('pid', 'heartbeat', 'progress_ms', 'progress', 'written', 'sealed', 'cap', 'bytes', 'error')
        if any(type(data.get(k)) is not int or not 0 <= data[k] < 2**64 for k in fields):
            return {}
        if (not data['pid'] or not 0 < data['progress_ms'] <= data['heartbeat']
                or not 0 <= data['written'] <= data['sealed'] <= data['cap'] <= MAX_FRAMES
                or data['cap'] < MIN_FRAMES or data['error'] not in (0, 1)
                or data.get('stage') not in ('capture', 'storage', 'gpu', 'finishing', 'complete')):
            return {}
        return data
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


def capture_status(deployment: Path, *, request_id: str | None = None) -> str:
    path = Path(deployment) / STATUS_NAME
    if not path.is_file():
        return ""
    text = path.read_text(encoding="ascii", errors="replace").strip()
    if request_id is not None and "request " + request_id not in text.splitlines():
        return ""  # A removed/replaced marker cannot authorize another take's status.
    marker = Path(deployment) / MARKER_NAME
    if marker.is_file():
        match = re.search(r" request ([0-9a-f]{32})", marker.read_text(encoding="ascii"))
        if match and "request " + match[1] not in text.splitlines():
            return ""  # Never accept another take's status/bundle.
    if marker.is_file() and marker.read_text(encoding="ascii").startswith('color-sequence-v4 '):
        requested = re.search(r' scratch ([0-9a-f]+)', marker.read_text(encoding="ascii"))
        if text.startswith(('armed', 'complete')) and (not requested or 'scratch ' + requested[1] not in text.splitlines()):
            return 'rejected scratch directory was not acknowledged by the native capture'
    return text


def wait_for_capture(deployment: Path, *, timeout: float = 240.0,
                     request_id: str | None = None) -> str:
    """Bounded wait for the native capture to finish; never blocks forever."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = capture_status(deployment, request_id=request_id)
        if status.startswith(("complete", "failed", "rejected")):
            return status
        time.sleep(0.2)
    return capture_status(deployment, request_id=request_id)


def iter_frames(bundle: Path):
    """Yield (width, height, premultiplied RGBA half-float bytes) per frame."""
    with Path(bundle).open("rb") as source:
        while True:
            header = source.read(64)
            if not header:
                return
            if len(header) != 64:
                raise RuntimeError("The player layer bundle is truncated.")
            magic, width, height, draws, first, last, _, bpp = struct.unpack("<8Q", header)
            if (magic != BUNDLE_MAGIC or not 0 < width <= 8192 or not 0 < height <= 8192
                    or (not draws and (first or last)) or bpp != 8):
                raise RuntimeError("The player layer bundle is not a reviewed capture.")
            size = width * height * 8
            pixels = source.read(size)
            if len(pixels) != size:
                raise RuntimeError("The player layer bundle is truncated.")
            if not draws and any(pixels):
                raise RuntimeError("The player layer's empty scene frame contains unexpected pixels.")
            yield width, height, pixels


def _preview_channel(value: float) -> int:
    value = max(0.0, value)
    value = value / (1 + value)
    value = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    return round(max(0.0, min(1.0, value)) * 255)


def write_previews(bundle: Path, output: Path) -> list[Path]:
    """Diagnostic sRGB previews with coverage alpha, matching the proofs.

    The native pixels are premultiplied half-float HDR; the preview path
    unpremultiplies, tone maps (Reinhard) and writes straight-alpha PNGs for
    the encoder. A product tonemapping decision is tracked separately.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, (width, height, pixels) in enumerate(iter_frames(bundle)):
        preview = bytearray(width * height * 4)
        for i, rgba in enumerate(struct.iter_unpack("<4e", pixels)):
            alpha = rgba[3]
            if alpha:
                preview[i * 4:i * 4 + 4] = bytes(
                    [_preview_channel(rgba[0] / alpha), _preview_channel(rgba[1] / alpha),
                     _preview_channel(rgba[2] / alpha), round(alpha * 255)])
        name = output / ("native-player-layer-%03d.png" % index)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data +
                    struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        png = (b"\x89PNG\r\n\x1a\n" +
               chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress(b"".join(
                   b"\0" + bytes(preview[y * width * 4:(y + 1) * width * 4]) for y in range(height)))) +
               chunk(b"IEND", b""))
        name.write_bytes(png)
        paths.append(name)
    return paths


def encode_layer(ffmpeg: Path, previews: list[Path], output: Path, *, fps: int = 60) -> Path:
    """Encode preview frames to the alpha-capable mezzanine layer video."""
    if not previews:
        raise RuntimeError("The player layer captured no frames.")
    if not Path(ffmpeg).is_file():
        raise RuntimeError("The player layer needs an FFmpeg executable for its alpha video.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite on purpose: a re-recorded take must replace its layer master
    # instead of silently keeping an older file under the same name.
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
               "-framerate", str(fps), "-start_number", "0",
               "-i", str(previews[0].with_name(previews[0].name.replace("000", "%03d"))),
               "-frames:v", str(len(previews)), "-an", "-c:v", "prores_ks", "-profile:v", "4444",
               "-vf", REC709_FILTER, "-pix_fmt", "yuva444p10le", *REC709_OUTPUT_ARGS,
               "-vendor", "apl0", "-threads", "2", str(output)]
    with external_program_environment() as environment:
        result = subprocess.run(command, capture_output=True, timeout=max(120, 30 * len(previews)),
                                env=environment,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError("Could not encode the player layer video: " +
                           result.stderr.decode(errors="replace")[:400])
    return output


def wait_until_armed(deployment: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = capture_status(deployment)
        if text.startswith("armed"):
            return
        if text.startswith("rejected exact module/layout gate"):
            raise RuntimeError(
                "The Players layer does not support this Deadlock build yet. "
                "Update Dolly, or record without the Players layer.")
        if text.startswith(("failed", "rejected", "complete")):
            raise RuntimeError("Player capture could not start: " + text.splitlines()[0])
        time.sleep(.05)
    raise RuntimeError("Player capture did not acknowledge this take. Use a matching updated Dolly build.")


def encode_bundle(ffmpeg: Path, bundle: Path, output: Path, *, fps: int = 60,
                  resource_mode="Balanced", cancelled=lambda: False, progress=lambda count: None,
                  partial_path=None) -> Path:
    """Stream one frame at a time to an atomic alpha MOV; no PNG/MP4 intermediates."""
    if fps not in (30, 60, 120, 300, 600):
        raise ValueError("Unsupported player layer FPS.")
    from .player_encode import resource_policy
    from .player_pixels import convert_rgba
    threads, priority = resource_policy(resource_mode)
    frames = iter(iter_frames(bundle))
    first = next(frames, None)
    if first is None:
        raise RuntimeError("The player layer captured no frames.")
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(partial_path) if partial_path else output.with_name(output.stem + "." + uuid.uuid4().hex + ".partial.mov")
    width, height, _ = first
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-threads", str(threads),
               "-filter_threads", str(threads), "-filter_complex_threads", str(threads),
               "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size", f"{width}x{height}",
               "-framerate", str(fps), "-i", "pipe:0", "-an", "-c:v", "prores_ks",
               "-profile:v", "4444", "-vf", REC709_FILTER, "-pix_fmt", "yuva444p10le",
               *REC709_OUTPUT_ARGS, "-vendor", "apl0",
               "-threads", str(threads), str(temporary)]
    import itertools
    process = None; timer = None
    monitor_stop = threading.Event()
    monitor = None
    try:
        with tempfile.TemporaryFile() as errors:
            # FFmpeg is a separate runtime, including when shipped beside Dolly.
            # Do not inherit the frozen editor's DLL search path or open a console
            # window whose Close button can terminate the encoder mid-stream.
            with external_program_environment() as environment:
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=errors, env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | priority)
            def watch_cancel():
                while not monitor_stop.wait(.1):
                    if cancelled():
                        if process.poll() is None:
                            try: process.kill()
                            except OSError: pass
                        return
            monitor = threading.Thread(target=watch_cancel, daemon=True)
            try:
                monitor.start()
            except RuntimeError:
                monitor = None
                raise
            estimated_frames = max(1, Path(bundle).stat().st_size // (64 + width * height * 8))
            timer = threading.Timer(max(120, 30 * estimated_frames), process.kill)
            timer.daemon = True; timer.start()
            try:
                for count, (w, h, pixels) in enumerate(itertools.chain((first,), frames), 1):
                    if (w, h) != (width, height):
                        raise RuntimeError("Player layer dimensions changed during capture.")
                    preview = convert_rgba(pixels, cancelled=cancelled)
                    process.stdin.write(preview)
                    progress(count)
                process.stdin.close()
                code = process.wait(timeout=120)
            except (OSError, subprocess.TimeoutExpired) as exc:
                # A Windows pipe write can raise EINVAL when the reader exits.
                # Give the exited reader time to publish its real exit status;
                # killing it first can replace that evidence with our kill code.
                try:
                    code = process.wait(timeout=1)
                    stopped = False
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait()
                    stopped = True
                raise RuntimeError(_encode_failure(errors, code, exc, stopped=stopped)) from exc
            if cancelled():
                raise InterruptedError("Players processing cancelled.")
            if code or not temporary.is_file() or not temporary.stat().st_size:
                raise RuntimeError(_encode_failure(errors, code))
        os.replace(temporary, output)
        return output
    finally:
        monitor_stop.set()
        if monitor: monitor.join(timeout=1)
        if timer: timer.cancel()
        if process and process.poll() is None:
            process.kill(); process.wait()
        if process and process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        frames.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # A locked leftover must not replace the encoding failure.


def _encode_failure(errors, code: int, cause=None, *, stopped: bool = False) -> str:
    errors.seek(0)
    detail = errors.read(1000).decode(errors="replace").strip()
    status = "FFmpeg exit %d (0x%08X)" % (code, code & 0xFFFFFFFF)
    if stopped:
        status += "; stopped after an input/timeout failure"
    if cause is not None:
        status += "; " + str(cause)
    return ("Could not encode the players MOV: " + status + ". "
            + (detail or "FFmpeg produced no error text.")
            + " The existing MOV was not replaced.")


def cleanup_capture(deployment: Path, layer_dir: Path) -> list[str]:
    """Delete only known completed-take intermediates, never folders recursively."""
    from . import player_scratch
    data_dir = player_scratch.from_marker(deployment)
    failures = []
    for parent, names in ((data_dir, (BUNDLE_NAME, META_NAME, "capture-status.txt")),
                          (Path(deployment), ("dolly_owner_events.bin",)),
                          (Path(layer_dir), ("players.mp4", "players.mp4.shot.json",
                                             "players.mkv", "players.mkv.shot.json")),
                          (Path(layer_dir)/"capture", ())):
        if not parent.exists(): continue
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            failures.append(str(parent)); continue
        candidates = [parent / n for n in names]
        if parent.name == "capture":
            candidates = [p for p in parent.iterdir() if re.fullmatch(r"native-player-layer-\d+\.png", p.name)]
        for path in candidates:
            try:
                if path.is_symlink() or path.resolve().parent != parent.resolve():
                    raise OSError("Unexpected capture path")
                path.unlink(missing_ok=True)
            except OSError:
                failures.append(str(path))
        if parent.name == "capture":
            try: parent.rmdir()
            except OSError: pass
    if data_dir != Path(deployment) and not failures:
        try:
            player_scratch.update(data_dir, 'completed')
            # Leave ownership intact if there are unexpected files; never recurse.
            if {p.name for p in data_dir.iterdir()} == {player_scratch.OWNER, player_scratch.MANIFEST}:
                (data_dir/player_scratch.MANIFEST).unlink()
                (data_dir/player_scratch.OWNER).unlink()
                data_dir.rmdir()
        except OSError:
            failures.append(str(data_dir))
    return failures
