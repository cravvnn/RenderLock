"""Frame-locked movie WAV capture for fixed-step exports.

The engine movie recorder is started with the ``wav`` format only, so it writes
no TGA frames. DollyNative redirects its GetFramerate accessor to the current
``host_framerate``. The recorder's WAV clock still advances at the requested
movie FPS, which makes the WAV exactly as long as the slow-motion video and
keeps the authored replay window.
"""
from __future__ import annotations

import re
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import tempfile
from typing import Callable
import uuid

from .ffmpeg_run import run_ffmpeg, scaled_timeout
from .file_ops import _plain_path

MOVIE_START = re.compile(
    r"Started recording movie:\s+movie/([0-9]{4}_[0-9]{2}_[0-9]{2}_[0-9]{2}_[0-9]{2}_[0-9]{2})/")


def _movie_sample_windows(video: Path, status: dict, name: str) -> tuple[int, list[tuple[int, int]]]:
    """Require a complete PCM map for this exact video and movie take."""
    # VideoExporter moves the native sidecar into the bundled take folder
    # before ClipAudioCapture finishes. Read the finalized owner's location.
    sidecar = video.parent / 'shot.json'
    error = 'Movie audio sample alignment is unavailable; the silent video and source WAV were kept.'
    try:
        if not _plain_path(sidecar) or sidecar.stat().st_size > 40 * 1024 * 1024:
            raise ValueError('unsafe or oversized sample map')
        with sidecar.open('rb') as stream:
            raw = stream.read(40 * 1024 * 1024 + 1)
        if len(raw) > 40 * 1024 * 1024:
            raise ValueError('sample map exceeds the read budget')
        data = json.loads(raw.decode('utf-8'))
        frames = status.get('frames_written')
        fps = status.get('fps')
        duration = float(status.get('duration', 0))
        mapping = data['movie_audio']
        rows = mapping['frames']
        if (type(frames) is not int or not 0 < frames <= 262144 or
                type(fps) is not int or fps <= 0 or
                not math.isfinite(duration) or abs(duration - frames / fps) > 1e-5 or
                data.get('format') != 'deadlock-dolly-shot' or data.get('version') != 1 or
                data.get('video_file') != video.name or data.get('fps') != fps or
                data.get('fixed_step') is not True or data.get('frames_written') != frames or
                mapping.get('version') != 1 or mapping.get('complete') is not True or
                not isinstance(rows, list) or len(rows) != frames):
            raise ValueError('incomplete or foreign sample map')
        take = int(name.removeprefix('dolly_audio_'), 16)
        windows = []
        previous = 0
        previous_frame = -1
        rate = 48000
        for row in rows:
            if not isinstance(row, list) or len(row) != 5 or any(type(v) is not int for v in row):
                raise ValueError('invalid sample row')
            identity, first, end, movie_frame, sample_rate = row
            if (identity != take or not 0 <= first < end or first < previous or
                    movie_frame <= previous_frame or sample_rate != 44100 or
                    first != (movie_frame * sample_rate + fps // 2) // fps or
                    end != ((movie_frame + 1) * sample_rate + fps // 2) // fps):
                raise ValueError('changed movie or missing PCM interval')
            # Resample the continuous source first. 48k divides every supported
            # export FPS, so skipped odd/even 44.1k frames cannot accumulate a
            # half-sample rounding error into drift or a false coverage refusal.
            windows.append(((movie_frame * rate + fps // 2) // fps,
                            ((movie_frame + 1) * rate + fps // 2) // fps))
            previous, previous_frame = end, movie_frame
        if abs(sum(b-a for a,b in windows) - round(duration * rate)) > 1:
            raise ValueError('movie samples do not cover the video duration')
        return rate, windows
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        raise RuntimeError(error) from exc


def _align_movie_samples(source: Path, target: Path, rate: int,
                         windows: list[tuple[int, int]]) -> None:
    """Stream exact captured intervals; preparation and skipped frames stay out."""
    import soundfile as sf
    with sf.SoundFile(source) as wav:
        if wav.samplerate != rate or wav.channels != 2 or windows[-1][1] > wav.frames:
            raise RuntimeError('Movie audio does not cover the captured frames; source WAV was kept.')
        with sf.SoundFile(target, 'w', samplerate=rate, channels=2, subtype='FLOAT') as aligned:
            for first, end in windows:
                wav.seek(first)
                block = wav.read(end - first, dtype='float32', always_2d=True)
                if len(block) != end - first:
                    raise RuntimeError('Movie audio ended before the captured frame; source WAV was kept.')
                aligned.write(block)


def _run_ffmpeg(command: list[str], environment, duration: float, timeout_message: str):
    """Run one bounded audio conversion; FFmpeg reports paths as UTF-8."""
    return run_ffmpeg(command, timeout=scaled_timeout(120.0, 4.0, duration), env=environment,
                      timeout_message=timeout_message)


@dataclass(frozen=True)
class MovieOutputContext:
    root: Path
    verify_owner: Callable[[object], None] = field(repr=False, compare=False)


class MovieWave:
    """A single ``startmovie <name> wav framerate <fps>`` take."""

    def __init__(self, video: Path, game_executable: Path, *, output_context: MovieOutputContext | None = None):
        self.video = Path(video)
        # ...\Deadlock\game\bin\win64\deadlock.exe -> ...\Deadlock\game\citadel
        self.citadel = Path(game_executable).parents[2] / "citadel"
        self._output_context = output_context
        self.movie_root = output_context.root if output_context is not None else self.citadel / "movie"
        self._root = self.movie_root
        self._controller = None
        self._capture_folder = None
        self._capture_name = None
        self._directory_ids = {}
        self.name = "dolly_audio_" + uuid.uuid4().hex[:10]
        self.folder: Path | None = None
        self.started = False
        self.finished = False
        self.error: str | None = None
        self.movie_engine_seconds: float | None = None

    def _verify_output(self, controller=None) -> None:
        if self._controller is not None and controller is not None and controller is not self._controller:
            raise RuntimeError("The movie audio controller changed; its files were kept.")
        if self._output_context is not None:
            self._output_context.verify_owner(controller if controller is not None else self._controller)
        if self.movie_root != self._root or not self.movie_root.is_absolute():
            raise RuntimeError("The movie audio output root changed; its files were kept.")
        paths = [self.movie_root]
        if self.folder is not None:
            if self.folder != self._capture_folder or self.name != self._capture_name:
                raise RuntimeError("The movie audio capture identity changed; its files were kept.")
            paths.extend((self.folder, self.folder / (self.name + ".wav")))
        try:
            for path in paths:
                for part in (path, *path.parents):
                    try:
                        if not _plain_path(part):
                            raise RuntimeError("Linked movie audio paths were left untouched.")
                    except FileNotFoundError:
                        continue
            for directory in (self.movie_root, self.folder):
                if directory is None or not directory.exists():
                    continue
                if not directory.is_dir():
                    raise RuntimeError("The movie audio directory changed; its files were kept.")
                info = directory.stat()
                identity = (info.st_dev, info.st_ino)
                previous = self._directory_ids.setdefault(directory, identity)
                if previous != identity:
                    raise RuntimeError("The movie audio directory changed; its files were kept.")
        except OSError as exc:
            raise RuntimeError("Could not verify movie audio paths; their files were kept.") from exc

    def start(self, controller, fps: int) -> None:
        if self.started:
            raise RuntimeError("The movie audio take is already recording.")
        if controller is None:
            raise RuntimeError("The game console is not available for movie audio.")
        if not re.fullmatch(r'dolly_audio_[0-9a-f]{10}', self.name):
            raise RuntimeError("The movie audio capture name is invalid.")
        self._verify_output(controller)
        self._controller = controller
        try:
            pid = controller.game_pid()
        except Exception:
            pid = None
        if pid is not None:
            marker = Path(tempfile.gettempdir()) / f"dolly_movie_audio_{pid}.ready"
            if not marker.is_file() or marker.read_text(encoding='ascii') != 'pcm-sample-map-v1':
                raise RuntimeError(
                    "This Dolly native build does not support fixed-step audio. "
                    "Update Dolly or turn off Fixed-step export for audio.")
        # startmovie only records while the in-game console window is closed.
        try:
            controller._request("hideconsole", timeout=4, allow_error=True)
        except Exception:
            pass
        self._verify_output(controller)
        response = controller._request(
            f"startmovie {self.name} wav framerate {int(fps)}", timeout=12,
            allow_error=True)
        match = MOVIE_START.search(response)
        if not match:
            detail = response.strip().splitlines()[-1:] or ["no response"]
            raise RuntimeError("The game did not start the movie audio take: " + detail[0])
        self._verify_output(controller)
        self.folder = self.movie_root / match.group(1)
        self._capture_folder, self._capture_name = self.folder, self.name
        self._verify_output(controller)
        self.started = True
        self.movie_engine_seconds = None
        try:
            bridge = controller._native_bridge()
            native = bridge.status() if bridge is not None else {}
            value = native.get("engine_time")
            if value is not None:
                self.movie_engine_seconds = float(value)
        except Exception:
            self.movie_engine_seconds = None
        if self.movie_engine_seconds is None:
            try:
                value = controller.status().get("time")
                if value is not None:
                    self.movie_engine_seconds = float(value)
            except Exception:
                self.movie_engine_seconds = None
        if self.movie_engine_seconds is None:
            try:
                tick_rate = float(controller.replay_tick_rate() or 64.0)
                text = controller._request("demo_goto", timeout=4, allow_error=True)
                tick = re.search(r"Currently playing (\d+) of", text)
                if tick:
                    self.movie_engine_seconds = int(tick.group(1)) / tick_rate
            except Exception:
                pass

    def finish(self, controller, duration: float, work: Path,
               start_engine_seconds: float | None = None,
               speed: float = 1.0, *, video_status: dict | None = None) -> Path:
        if not self.started:
            raise RuntimeError("The movie audio take was not started.")
        self._verify_output(controller)
        response = controller._request("endmovie", timeout=45, allow_error=True)
        if "Finishing movie" not in response:
            detail = response.strip().splitlines()[-1:] or ["no response"]
            raise RuntimeError("The game did not finish the movie audio take: " + detail[0])
        self.finished = True
        self._verify_output(controller)
        if self.folder is None:
            raise RuntimeError("The movie audio folder was not reported.")
        source = self.folder / f"{self.name}.wav"
        if not source.is_file() or source.stat().st_size <= 0:
            raise RuntimeError("The movie audio WAV was not written.")
        duration = float(duration)
        if duration <= 0:
            raise RuntimeError("The video did not report a duration for its audio.")
        from .clip_audio import _ffmpeg
        from .runtime import external_program_environment
        if video_status is not None:
            rate, windows = _movie_sample_windows(self.video, video_status, self.name)
            import soundfile as sf
            with sf.SoundFile(source) as wav:
                if (wav.samplerate != 44100 or wav.channels != 2 or
                        wav.frames * rate < windows[-1][1] * wav.samplerate):
                    raise RuntimeError('Movie audio does not cover the captured frames; source WAV was kept.')
            converted = Path(work) / 'movie_resampled.wav'
            with external_program_environment() as environment:
                result = _run_ffmpeg([str(_ffmpeg()), '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(source), '-ar', str(rate), '-ac', '2', '-c:a', 'pcm_f32le', str(converted)],
                    environment, duration, 'Movie audio resampling timed out; source WAV was kept.')
            if result.returncode or not converted.is_file():
                raise RuntimeError('Movie audio resampling failed; source WAV was kept: ' + (result.stderr or '')[-1000:])
            aligned = Path(work) / 'movie_aligned.wav'
            _align_movie_samples(converted, aligned, rate, windows)
            source = aligned
        offset = 0.0
        if video_status is None and start_engine_seconds is not None and self.movie_engine_seconds is not None:
            delta = float(start_engine_seconds) - float(self.movie_engine_seconds)
            if 0.0 <= delta <= 30.0:
                offset = delta / max(0.05, float(speed))
        target = Path(work) / "movie_reference.wav"
        command = [str(_ffmpeg()), "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(source), "-ss", f"{offset:.9f}", "-t", f"{duration:.9f}",
                   "-af", "apad", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                   str(target)]
        with external_program_environment() as environment:
            result = _run_ffmpeg(command, environment, duration,
                                 "The movie audio WAV conversion timed out.")
        if result.returncode or not target.is_file() or target.stat().st_size <= 0:
            detail = (result.stderr or "").strip()[-1000:]
            raise RuntimeError("The movie audio WAV could not be converted: " + detail)
        self.cleanup()
        return target

    def cancel(self, controller=None) -> None:
        self._verify_output(controller)
        if self.started and not self.finished:
            if controller is None:
                raise RuntimeError("Movie audio stop was not confirmed; its WAV was kept.")
            try:
                response = controller._request("endmovie", timeout=15, allow_error=True)
            except Exception as exc:
                raise RuntimeError("Movie audio stop was not confirmed; its WAV was kept.") from exc
            if "Finishing movie" not in response:
                raise RuntimeError("Movie audio stop was not confirmed; its WAV was kept.")
            self.finished = True
        self.cleanup()

    def cleanup(self) -> None:
        if self.folder is None:
            return
        self._verify_output()
        try:
            # This timestamp directory may contain a second recording or an
            # unknown file. Only this take's exact WAV belongs to us.
            source = self.folder / (self.name + ".wav")
            if source.is_file():
                source.unlink()
            self.folder.rmdir()  # Remove only an empty directory; keep siblings.
        except OSError:
            pass
        if self._output_context is not None:
            try:
                self.movie_root.rmdir()  # Only our empty per-session MOD root.
            except OSError:
                pass
        self.folder = None
