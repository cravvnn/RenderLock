"""A stale helper must fail before a candidate is staged or archived."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def packager(monkeypatch):
    folder = Path(__file__).parents[1] / "tools"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location("character_beta_packager_test", folder / "package_character_beta.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper(tmp_path, packager, monkeypatch):
    probe = tmp_path / "particles"
    runtime = tmp_path / "dotnet"
    probe.mkdir(); runtime.mkdir()
    (probe / "ParticleProbe.dll").write_bytes(b"helper fixture")
    (runtime / "dotnet.exe").write_bytes(b"runtime fixture")
    (probe / "ValveResourceFormat.dll").write_bytes(b"qualified parser fixture")
    (probe / "libSkiaSharp.dll").write_bytes(b"native image fixture")
    from agr import model_material_groups
    monkeypatch.setattr(model_material_groups, "PARSER_SHA256", packager.digest(probe / "ValveResourceFormat.dll"))
    return probe, runtime


@pytest.mark.parametrize("reply", [
    "not JSON", "[]", '{"schema_version":1,"commands":["decompile-cloth"]}',
    '{"schema_version":0,"commands":[]}', '{"schema_version":1,"commands":[1]}',
])
def test_stale_helper_cannot_create_candidate(packager, helper, tmp_path, monkeypatch, reply):
    probe, runtime = helper
    native = tmp_path / "native.exe"; native.write_bytes(b"native")
    output = tmp_path / "candidate"
    monkeypatch.setattr(packager.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, reply, ""))
    with pytest.raises(ValueError, match="capability check failed"):
        packager.package(tmp_path, output, native, particles_dir=probe, dotnet_runtime=runtime)
    assert not output.exists()


def test_capability_launch_failure_refuses(packager, helper, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(packager.subprocess, "run", fail)
    with pytest.raises(ValueError, match="capability check failed"):
        packager.verify_particle_helper(*helper)


def test_required_commands_and_exact_helper_are_recorded(packager, helper, monkeypatch):
    probe, runtime = helper
    capabilities = dict(schema_version=1, commands=sorted(packager.PARTICLE_COMMANDS))
    def run(command, **kwargs):
        assert command == [str(runtime / "dotnet.exe"), str(probe / "ParticleProbe.dll"), "--capabilities-v1"]
        assert kwargs["timeout"] == 30
        assert kwargs["env"]["DOTNET_ROOT"] == str(runtime)
        return subprocess.CompletedProcess(command, 0, json.dumps(capabilities), "")
    monkeypatch.setattr(packager.subprocess, "run", run)
    result = packager.verify_particle_helper(probe, runtime)
    assert result == dict(sha256=packager.digest(probe / "ParticleProbe.dll"), capabilities=capabilities,
                          parser_sha256=packager.digest(probe / "ValveResourceFormat.dll"),
                          native_image_sha256=packager.digest(probe / "libSkiaSharp.dll"))


def test_runtime_is_required(packager, helper):
    with pytest.raises(ValueError, match="bundled .NET runtime"):
        packager.verify_particle_helper(helper[0], None)


@pytest.mark.parametrize("filename, message", [
    ("ValveResourceFormat.dll", "qualified material parser"),
    ("libSkiaSharp.dll", "native image decoder"),
])
def test_incomplete_or_wrong_dependencies_refuse(packager, helper, filename, message):
    (helper[0] / filename).unlink()
    with pytest.raises(ValueError, match=message):
        packager.verify_particle_helper(*helper)


def test_packager_cli_bootstraps_outside_project_directory(tmp_path):
    script = Path(__file__).parents[1] / "tools/package_character_beta.py"
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "--particles-dir" in result.stdout
