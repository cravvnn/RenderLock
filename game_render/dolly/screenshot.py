"""High-resolution stills: one settled frame of color, depth and the players layer.

A still is a very short fixed-step layered take of the current paused view.
The camera holds one pose while the replay creeps forward at a slow
export speed, so temporal anti-aliasing and other history-based effects settle
on frames from one near-frozen moment. One settled frame is then pulled out of
each pass and written as Photoshop-ready files:

- ``plate.png``          the normal game frame (8-bit RGB)
- ``hero_alpha.png``     the players-only coverage matte (16-bit grayscale)
- ``hero_rgba.png``      the plate with that matte as its alpha (16-bit RGBA)
- ``hero_isolated.png``  the players layer rendered alone, Reinhard preview
                         tonemap with straight alpha (8-bit RGBA)
- ``depth.exr``          float scene depth in game units (optional)

Resolution follows the game's backbuffer, exactly like video export. Launch
the game windowed at a larger size (for example ``-windowed -w 7680 -h 4320``)
to render stills larger than the monitor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import secrets
import struct
import zlib

from . import player_layer
from .ffmpeg_run import last_line, run_ffmpeg
from .path import Keyframe, Project
from .runtime import external_program_environment

STILL_FPS = 60
# Stay within the Players capture's 600 Hz engine clock while keeping each
# frame near the captured moment: the replay advances ~1/600 s per frame.
STILL_SPEED = 0.1
STILL_FRAMES = 12
# Late enough for temporal effects to converge, early enough that a take
# ending one frame short still contains it.
SETTLED_INDEX = 8
STILL_DURATION = STILL_FRAMES * STILL_SPEED / STILL_FPS
STILL_CODEC = "lossless"
MAX_DIMENSION = 8192

RENDER_SIZES = (
    ("Game window (no change)", None),
    ("2560 x 1440", (2560, 1440)),
    ("3840 x 2160 (4K)", (3840, 2160)),
    ("5120 x 2880 (5K)", (5120, 2880)),
    ("7680 x 4320 (8K)", (7680, 4320)),
)
RENDER_SIZE_LABELS = tuple(label for label, _size in RENDER_SIZES)

OUTPUT_NAMES = ("plate.png", "hero_alpha.png", "hero_rgba.png", "hero_isolated.png", "depth.exr")


@dataclass
class StillRun:
    """Desktop state for one screenshot while its layered take runs."""
    project: Project
    options: object
    depth: bool

    @property
    def take_folder(self) -> Path:
        return Path(self.options.path).with_suffix("")


def default_still_path(shot_name: str, directory: Path, *, now: datetime | None = None) -> Path:
    """An unused ``.mkv`` color path whose take folder does not exist yet."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(shot_name)).strip(" .")[:80] or "Dolly"
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"Dolly_Still_{name}_{stamp}"
    candidate = Path(directory) / (stem + ".mkv")
    sequence = 2
    while any(p.exists() or p.is_symlink() for p in (candidate, candidate.with_suffix(""))):
        candidate = Path(directory) / f"{stem}_{sequence}.mkv"
        sequence += 1
    return candidate


def still_project(base: Project, key: Keyframe, tick: int) -> Project:
    """A two-key hold of one captured pose, starting at the captured tick.

    Lens and cvar tracks are left out: the still uses the pose and lens that
    were on screen. Visual shot settings (confetti, particles, setup values)
    carry over so the still matches what the shot shows.
    """
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise ValueError("The screenshot needs the paused replay tick.")
    first = Keyframe(**{**key.__dict__, "time": 0.0, "source": "free", "attach": None,
                        "source_blend": 0.0})
    last = Keyframe(**{**first.__dict__, "time": STILL_DURATION})
    project = Project(
        name=(base.name or "Untitled") + " still", keyframes=[first, last],
        interpolation="linear", start_tick=tick, tick_rate=base.tick_rate,
        setup_values=dict(base.setup_values), standard_aspect=base.standard_aspect,
        lens_interpolation="step", confetti_enabled=base.confetti_enabled,
        confetti_spawn_height=base.confetti_spawn_height,
        confetti_despawn_on_ground=base.confetti_despawn_on_ground,
        particles_preset=base.particles_preset, particles_intensity=base.particles_intensity)
    project.validate()
    return project


