"""Commands for explicitly selected DLL or portable particle simulators."""
from pathlib import Path
import os


def particle_command(probe, dotnet=None):
    path = Path(probe).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {'.dll', '.exe'}:
        raise ValueError('Choose an existing AGR ParticleProbe.dll or portable ParticleProbe.exe.')
    if path.suffix.lower() == '.exe':
        return [str(path)]
    if not dotnet or not Path(dotnet).is_file():
        raise ValueError('Choose the .NET executable for the particle simulator DLL.')
    return [str(Path(dotnet).resolve()), str(path)]


def particle_environment(probe, dotnet=None):
    env = dict(os.environ)
    if Path(probe).suffix.lower() == '.dll':
        if not dotnet:
            raise ValueError('Particle simulator DLL requires .NET.')
        env['DOTNET_ROOT'] = str(Path(dotnet).resolve().parent)
    return env
