"""Exercise launch failure reporting without installing packages or opening Tk."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("agr_bootstrap_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STARTUP_VERSION", "0.2.1")
    # Exercise punctuation in real paths; subprocess receives argument arrays.
    root = tmp_path / "DeadBlend test (alpha) & other!"
    (root / "agr").mkdir(parents=True)
    (root / "agr" / "gui.py").touch()
    (root / "agr" / "__init__.py").write_text('__version__ = "0.2.1"\n')
    (root / "agr" / "cli.py").touch()
    (root / "bootstrap.py").write_bytes(path.read_bytes())
    (root / "requirements.txt").write_text("numpy>=1.26,<3\n")
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module.sys, "version_info", (3, 12, 0))
    write_manifest(root)
    return module


def write_manifest(root):
    sources = [root / "bootstrap.py", *sorted((root / "agr").rglob("*.py"))]
    entries = [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources]
    (root / "release_manifest.json").write_text(json.dumps({"version": "0.2.1", "files": entries}))


def outcomes(module, monkeypatch, codes):
    calls = []
    pending = iter(codes)

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return next(pending)

    monkeypatch.setattr(module, "run", fake_run)
    return calls


def test_gui_failure_is_reported_and_preserved(bootstrap, monkeypatch, capsys):
    calls = outcomes(bootstrap, monkeypatch, [0, 0, 0, 23])
    assert bootstrap.main() == 23
    assert calls[-1][-2:] == ["-m", "agr.gui"]
    assert "DeadBlend desktop exited with code 23" in capsys.readouterr().out


def test_normal_exit_includes_no_window_log_guidance(bootstrap, monkeypatch, capsys):
    outcomes(bootstrap, monkeypatch, [0, 0, 0, 0])
    assert bootstrap.main() == 0
    assert "If no window appeared, send this startup log" in capsys.readouterr().out


def test_pip_failure_stops_before_gui(bootstrap, monkeypatch, capsys):
    calls = outcomes(bootstrap, monkeypatch, [0, 0, 1, 1])
    assert bootstrap.main() == 1
    assert "pip" in calls[-1]
    assert all("agr.gui" not in command for command in calls)
    assert "Dependency installation failed" in capsys.readouterr().out


def test_installed_dependencies_are_rechecked(bootstrap, monkeypatch, capsys):
    calls = outcomes(bootstrap, monkeypatch, [0, 0, 1, 0, 1])
    assert bootstrap.main() == 1
    assert calls[-1][-1] == bootstrap.DEPENDENCY_CHECK
    assert "Dependencies still cannot load" in capsys.readouterr().out


def test_tcl_failure_stops_before_dependency_setup(bootstrap, monkeypatch, capsys):
    calls = outcomes(bootstrap, monkeypatch, [0, 1])
    assert bootstrap.main() == 1
    assert len(calls) == 2
    assert "Tcl/Tk is unavailable" in capsys.readouterr().out


def test_incomplete_environment_has_recovery_guidance(bootstrap, monkeypatch, capsys):
    python = bootstrap.ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.unlink()
    calls = outcomes(bootstrap, monkeypatch, [])
    assert bootstrap.main() == 1
    assert calls == []
    assert "Rename it to .venv_old" in capsys.readouterr().out


def test_child_arguments_are_not_shell_commands_and_logs_are_inherited(bootstrap, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "unrelated Python")
    monkeypatch.setenv("PYTHONPATH", "unrelated packages")
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    exe = bootstrap.ROOT / ".venv" / "Scripts" / "python.exe"
    assert bootstrap.run([exe, "-c", "print('log')"]) == 17
    assert captured["argv"] == [str(exe), "-c", "print('log')"]
    assert captured["cwd"] == bootstrap.ROOT
    assert not captured.get("shell", False)
    assert "stdout" not in captured and "stderr" not in captured
    assert "PYTHONHOME" not in captured["env"]
    assert "PYTHONPATH" not in captured["env"]
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"


def test_null_source_stops_before_setup_and_names_file(bootstrap, monkeypatch, capsys):
    bad = bootstrap.ROOT / "agr" / "__init__.py"
    bad.write_bytes(b'__version__ = "0.2.1"\n\0')
    calls = outcomes(bootstrap, monkeypatch, [])
    assert bootstrap.main() == 1
    assert calls == []
    output = capsys.readouterr().out
    assert "agr/__init__.py: 1 null byte(s)" in output
    assert "differs from DeadBlend 0.2.1 release" in output
    assert "Keep your asset cache, settings and .venv" in output


@pytest.mark.parametrize("payload", [b'\0' * 24, b'def broken(:\n', 'print("hello")'.encode('utf-16')])
def test_matching_checksum_does_not_hide_invalid_python(bootstrap, payload, capsys):
    (bootstrap.ROOT / "agr" / "gui.py").write_bytes(payload)
    write_manifest(bootstrap.ROOT)
    assert not bootstrap.verify_program_files(bootstrap.ROOT)
    assert "agr/gui.py:" in capsys.readouterr().out


def test_valid_but_wrong_source_is_rejected(bootstrap, capsys):
    (bootstrap.ROOT / "agr" / "gui.py").write_text("# incomplete update\n")
    assert not bootstrap.verify_program_files(bootstrap.ROOT)
    assert "agr/gui.py: differs" in capsys.readouterr().out


def test_missing_source_is_named(bootstrap, capsys):
    (bootstrap.ROOT / "agr" / "cli.py").unlink()
    assert not bootstrap.verify_program_files(bootstrap.ROOT)
    assert "agr/cli.py: cannot read source" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["missing", "invalid_json", "old_version", "missing_entry", "unsafe_path"])
def test_broken_manifest_has_recovery_message(bootstrap, change, capsys):
    path = bootstrap.ROOT / "release_manifest.json"
    manifest = json.loads(path.read_text())
    if change == "missing":
        path.unlink()
    elif change == "invalid_json":
        path.write_text("bad json")
    else:
        if change == "old_version":
            manifest["version"] = "0.1.5"
        elif change == "missing_entry":
            manifest["files"] = []
        else:
            manifest["files"][0]["path"] = "../agr/__init__.py"
        path.write_text(json.dumps(manifest))
    assert not bootstrap.verify_program_files(bootstrap.ROOT)
    assert "ERROR: release_manifest.json:" in capsys.readouterr().out


def test_source_check_does_not_touch_cache_environment_or_settings(bootstrap):
    paths = [bootstrap.ROOT / "cache" / "model.bin", bootstrap.ROOT / "settings.json",
             bootstrap.ROOT / ".venv" / "marker.txt"]
    for p in paths:
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"preserve this")
    assert bootstrap.verify_program_files(bootstrap.ROOT)
    assert all(p.read_bytes() == b"preserve this" for p in paths)