def apply_render_size(launch_options: str, size: tuple[int, int] | None) -> str:
    """Return launch options that render the game at ``size`` (or drop the size).

    A window larger than the desktop only works windowed, so ``-fullscreen``
    is replaced by ``-windowed -noborder``. Other options are kept verbatim.
    """
    tokens = shlex.split(launch_options or "", posix=True)
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        flag = tokens[index].casefold()
        if flag in ("-w", "-width", "-h", "-height"):
            index += 2
            continue
        if size is not None and flag in ("-fullscreen", "-windowed", "-window", "-sw", "-noborder"):
            index += 1
            continue
        kept.append(tokens[index])
        index += 1
    if size is not None:
        width, height = size
        if not (320 <= width <= MAX_DIMENSION and 200 <= height <= MAX_DIMENSION):
            raise ValueError("Choose a render size up to %d x %d." % (MAX_DIMENSION, MAX_DIMENSION))
        kept += ["-windowed", "-noborder", "-w", str(width), "-h", str(height)]
    return " ".join(kept)


def render_size_label(launch_options: str) -> str:
    """The preset label matching the current launch options, if any."""
    tokens = [t.casefold() for t in shlex.split(launch_options or "", posix=True)]
    size = []
    for flag in (("-w", "-width"), ("-h", "-height")):
        for i, token in enumerate(tokens[:-1]):
            if token in flag and tokens[i + 1].isdigit():
                size.append(int(tokens[i + 1]))
                break
    if len(size) == 2:
        for label, preset in RENDER_SIZES:
            if preset == tuple(size):
                return label
    return RENDER_SIZE_LABELS[0]


def settled_index(take_folder: Path, video_name: str) -> int:
    """Pick the settled frame from the completed color take's metadata."""
    try:
        data = json.loads((Path(take_folder) / "shot.json").read_text(encoding="utf-8"))
        frames = data.get("frames_written") if data.get("video_file") == video_name else None
    except (OSError, ValueError, AttributeError):
        frames = None
    if type(frames) is not int or frames < 1:
        raise RuntimeError("The screenshot's color take has no frame metadata.")
    return min(SETTLED_INDEX, frames - 1)


SHORT_CAPTURE_STATUS = "failed player capture missed shot frames"
# A fixed-step capture can also stop because the scratch drive could not keep
# up. A still only needs its settled frame, so both shorthands share the same
# alignment check.
SHORT_CAPTURE_STATUSES = (
    SHORT_CAPTURE_STATUS,
    "failed players capture could not write to the scratch drive fast enough",
)
FRAME_TIME_TOLERANCE = 1e-6  # seconds of replay time


def players_frame_time(meta: Path, index: int) -> float | None:
    """Replay time the players capture recorded for one sealed frame.

    The metadata is 64-byte records: a header, then (frame index, replay time
    as float64 bits, ...) per sealed frame.
    """
    try:
        data = Path(meta).read_bytes()
    except OSError:
        return None
    for offset in range(64, len(data) - 63, 64):
        frame, bits = struct.unpack_from("<2Q", data, offset)
        if frame == index:
            return struct.unpack("<d", struct.pack("<Q", bits))[0]
    return None


def color_frame_time(take_folder: Path, index: int) -> float | None:
    """Replay time the color take recorded for one frame (shot.json samples)."""
    try:
        data = json.loads((Path(take_folder) / "shot.json").read_text(encoding="utf-8"))
        for sample in data.get("clock_samples", ()):
            if sample.get("frame") == index:
                return float(sample["phase"])
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        pass
    return None


def settled_frame_aligned(meta: Path, take_folder: Path, index: int) -> bool:
    """The players and color passes rendered the settled frame at one replay time.

    A still only needs that frame, so a players pass that ends a frame short of
    the color take is usable when this holds; a shifted capture is not.
    """
    players, color = players_frame_time(meta, index), color_frame_time(take_folder, index)
    return players is not None and color is not None and abs(players - color) <= FRAME_TIME_TOLERANCE


