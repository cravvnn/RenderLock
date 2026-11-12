"""Optional actual helper contract checks; no game installation required."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module')
def helper():
    dll = os.environ.get('RENDERLOCK_GEOMETRY_HELPER')
    dotnet = os.environ.get('RENDERLOCK_GEOMETRY_DOTNET')
    if not dll or not dotnet:
        pytest.skip('Set RENDERLOCK_GEOMETRY_HELPER and RENDERLOCK_GEOMETRY_DOTNET for actual helper checks')
    assert Path(dll).is_file() and Path(dotnet).is_file()
    return [dotnet, dll]


def run(helper, *args):
    return subprocess.run(helper + list(map(str, args)), capture_output=True,
                          text=True, timeout=30)


def test_geometry_capability_keeps_materials_separate(helper):
    result = run(helper, '--capabilities')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(policy='agr_particle_model_morph_v1',
        upstream_revision='42743fb93030c552f834fabb30708dad84d0cbf5',
        patches=['vrf-skeletonless-morph-animation.patch','vrf-kv3-v5-unsigned-constants.patch'],
        commands=['particle-model-v1','particle-authored-model-v1','particle-static-model-v1',
            'inventory-particle-model-materials','inventory-particle-owner-model','particle-texture-sheet-v1'],
        material_exported=False)


@pytest.mark.parametrize('resource', ['../bat.vmdl', '/bat.vmdl', 'a//bat.vmdl',
    'a/./bat.vmdl', 'C:/bat.vmdl', 'a\\bat.vmdl', 'bat.vmdl_c', 'a/\nbat.vmdl'])
@pytest.mark.parametrize('command',['--particle-model-v1','--particle-static-model-v1','--particle-authored-model-v1'])
def test_invalid_resource_is_rejected_before_loading_or_output(helper, tmp_path, resource,command):
    output = tmp_path / 'output'
    result = run(helper, command, tmp_path / 'absent-gameinfo.gi', resource, output)
    assert result.returncode != 0
    assert 'relative particle model' in result.stderr
    assert not output.exists()


@pytest.mark.parametrize('file_destination', [False, True])
@pytest.mark.parametrize('command',['--particle-model-v1','--particle-static-model-v1','--particle-authored-model-v1'])
def test_existing_output_is_preserved_before_loading(helper, tmp_path, file_destination,command):
    output = tmp_path / 'output'
    if file_destination:
        output.write_bytes(b'preserve existing file')
        sentinel = output
    else:
        output.mkdir()
        sentinel = output / 'model.json'
        sentinel.write_bytes(b'preserve existing descriptor')
    before = sentinel.read_bytes()
    result = run(helper, command, tmp_path / 'absent-gameinfo.gi', 'bat.vmdl', output)
    assert result.returncode != 0 and 'output must be empty' in result.stderr
    assert sentinel.read_bytes() == before
