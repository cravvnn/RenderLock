"""Run isolated test source with an existing Linux environment and extractor.

Other native tools remain explicit agr options. No files in the old installation
are changed, no packages are installed, and no qualification flags are added.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def launch_spec(root, installed, arguments, environment):
    root, installed = Path(root).resolve(), Path(installed).expanduser().resolve()
    python = installed / '.venv/bin/python'
    extractor = installed / 'bin/agr-extract'
    if not python.is_file() or not extractor.is_file():
        raise ValueError('Existing Linux installation needs .venv/bin/python and bin/agr-extract. '
                         'See TESTING_LINUX.md for a fresh source setup.')
    with extractor.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            raise ValueError('The existing agr-extract must be a native Linux ELF, not a Windows executable.')
    if not arguments:
        raise ValueError('Pass an agr command after --, for example -- --help.')
    env = dict(environment, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1',
               AGR_NATIVE_EXTRACTOR=str(extractor))
    model_extractor = installed / 'tools/particles/ParticleProbe.dll'
    if model_extractor.is_file():
        env['AGR_MODEL_EXTRACTOR'] = str(model_extractor)
    return [str(python), '-B', '-m', 'agr', *arguments], env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-from', type=Path, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('This launcher is for native Linux test installations only.')
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    root = Path(__file__).resolve().parents[1]
    try:
        command, env = launch_spec(root, args.tools_from, arguments, os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    os.chdir(root)
    os.execve(command[0], command, env)


if __name__ == '__main__':
    main()
