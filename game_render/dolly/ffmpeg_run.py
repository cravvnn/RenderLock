"""One bounded FFmpeg child run: UTF-8 text, no stdin, no console window."""
from __future__ import annotations

import logging
from pathlib import Path
import subprocess

LOG = logging.getLogger(__name__)


def scaled_timeout(floor: float, seconds_per_unit: float, units: float) -> float:
    """A child limit that grows with the take's duration or frame count."""
    return max(float(floor), float(seconds_per_unit) * float(units))


def remove_partial(path: "Path | None") -> None:
    """A failed or killed encoder must not leave a truncated file behind."""
    if path is None:
        return
    try:
        if Path(path).is_file():
            Path(path).unlink()
    except OSError:
        LOG.warning("Incomplete FFmpeg output could not be removed: %s", path)


def last_line(result) -> str:
    """The last line FFmpeg reported, for a short user-facing error."""
    lines = (result.stderr or "").strip().splitlines()
    return lines[-1] if lines else "unknown error"


def run_ffmpeg(command, *, timeout: float, env=None, partial: "Path | None" = None,
               timeout_message: "str | None" = None) -> subprocess.CompletedProcess:
    """Run FFmpeg once and return its result.

    FFmpeg prints paths as UTF-8 whatever the Windows code page, so stderr is
    decoded that way with replacement. On a timeout subprocess.run kills and
    waits for the child before raising. ``partial`` is removed when the run
    fails, times out or cannot start; ``timeout_message`` turns a timeout into
    a RuntimeError with that text.
    """
    try:
        result = subprocess.run(list(command), capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                stdin=subprocess.DEVNULL, timeout=timeout, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        remove_partial(partial)
        if timeout_message is not None:
            raise RuntimeError(timeout_message) from exc
        raise
    except BaseException:
        remove_partial(partial)
        raise
    if result.returncode != 0:
        remove_partial(partial)
    return result
