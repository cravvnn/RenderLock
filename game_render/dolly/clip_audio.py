"""Deadlock-only audio capture and optional source-voice reconstruction."""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

from . import source2viewer
from .ffmpeg_run import run_ffmpeg, scaled_timeout
from .runtime import external_program_environment, resource_root

LOG = logging.getLogger("dolly")


def _sample_window(offset: float, duration: float, available: int,
                   sample_rate: int = 48000) -> tuple[int, int]:
    """Require full reference coverage before writing or muxing clip audio."""
    if not math.isfinite(offset) or not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Video audio timing is invalid")
    start = round(offset * sample_rate)
    frames = round(duration * sample_rate)
    if offset < 0 or frames <= 0 or start + frames > available:
        raise RuntimeError("Game audio does not cover the complete video window; the silent video is kept")
    return start, frames


def _ffmpeg() -> Path:
    from .video_export import resolve_ffmpeg, bundled_ffmpeg_path
    configured = resolve_ffmpeg(os.environ.get("DOLLY_FFMPEG"))
    if configured is not None:
        return configured
    bundled = bundled_ffmpeg_path()
    if bundled is not None:
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    try:
        import imageio_ffmpeg
        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError, OSError) as exc:
        raise RuntimeError("Audio export needs FFmpeg. Install imageio-ffmpeg or set DOLLY_FFMPEG.") from exc


def _viewer(progress=None) -> Path:
    """Return the reconstructed-audio extractor, installing it on first use."""
    return source2viewer.ensure_viewer(progress=progress)


def _mux(video: Path, audios: list[Path], ffmpeg: Path, duration: float = 0.0) -> None:
    if not audios:
        return
    with tempfile.TemporaryDirectory(prefix="dolly-mux-", dir=video.parent) as folder:
        target = Path(folder) / ("with_audio" + video.suffix)
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-n",
                   "-i", str(video)]
        for audio in audios:
            command += ["-i", str(audio)]
        command += ["-map", "0:v:0"]
        for index in range(len(audios)):
            command += ["-map", f"{index + 1}:a:0"]
        # Tracks have already been cropped to the capture window. Do not use
        # -shortest: audio/container rounding can otherwise discard the final
        # video packet even when the audio is only a fraction of a frame short.
        command += ["-c:v", "copy", "-c:a", "aac", "-b:a", "256k", str(target)]
        with external_program_environment() as environment:
            result = run_ffmpeg(command, timeout=scaled_timeout(300.0, 2.0, duration),
                                env=environment, partial=target,
                                timeout_message="FFmpeg timed out adding clip audio; the silent video is kept")
        if result.returncode:
            raise RuntimeError("FFmpeg could not add clip audio: " + (result.stderr or "")[-1000:])
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce the audio clip")
        os.replace(target, video)


