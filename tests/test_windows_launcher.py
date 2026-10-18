"""Run actual cmd fallback branches on Windows with controlled probe exit codes."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows cmd")
def test_outer_launcher_with_current_directory_search_disabled(tmp_path):
    folder = tmp_path / "RenderLock (launch) & spaces!"
    folder.mkdir()
    source = (Path(__file__).parents[1] / "Launch_RenderLock.bat").read_bytes()
    (folder / "Launch_RenderLock.bat").write_bytes(source)
    (folder / "Launch_RenderLock_Windows.cmd").write_text(
        "@echo WORKER_STARTED\n@exit /b 7\n", encoding="ascii")
    env = dict(os.environ, NoDefaultCurrentDirectoryInExePath="1")
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", r".\Launch_RenderLock.bat"],
        cwd=folder, env=env, input="\n", capture_output=True, text=True, timeout=30)
    assert result.returncode == 7, result.stdout + result.stderr
    assert "WORKER_STARTED" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Requires the real Windows command processor")
@pytest.mark.parametrize("checkout", [False, True])
def test_development_launcher_only_in_checkout(tmp_path, checkout):
    folder = tmp_path / "RenderLock (development) & spaces!"
    folder.mkdir()
    (folder / "Launch_RenderLock.bat").write_bytes(
        (Path(__file__).parents[1] / "Launch_RenderLock.bat").read_bytes())
    (folder / "Launch_RenderLock_Windows.cmd").write_text(
        "@echo RELEASE_VERIFICATION\n@exit /b 7\n", encoding="ascii")
    (folder / "Launch_RenderLock_Development.bat").write_text(
        "@echo DEVELOPMENT_STARTED\n@exit /b 9\n", encoding="ascii")
    if checkout:
        (folder / ".git").mkdir()
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", r".\Launch_RenderLock.bat"],
        cwd=folder, env=dict(os.environ, NoDefaultCurrentDirectoryInExePath="1"),
        input="\n", capture_output=True, text=True, timeout=30)
    assert result.returncode == (9 if checkout else 7), result.stdout + result.stderr
    assert ("DEVELOPMENT_STARTED" in result.stdout) == checkout
    assert ("RELEASE_VERIFICATION" in result.stdout) != checkout


@pytest.mark.skipif(os.name != "nt", reason="Requires the real Windows command processor")
@pytest.mark.parametrize("failure", [-1610612730, 1, 9009])
def test_failed_py_probe_falls_through_to_python(tmp_path, failure):
    source = (Path(__file__).parents[1] / "Launch_RenderLock_Windows.cmd").read_text()
    lines = []
    for line in source.splitlines():
        # Stub only external commands, retaining the real batch branches.
        if line.startswith(("py -3.12 -c ", "py -3 -c ")):
            line = f"cmd.exe /d /c exit /b {failure}"
        elif line.startswith("python -c "):
            line = "cmd.exe /d /c exit /b 0"
        elif line.startswith(("py -3.12 -u ", "py -3 -u ")):
            line = "echo INCORRECT_PY_BOOTSTRAP"
        elif line.startswith("python -u "):
            line = "echo CORRECT_PYTHON_BOOTSTRAP"
        lines.append(line)
    folder = tmp_path / "RenderLock (different folder) & spaces!"
    folder.mkdir()
    worker = folder / "worker.cmd"
    worker.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))
    env = os.environ.copy()
    env.pop("RENDERLOCK_PYTHON_EXE", None)
    result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", r".\worker.cmd"],
                            cwd=folder, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CORRECT_PYTHON_BOOTSTRAP" in result.stdout
    assert "INCORRECT_PY_BOOTSTRAP" not in result.stdout
