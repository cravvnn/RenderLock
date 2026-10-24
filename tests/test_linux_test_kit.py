"""The handoff borrows dependencies without patching an old installation."""
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.run_linux_test import launch_spec


def installation(tmp_path, header=b'\x7fELF'):
    app = tmp_path / 'existing app'
    (app / '.venv/bin').mkdir(parents=True)
    (app / '.venv/bin/python').write_bytes(b'interpreter fixture')
    (app / 'bin').mkdir()
    (app / 'bin/agr-extract').write_bytes(header + b'fixture')
    (app / 'tools/particles').mkdir(parents=True)
    (app / 'tools/particles/ParticleProbe.dll').write_bytes(b'managed fixture')
    return app


def test_launcher_keeps_explicit_arguments_and_uses_new_source(tmp_path):
    app = installation(tmp_path)
    root = tmp_path / 'new source'; root.mkdir()
    env = {'PYTHONPATH': 'old source', 'HOME': 'preserved', 'AGR_NATIVE_EXTRACTOR': 'wrong'}
    args = ['convert', '--demo', 'a replay.dem', '--output', 'new scene.blend', '--no-effects']
    before = {p: p.read_bytes() for p in app.rglob('*') if p.is_file()}
    command, selected = launch_spec(root, app, args, env)
    assert command == [str(app / '.venv/bin/python'), '-B', '-m', 'agr', *args]
    assert selected['PYTHONPATH'] == str(root.resolve())
    assert selected['AGR_NATIVE_EXTRACTOR'] == str(app / 'bin/agr-extract')
    assert selected['AGR_MODEL_EXTRACTOR'] == str(app / 'tools/particles/ParticleProbe.dll')
    assert selected['PYTHONDONTWRITEBYTECODE'] == '1'
    assert selected['HOME'] == env['HOME']
    assert env['PYTHONPATH'] == 'old source'
    assert '--partial' not in command and '--partial-lighting' not in command
    assert before == {p: p.read_bytes() for p in app.rglob('*') if p.is_file()}


@pytest.mark.parametrize('missing', ['.venv/bin/python', 'bin/agr-extract'])
def test_launcher_refuses_incomplete_existing_installation(tmp_path, missing):
    app = installation(tmp_path); (app / missing).unlink()
    with pytest.raises(ValueError, match='Existing Linux installation needs'):
        launch_spec(tmp_path, app, ['--help'], {})


@pytest.mark.parametrize('header', [b'MZxx', b'', b'#! /'])
def test_launcher_refuses_non_native_extractors(tmp_path, header):
    with pytest.raises(ValueError, match='native Linux ELF'):
        launch_spec(tmp_path, installation(tmp_path, header), ['--help'], {})


def test_launcher_requires_explicit_command(tmp_path):
    with pytest.raises(ValueError, match='Pass an agr command'):
        launch_spec(tmp_path, installation(tmp_path), [], {})


def test_linux_source_archive_is_complete_and_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'tools'))
    from package_character_beta import SOURCE_ROOT_FILES
    from package_installed_release import validate_archive
    from package_linux_test import package
    root = tmp_path / 'checkout'; root.mkdir()
    for name in SOURCE_ROOT_FILES:
        (root / name).write_text('# documentation fixture\n', encoding='utf-8')
    (root / 'launch_renderlock.sh').write_bytes(b'#!/usr/bin/env bash\r\nset -euo pipefail\r\n')
    for name, data in [('agr/__init__.py', b'__version__ = "0.3.0b17.dev1"\n'),
                       ('tools/run_linux_test.py', b'print("launcher fixture")\n'),
                       ('artwork/renderlock-intro.gif', b'GIF89a'),
                       ('agr/__pycache__/private.py', b'private cache'),
                       ('agr/private.blend', b'private asset')]:
        p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    output = tmp_path / 'candidate'
    result = package(root, output, '0.3.0b17.dev1')
    manifest = validate_archive(Path(result['archive']['path']), 'RenderLock-0.3.0b17.dev1-Linux-test-source')
    assert manifest['kind'] == 'Linux-experimental-test-source'
    assert result['runtime_bundled'] is False
    paths = {r['path'] for r in manifest['files']}
    assert {'TESTING_LINUX.md', 'LINUX.md', 'tools/run_linux_test.py', 'artwork/renderlock-intro.gif'} <= paths
    assert not any('private' in p for p in paths)
    with ZipFile(result['archive']['path']) as archive:
        info = archive.getinfo('RenderLock-0.3.0b17.dev1-Linux-test-source/launch_renderlock.sh')
        assert b'\r' not in archive.read(info)
        assert info.create_system == 3
        assert (info.external_attr >> 16) & 0o777 == 0o755
    assert json.loads((output / 'packaging-receipt.json').read_text()) == result
    with pytest.raises(FileExistsError):
        package(root, output, '0.3.0b17.dev1')