class ClipAudioCapture:
    """Capture the game process and optional native voice log with video."""

    def __init__(self, video: Path, *, game_audio: bool, reconstructed: bool,
                 bridge, game: Path, game_pid: int, controller=None,
                 movie_fps: int = 60, fixed_step: bool = False,
                 speed: float = 1.0, start_engine_seconds: float | None = None):
        self.video = video
        self.game_audio = game_audio
        self.reconstructed = reconstructed
        self.bridge = bridge
        self.game = game
        self.game_pid = game_pid
        self.controller = controller
        self.movie_fps = int(movie_fps)
        self.fixed_step = bool(fixed_step)
        self.speed = float(speed)
        self.start_engine_seconds = start_engine_seconds
        self.movie = None
        self.folder = video.with_name(video.stem + "_reconstructed")
        self.ffmpeg = _ffmpeg()
        self.viewer = None
        if reconstructed:
            last_bucket = -1
            def viewer_progress(done: int, total: int) -> None:
                nonlocal last_bucket
                percent = 0 if total <= 0 else min(100, round(done * 100 / total))
                bucket = percent // 10
                if bucket == last_bucket and percent < 100:
                    return
                last_bucket = bucket
                LOG.info("Reconstructed-audio helper download: %s%%", percent)
                if self.controller is not None:
                    try:
                        self.controller._message(
                            f"Downloading reconstructed-audio helper... {percent}%")
                    except Exception:
                        pass
            self.viewer = _viewer(progress=viewer_progress)
        self.vpk = game.parents[2] / "citadel/pak01_dir.vpk"
        if reconstructed and not self.vpk.is_file():
            raise RuntimeError(f"Deadlock sound archive is missing: {self.vpk}")
        if reconstructed and self.folder.exists():
            raise FileExistsError(f"Reconstructed audio folder already exists: {self.folder}")
        if self.fixed_step:
            from .movie_audio import MovieOutputContext, MovieWave
            if controller is None:
                raise RuntimeError("The owned movie audio session is unavailable.")
            context = controller.movie_output_context()
            if not isinstance(context, MovieOutputContext):
                raise RuntimeError("The owned movie audio output could not be verified.")
            self.movie = MovieWave(video, game, output_context=context)
            self.recorder = None
        else:
            self.recorder = resource_root() / "native/bin/win64/DollyGameAudio.exe"
            if not self.recorder.is_file():
                raise RuntimeError(f"Deadlock-only audio recorder is missing: {self.recorder}")
            if not isinstance(game_pid, int) or game_pid <= 0:
                raise RuntimeError("Deadlock is not running")
        self.temporary = tempfile.TemporaryDirectory(prefix="dolly-clip-audio-")
        self.work = Path(self.temporary.name)
        self.voice_log = self.work / "voices.csv"
        self.reference = self.work / "deadlock.wav"
        self.process = None
        self.voice_started = False

    def start(self) -> None:
        if self.movie is not None:
            if self.reconstructed:
                try:
                    self.bridge.start_source_audio(str(self.voice_log))
                    self.voice_started = True
                except BaseException:
                    self.cancel()
                    raise
            return
        self.process = subprocess.Popen(
            [str(self.recorder), str(self.game_pid), str(self.reference)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if self.process.stdout.readline().strip() != "READY":
            detail = self.process.stderr.read() if self.process.poll() is not None else "No ready signal"
            self.cancel()
            raise RuntimeError(f"Deadlock audio capture could not start: {detail}")
        if self.reconstructed:
            try:
                self.bridge.start_source_audio(str(self.voice_log))
                self.voice_started = True
            except BaseException:
                self.cancel()
                raise

    def start_movie(self) -> None:
        """Start the engine WAV take after fixed-step timing is armed."""
        if self.movie is not None:
            self.movie.start(self.controller, self.movie_fps)

    def _end_capture(self) -> None:
        try:
            if self.voice_started:
                self.bridge.stop_source_audio()
                self.voice_started = False
        finally:
            if self.process:
                try:
                    _, stderr = self.process.communicate(input="\n", timeout=15)
                except subprocess.TimeoutExpired as exc:
                    self.process.kill()
                    self.process.communicate()
                    raise RuntimeError("Deadlock audio capture did not stop") from exc
                if self.process.returncode:
                    raise RuntimeError(f"Deadlock audio capture failed: {stderr.strip()}")

    def finish(self, video_status: dict) -> Path | None:
        if self.movie is not None:
            return self._finish_movie(video_status)
        import soundfile as sf

        self._end_capture()
        first_qpc = int(video_status.get("video_first_qpc", 0))
        frequency = int(video_status.get("qpc_frequency", 0))
        duration = float(video_status.get("duration", 0))
        if first_qpc <= 0 or frequency <= 0 or duration <= 0:
            raise RuntimeError("Video did not report its first frame clock and duration")
        clock = json.loads(Path(str(self.reference) + ".clock.json").read_text())
        origin = float(clock["first_sample_qpc_seconds"])
        offset = first_qpc / frequency - origin
        audio, rate = sf.read(self.reference, always_2d=True, dtype="float32")
        if rate != 48000 or audio.shape[1] != 2:
            raise RuntimeError("Game audio must be 48 kHz stereo")
        start, frames = _sample_window(offset, duration, len(audio), rate)
        metadata = {"first_block_end_monotonic": origin + 1024 / rate,
                    "loopback_block_frames": 1024, "video_first_qpc": first_qpc,
                    "qpc_frequency": frequency, "clip_offset_seconds": offset,
                    "duration_seconds": duration}
        self.reference.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
        tracks = []
        if self.game_audio:
            game_clip = self.video.with_name(self.video.stem + "_game_audio.wav")
            sf.write(game_clip, audio[start:start + frames], 48000, subtype="FLOAT")
            tracks.append(game_clip)
        if self.reconstructed:
            from tools.export_captured_audio import export
            from tools.filter_captured_audio_window import filter_window
            from tools.fit_reference_voices import fit

            filtered = self.work / "window_voices.csv"
            filter_window(self.voice_log, self.reference, filtered)
            sources = self.work / "sources"
            export(filtered, sources, vpk=self.vpk, viewer=self.viewer, ffmpeg=self.ffmpeg)
            state = Path(str(self.voice_log) + ".state.csv")
            if not state.is_file():
                raise RuntimeError("The native voice-state sidecar is missing")
            fitted = self.work / "fitted"
            fit(SimpleNamespace(source_dir=sources, reference_wav=self.reference,
                                reference_json=self.reference.with_suffix(".json"),
                                state_csv=state, output=fitted,
                                clip_offset=offset, clip_duration=duration, passes=8))
            self.folder.mkdir(parents=True)
            shutil.move(str(fitted / "sounds"), str(self.folder / "sounds"))
            shutil.move(str(fitted / "reconstructed.wav"), str(self.folder / "reconstructed.wav"))
            shutil.move(str(fitted / "fitted_usage.csv"), str(self.folder / "usage.csv"))
        _mux(self.video, tracks, self.ffmpeg, duration)
        self._cleanup_work()
        return self.folder if self.reconstructed else None

    def _finish_movie(self, video_status: dict) -> Path | None:
        """Finish a fixed-step take from the engine's frame-locked WAV."""
        self._end_capture()
        duration = float(video_status.get("duration", 0))
        if duration <= 0:
            raise RuntimeError("Video did not report a duration for its audio.")
        # The movie WAV starts at the prepared replay time. Its demo clock is
        # the same clock the native voice log records in engine_seconds; the
        # project tick is not necessarily the demo clock (replays can start
        # with an intro/time offset).
        start_engine = self.movie.movie_engine_seconds
        if start_engine is None:
            start_engine = self.start_engine_seconds
        if self.reconstructed and start_engine is None:
            raise RuntimeError("Fixed-step reconstructed audio needs a camera path or POV shot.")
        self.reference = self.movie.finish(self.controller, duration, self.work,
                                           start_engine, self.speed, video_status=video_status)
        tracks = []
        if self.game_audio:
            game_clip = self.video.with_name(self.video.stem + "_game_audio.wav")
            shutil.copy2(self.reference, game_clip)
            tracks.append(game_clip)
        if self.reconstructed:
            from tools.export_captured_audio import export
            from tools.fit_reference_voices import fit

            sources = self.work / "sources"
            export(self.voice_log, sources, vpk=self.vpk, viewer=self.viewer, ffmpeg=self.ffmpeg)
            state = Path(str(self.voice_log) + ".state.csv")
            if not state.is_file():
                raise RuntimeError("The native voice-state sidecar is missing")
            fitted = self.work / "fitted"
            fit(SimpleNamespace(source_dir=sources, reference_wav=self.reference,
                                reference_json=None, state_csv=state, output=fitted,
                                clip_offset=0.0, clip_duration=duration, passes=8,
                                engine_start=start_engine, speed=self.speed))
            self.folder.mkdir(parents=True)
            shutil.move(str(fitted / "sounds"), str(self.folder / "sounds"))
            shutil.move(str(fitted / "reconstructed.wav"), str(self.folder / "reconstructed.wav"))
            shutil.move(str(fitted / "fitted_usage.csv"), str(self.folder / "usage.csv"))
        _mux(self.video, tracks, self.ffmpeg, duration)
        self._cleanup_work()
        return self.folder if self.reconstructed else None

    def cancel(self) -> None:
        try:
            if self.movie is not None:
                self.movie.cancel(self.controller)
        finally:
            try:
                if self.voice_started:
                    self.bridge.stop_source_audio()
                    self.voice_started = False
            finally:
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    self.process.communicate()
                self._cleanup_work()


    def _cleanup_work(self) -> None:
        try:
            self.temporary.cleanup()
        except OSError:
            LOG.warning("Could not remove temporary clip-audio work: %s", self.work, exc_info=True)