def _png(path: Path, width: int, height: int, depth: int, color: int, rows) -> None:
    """Stream a PNG from an iterable of raw rows (filter byte added here)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    temporary = path.with_name(".dolly-png-" + secrets.token_hex(16) + ".tmp")
    compressor = zlib.compressobj(6)
    stream = temporary.open("xb")
    try:
        with stream as out:
            out.write(b"\x89PNG\r\n\x1a\n")
            out.write(chunk(b"IHDR", struct.pack(">2I5B", width, height, depth, color, 0, 0, 0)))
            pending = bytearray()
            for row in rows:
                pending += compressor.compress(b"\0" + bytes(row))
                if len(pending) >= 1 << 20:
                    out.write(chunk(b"IDAT", bytes(pending)))
                    pending.clear()
            pending += compressor.flush()
            out.write(chunk(b"IDAT", bytes(pending)))
            out.write(chunk(b"IEND", b""))
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # An owned leftover must not hide the PNG write result.


def read_bundle_frame(bundle: Path, index: int) -> tuple[int, int, bytes]:
    """Read one premultiplied RGBA half-float frame from the native bundle."""
    with Path(bundle).open("rb") as source:
        header = source.read(64)
        if len(header) != 64:
            raise RuntimeError("The players capture is empty.")
        magic, width, height, draws, _, _, _, bpp = struct.unpack("<8Q", header)
        if (magic != player_layer.BUNDLE_MAGIC or not 0 < width <= MAX_DIMENSION
                or not 0 < height <= MAX_DIMENSION or bpp != 8):
            raise RuntimeError("The players capture is not a reviewed bundle.")
        size = width * height * 8
        source.seek(index * (64 + size))
        header = source.read(64)
        if len(header) != 64:
            raise RuntimeError("The players capture does not contain the settled frame.")
        frame = struct.unpack("<8Q", header)
        if (frame[:3] != (magic, width, height) or frame[7] != 8
                or (not frame[3] and (frame[4] or frame[5]))):
            raise RuntimeError("The settled players frame is not a reviewed capture.")
        pixels = source.read(size)
        if len(pixels) != size:
            raise RuntimeError("The players capture is truncated.")
        if not frame[3] and any(pixels):
            raise RuntimeError("The players empty scene frame contains unexpected pixels.")
        return width, height, pixels


def write_hero_stills(width: int, height: int, pixels: bytes, folder: Path) -> tuple[Path, Path]:
    """Write the 16-bit coverage matte and the isolated players preview."""
    import math
    folder = Path(folder)
    row_format = struct.Struct("<%de" % (width * 4))
    stride = width * 8
    empty_alpha = bytes(width * 2)
    empty_rgba = bytes(width * 4)
    preview = player_layer._preview_channel

    def rows():
        for y in range(height):
            values = row_format.unpack_from(pixels, y * stride)
            alphas = values[3::4]
            if any(not math.isfinite(a) or not 0 <= a <= 1 for a in alphas):
                raise ValueError("Invalid Players alpha.")
            if not any(alphas):
                yield empty_alpha, empty_rgba
                continue
            matte = bytearray(width * 2)
            color = bytearray(width * 4)
            for x, alpha in enumerate(alphas):
                if alpha <= 0:
                    continue
                alpha = min(alpha, 1.0)
                struct.pack_into(">H", matte, x * 2, round(alpha * 65535))
                r, g, b = values[x * 4:x * 4 + 3]
                if not all(math.isfinite(v) for v in (r, g, b)):
                    raise ValueError("Invalid Players color.")
                color[x * 4:x * 4 + 4] = bytes((preview(r / alpha), preview(g / alpha),
                                                preview(b / alpha), round(alpha * 255)))
            yield matte, color

    # Each pass decodes the rows again rather than holding two 8K images.
    alpha_path = folder / "hero_alpha.png"
    isolated_path = folder / "hero_isolated.png"
    _png(alpha_path, width, height, 16, 0, (matte for matte, _ in rows()))
    _png(isolated_path, width, height, 8, 6, (color for _, color in rows()))
    return alpha_path, isolated_path


def _ffmpeg(ffmpeg: Path, *args: str, timeout: float = 300.0) -> None:
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *args]
    # Same as the players MOV encoder: in the packaged app, don't hand FFmpeg the
    # frozen editor's DLL search path, and don't open a console window for it.
    with external_program_environment() as environment:
        result = run_ffmpeg(command, timeout=timeout, env=environment, partial=Path(args[-1]),
                            timeout_message="FFmpeg timed out")
    if result.returncode != 0:
        raise RuntimeError("FFmpeg failed: " + last_line(result))


def extract_plate(ffmpeg: Path, video: Path, index: int, output: Path) -> Path:
    """Decode one lossless color frame. The backbuffer alpha is not coverage."""
    _ffmpeg(ffmpeg, "-i", str(video), "-vf", "select=eq(n\\,%d)" % index, "-vsync", "0",
            "-frames:v", "1", "-pix_fmt", "rgb24", str(output))
    if not Path(output).is_file():
        raise RuntimeError("The color take has no frame %d." % index)
    return Path(output)


def merge_hero_rgba(ffmpeg: Path, plate: Path, alpha: Path, output: Path) -> Path:
    """Plate color with the players matte as a straight 16-bit alpha channel.

    ``alphamerge`` would round the matte through 8 bits; merging planes keeps
    every 16-bit coverage value exact.
    """
    _ffmpeg(ffmpeg, "-i", str(plate), "-i", str(alpha), "-filter_complex",
            "[0:v]format=gbrp16le,setsar=1[c];[1:v]format=gray16le,setsar=1[a];"
            "[c][a]mergeplanes=map0s=0:map0p=0:map1s=0:map1p=1:map2s=0:map2p=2:"
            "map3s=1:map3p=0:format=gbrap16le,format=rgba64be[out]",
            "-map", "[out]", "-frames:v", "1", str(output))
    return Path(output)


def copy_depth(take_folder: Path, index: int, output: Path) -> Path:
    source = Path(take_folder) / "depth" / "exr" / ("%08d.exr" % index)
    if not source.is_file():
        raise RuntimeError("The depth pass has no EXR for frame %d." % index)
    shutil.copyfile(source, output)
    return Path(output)


def finish(ffmpeg: Path, run: StillRun) -> list[Path]:
    """Build the still files inside the take folder and drop the intermediates.

    Intermediates are only removed after every requested output exists; a
    failure keeps the take folder intact for diagnosis.
    """
    folder = run.take_folder
    video = folder / Path(run.options.path).name
    index = settled_index(folder, video.name)
    outputs = [extract_plate(ffmpeg, video, index, folder / "plate.png")]
    alpha, isolated = folder / "hero_alpha.png", folder / "hero_isolated.png"
    if not alpha.is_file() or not isolated.is_file():
        raise RuntimeError("The players stills were not written.")
    outputs += [alpha, merge_hero_rgba(ffmpeg, outputs[0], alpha, folder / "hero_rgba.png"), isolated]
    if run.depth:
        outputs.append(copy_depth(folder, index, folder / "depth.exr"))
    cleanup(folder, video)
    return outputs


def cleanup(folder: Path, video: Path) -> None:
    """Remove the video takes once the stills exist; keep anything unexpected."""
    for path in (video, folder / "shot.json", Path(str(video) + ".shot.json")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    from .file_ops import _plain_path

    def discard(directory, names, pattern=None):
        # No recursive removal: only documented regular intermediates are ours.
        for path in directory.iterdir():
            try:
                if ((path.name in names or pattern and re.fullmatch(pattern, path.name))
                        and _plain_path(path) and path.is_file()):
                    path.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()  # Unknown files/directories deliberately block this.
        except OSError:
            pass

    for sub in (folder / "depth", folder / "players"):
        try:
            if not _plain_path(sub) or not sub.is_dir():
                continue
            if sub.name == "depth":
                exr = sub / "exr"
                if exr.exists() and _plain_path(exr) and exr.is_dir():
                    discard(exr, (), r"[0-9]{8}\.exr")
                discard(sub, ("depth.mov", "depth.mov.shot.json", "shot.json",
                              "manifest.json", "camera.json", "preview.mp4", "preview.mkv"),
                        r"preview_[0-9]+x[0-9]+\.raw")
            else:
                discard(sub, ("players.mov", "players.mov.shot.json", "players.mp4",
                              "players.mp4.shot.json", "players.mkv", "players.mkv.shot.json",
                              "shot.json"))
        except OSError:
            pass
