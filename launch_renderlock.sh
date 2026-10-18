#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

echo "RenderLock — Replay to Blender"
renderlock_python=""
if [[ -x .venv/bin/python ]]; then
  if ! .venv/bin/python -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    echo "The existing .venv needs Python 3.12+. Rename it and run this launcher again." >&2
    exit 1
  fi
  renderlock_python=".venv/bin/python"
else
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
      renderlock_python="$candidate"
      break
    fi
  done
fi
if [[ -z "$renderlock_python" ]]; then
  echo "Install Python 3.12+ with Tkinter, then run this launcher again. Blender is installed separately." >&2
  exit 1
fi
if ! "$renderlock_python" -c "import numpy, boon, polars, snappy, tkinter; from importlib.metadata import version; raise SystemExit(0 if version('boon-deadlock') == '0.15.0' and version('polars') == '1.44.1' and version('python-snappy') == '0.7.3' else 'RenderLock dependency versions do not match requirements.txt')" >/dev/null 2>&1; then
  echo "Preparing RenderLock's local environment with tested dependency versions. First setup downloads requirements from PyPI."
  if [[ ! -x .venv/bin/python ]]; then
    "$renderlock_python" -m venv .venv
  fi
  renderlock_python=".venv/bin/python"
  "$renderlock_python" -m pip install --disable-pip-version-check -r requirements.txt
  if ! "$renderlock_python" -c "import numpy, boon, polars, snappy, tkinter; from importlib.metadata import version; raise SystemExit(0 if version('boon-deadlock') == '0.15.0' and version('polars') == '1.44.1' and version('python-snappy') == '0.7.3' else 'RenderLock dependency versions do not match requirements.txt')"; then
    echo "Dependency setup failed. If Tkinter is missing, install it using your Python provider or OS package manager." >&2
    exit 1
  fi
fi
exec "$renderlock_python" -m agr.gui
