from pathlib import Path
import pytest
from agr.particle_runtime import particle_command, particle_environment
from agr import gui


def test_portable_runtime_ignores_stale_dotnet_and_preserves_argument_boundaries(tmp_path, monkeypatch):
    probe = tmp_path / 'space & (portable)' / 'ParticleProbe.exe'
    probe.parent.mkdir()
    probe.touch()
    assert particle_command(probe, 'missing dotnet') == [str(probe.resolve())]
    monkeypatch.setenv('DOTNET_ROOT', 'existing environment')
    assert particle_environment(probe)['DOTNET_ROOT'] == 'existing environment'


def test_dll_requires_explicit_existing_host(tmp_path):
    probe = tmp_path / 'ParticleProbe.dll'
    probe.touch()
    with pytest.raises(ValueError, match='.NET'):
        particle_command(probe)
    host = tmp_path / 'dotnet.exe'
    host.touch()
    assert particle_command(probe, host) == [str(host.resolve()), str(probe.resolve())]
    assert particle_environment(probe, host)['DOTNET_ROOT'] == str(tmp_path.resolve())


@pytest.mark.parametrize('name', ['missing.exe', 'ParticleProbe.cmd', 'ParticleProbe.json'])
def test_runtime_rejects_missing_or_non_executable_input(tmp_path, name):
    probe = tmp_path / name
    if name != 'missing.exe':
        probe.touch()
    with pytest.raises(ValueError, match='ParticleProbe'):
        particle_command(probe)


def test_gui_prefers_bundled_portable_runtime(tmp_path, monkeypatch):
    probe = tmp_path / 'tools/particles/ParticleProbe.exe'
    probe.parent.mkdir(parents=True)
    probe.touch()
    monkeypatch.setattr(gui, 'PROGRAM_ROOT', tmp_path)
    monkeypatch.setattr(gui.sys, 'platform', 'win32')
    assert gui.discover_particle_runtime() == (str(probe.resolve()), '')
