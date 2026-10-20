"""Exercise real redirected Windows-style legacy encoding, not a mocked print."""
import os
from pathlib import Path
import subprocess
import sys


def test_redirected_legacy_streams_accept_unicode_paths_and_tracebacks():
    code = ("from agr.console import configure_stdio;import sys;"
            "configure_stdio();"
            "print('Saved: C:/Exports/雪 Ω.blend');"
            "print('Error in café/Ω',file=sys.stderr)")
    result = subprocess.run([sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, PYTHONIOENCODING="cp1252"), capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8").strip() == "Saved: C:/Exports/雪 Ω.blend"
    assert result.stderr.decode("utf-8").strip() == "Error in café/Ω"
