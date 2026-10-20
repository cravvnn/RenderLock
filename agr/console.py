"""Consistent UTF-8 redirected logs, including Windows portable child processes."""
from __future__ import annotations

import sys


def configure_stdio():
    """Do not let a user's path or a traceback fail on a legacy code page.

    Isolated Python ignores PYTHONUTF8/PYTHONIOENCODING environment variables.
    Configure the streams themselves too, so direct CLI/Blender entry points
    and pythonw-style processes with absent streams remain safe.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                # Embedded hosts may provide a closed or fixed text stream.
                pass
