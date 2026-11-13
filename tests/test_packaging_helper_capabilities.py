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


def test_helper_without_world_origin_command_cannot_stage_release(packager, helper, tmp_path, monkeypatch):
    commands=sorted(packager.PARTICLE_COMMANDS-{'simulate-world-origin-v1'})
    monkeypatch.setattr(packager.subprocess,'run',lambda *a,**k:
        subprocess.CompletedProcess(a,0,json.dumps(dict(schema_version=1,commands=commands)),''))
    native=tmp_path/'native.exe';native.write_bytes(b'fixture')
    output=tmp_path/'candidate'
    with pytest.raises(ValueError,match='capability check failed'):
        packager.package(tmp_path,output,native,particles_dir=helper[0],dotnet_runtime=helper[1])
    assert not output.exists()


@pytest.fixture
def isolated_helpers(tmp_path, packager, monkeypatch):
    simulation, geometry, runtime = [tmp_path / name for name in ('simulation', 'geometry', 'runtime')]
    runtime.mkdir(); (runtime / 'dotnet.exe').write_bytes(b'runtime fixture')
    for folder, dll in [(simulation, 'ParticleSimulationProbe'), (geometry, 'ParticleGeometryProbe')]:
        folder.mkdir()
        for name in [dll+'.dll', dll+'.deps.json', dll+'.runtimeconfig.json',
                     'ValveResourceFormat.dll', 'libSkiaSharp.dll', 'spirv-cross.dll']:
            (folder / name).write_bytes(b'typed fixture')
    real_digest = packager.digest
    def digest(path):
        if path.name == 'ValveResourceFormat.dll':
            return ('2fa0d37b732c457cc839dde6f3db7736fd3d3e76fb4ded287c90fad98927f608'
                    if path.parent == simulation else
                    '5c37d80d5962445ff9754897e695b50b0873b24dda7bca02c4e8868466a5e90e')
        return real_digest(path)
    monkeypatch.setattr(packager, 'digest', digest)
    return simulation, geometry, runtime


def test_geometry_without_rest_inventory_refuses(packager, isolated_helpers, monkeypatch):
    def run(command, **kwargs):
        commands = (sorted(packager.PARTICLE_COMMANDS) if 'Simulation' in command[1] else
                    ['particle-model-v1', 'particle-authored-model-v1', 'particle-static-model-v1',
                     'particle-texture-sheet-v1', 'inventory-particle-owner-model', 'inventory-particle-model-materials'])
        return subprocess.CompletedProcess(command, 0, json.dumps({'commands': commands}), '')
    monkeypatch.setattr(packager.subprocess, 'run', run)
    with pytest.raises(ValueError, match='geometry helper capability check failed'):
        packager.verify_isolated_particle_helpers(*isolated_helpers)


@pytest.mark.parametrize('missing', ['libSkiaSharp.dll', 'spirv-cross.dll', 'ParticleSimulationProbe.deps.json'])
def test_isolated_runtime_requires_complete_dependencies(packager, isolated_helpers, missing):
    (isolated_helpers[0] / missing).unlink()
    with pytest.raises(ValueError, match='Incomplete simulation helper'):
        packager.verify_isolated_particle_helpers(*isolated_helpers)


def test_isolated_parsers_cannot_replace_material_parser(packager, isolated_helpers, monkeypatch):
    monkeypatch.setattr(packager, 'digest', lambda _: 'cbc14ee90f4fa7481448721c6e0a66a1c153ccd191f9c1deb977c3abb6466cd6')
    with pytest.raises(ValueError, match='Unqualified simulation parser'):
        packager.verify_isolated_particle_helpers(*isolated_helpers)


def test_runtime_pair_is_required_before_output(packager, helper, tmp_path, monkeypatch):
    probe, runtime = helper
    monkeypatch.setattr(packager.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps({'schema_version': 1, 'commands': sorted(packager.PARTICLE_COMMANDS)}), ''))
    native=tmp_path/'native.exe'; native.write_bytes(b'fixture')
    output=tmp_path/'candidate'
    with pytest.raises(ValueError, match='Separate simulation, geometry'):
        packager.package(tmp_path, output, native, particles_dir=probe, dotnet_runtime=runtime)
    assert not output.exists()
