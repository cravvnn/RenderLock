"""Standard-library-only Windows setup; all output is captured by Launch_RenderLock.bat."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import struct
import subprocess
import sys
import traceback


ROOT = Path(__file__).resolve().parent
STARTUP_VERSION = "0.3.0b1"
PYTHON_CHECK = """import struct, sys
print('Python executable:', sys.executable)
print('Python version:', sys.version)
print('Python bits:', struct.calcsize('P') * 8)
sys.exit(0 if sys.version_info >= (3, 12) and struct.calcsize('P') == 8 else 1)
"""
DEPENDENCY_CHECK = """import numpy, boon, polars, snappy
from importlib.metadata import version
expected = {'boon-deadlock': '0.15.0', 'polars': '1.44.1', 'python-snappy': '0.7.3'}
for package, required in expected.items():
    installed = version(package)
    print(package + ': ' + installed)
    if installed != required:
        raise RuntimeError(package + ' requires ' + required)
np_version = tuple(int(n) for n in version('numpy').split('.')[:2])
if not (1, 26) <= np_version < (3, 0):
    raise RuntimeError('numpy requires >=1.26,<3')
print('numpy: ' + version('numpy'))
"""


def child_environment():
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def run(argv, *, cwd=None):
    print("Running:", subprocess.list2cmdline([str(item) for item in argv]), flush=True)
    return subprocess.run([str(item) for item in argv], cwd=cwd or ROOT,
                          env=child_environment(), stdin=subprocess.DEVNULL).returncode


def verify_program_files(root):
    """Check source bytes before importing RenderLock or touching its environment/cache."""
    root = Path(root)
    print("Checking RenderLock program files...", flush=True)
    try:
        manifest = json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))
        if manifest["version"] != STARTUP_VERSION:
            raise ValueError("release version does not match launcher " + STARTUP_VERSION)
        entries = {}
        for item in manifest["files"]:
            name = item["path"]
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name
                    or ":" in name or path.as_posix() != name):
                raise ValueError("invalid manifest path: " + repr(name))
            if name == "bootstrap.py" or (path.parts[0] == "agr" and path.suffix == ".py"):
                if name in entries:
                    raise ValueError("duplicate source entry: " + name)
                if (not isinstance(item["bytes"], int) or item["bytes"] < 0
                        or not isinstance(item["sha256"], str) or len(item["sha256"]) != 64):
                    raise ValueError("invalid source checksum entry: " + name)
                entries[name] = item
        required = {"bootstrap.py", "agr/__init__.py", "agr/gui.py", "agr/cli.py"}
        if not required.issubset(entries):
            raise ValueError("manifest is missing required RenderLock source entries")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print("ERROR: release_manifest.json: " + str(exc), flush=True)
        print("Extract ALL files from the latest RenderLock update beside Launch_RenderLock.bat, replacing files.", flush=True)
        return False

    errors = []
    for name, item in sorted(entries.items()):
        try:
            data = (root / name).read_bytes()
        except OSError as exc:
            errors.append(f"{name}: cannot read source ({exc})")
            continue
        if b"\0" in data:
            errors.append(f"{name}: {data.count(bytes([0]))} null byte(s); first at byte {data.index(bytes([0]))}")
        else:
            try:
                compile(data, name, "exec", dont_inherit=True)
            except (SyntaxError, ValueError) as exc:
                errors.append(f"{name}: invalid Python source ({exc})")
        actual = hashlib.sha256(data).hexdigest()
        if len(data) != item["bytes"] or actual != item["sha256"]:
            errors.append(f"{name}: differs from RenderLock {STARTUP_VERSION} release "
                          f"(bytes {len(data)}/{item['bytes']}; SHA256 {actual}/{item['sha256']})")
    if errors:
        for error in errors:
            print("ERROR: " + error, flush=True)
        print("Program file check failed before opening RenderLock.", flush=True)
        print("Extract ALL files from the latest RenderLock update beside Launch_RenderLock.bat, replacing files.", flush=True)
        print("Keep your asset cache, settings and .venv. Send this complete startup log if it still fails.", flush=True)
        return False
    print(f"RenderLock {STARTUP_VERSION}: {len(entries)} program source files passed SHA256 and syntax checks.", flush=True)
    return True


def main():
    print("RenderLock " + STARTUP_VERSION + " startup diagnostics", flush=True)
    print("System:", platform.platform(), flush=True)
    print("Program folder:", ROOT, flush=True)
    if sys.version_info < (3, 12) or struct.calcsize("P") != 8:
        print("ERROR: Install 64-bit Python 3.12 or newer with Tcl/Tk.", flush=True)
        return 1
    if not (ROOT / "agr" / "gui.py").is_file() or not (ROOT / "requirements.txt").is_file():
        print("ERROR: Program files are missing. Extract the complete program ZIP.", flush=True)
        return 1
    if not verify_program_files(ROOT):
        return 1

    venv = ROOT / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        if venv.exists():
            print("ERROR: The existing .venv is incomplete. Rename it to .venv_old, then relaunch.", flush=True)
            return 1
        print("Creating RenderLock's local Python environment...", flush=True)
        if run([sys.executable, "-m", "venv", venv]):
            print("ERROR: Could not create .venv. Use a writable extracted folder and Python with pip/venv.", flush=True)
            return 1
    if run([python, "-u", "-c", PYTHON_CHECK]):
        print("ERROR: The existing .venv is incompatible. Rename it to .venv_old, then relaunch with 64-bit Python 3.12+.", flush=True)
        return 1

    if run([python, "-u", "-c", "import tkinter; t = tkinter.Tcl(); print('Tcl version:', t.eval('info patchlevel'))"]):
        print("ERROR: Tcl/Tk is unavailable. Repair your Python installation with Tcl/Tk and IDLE enabled, then rename .venv and relaunch.", flush=True)
        return 1
    if run([python, "-u", "-c", DEPENDENCY_CHECK]):
        print("Installing requirements. First setup requires internet access; pip progress follows.", flush=True)
        if run([python, "-u", "-m", "pip", "install", "--disable-pip-version-check", "-r", ROOT / "requirements.txt"]):
            print("ERROR: Dependency installation failed. The pip error above identifies the package or download problem.", flush=True)
            return 1
        if run([python, "-u", "-c", DEPENDENCY_CHECK]):
            print("ERROR: Dependencies still cannot load after installation. Send the complete startup log.", flush=True)
            return 1

    helper = ROOT / "bin" / ("agr-extract.exe" if os.name == "nt" else "agr-extract")
    print("Native replay helper present:", helper.is_file(), flush=True)
    print("Bundled asset pack present:", (ROOT / "assets" / "assets.json").is_file(), flush=True)
    print("Opening the RenderLock desktop window...", flush=True)
    code = run([python, "-u", "-X", "faulthandler", "-m", "agr.gui"])
    if code:
        print("ERROR: RenderLock desktop exited with code", code, flush=True)
    else:
        print("RenderLock desktop process exited normally. If no window appeared, send this startup log.", flush=True)
    return code


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check-program-files"]:
            raise SystemExit(0 if verify_program_files(ROOT) else 1)
        raise SystemExit(main())
    except Exception:
        print("ERROR: Unexpected startup failure.", file=sys.stderr, flush=True)
        traceback.print_exc()
        raise SystemExit(1)
