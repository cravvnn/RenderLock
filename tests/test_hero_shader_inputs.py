"""Hero conversions require shader inputs even though RERL does not list them."""
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from agr.asset_converter import AssetConverter, ConversionError, sha


def fixture(tmp_path, monkeypatch, warning=''):
    names = ['models/hero.vmdl_c', 'shaders/vulkan/pbr_features.vcs']
    files = {}
    for index, name in enumerate(names):
        files[name] = tmp_path / str(index)
        files[name].write_bytes(f'input {index}'.encode())
    def resolve(name):
        path = files[name]
        return SimpleNamespace(resource_path=name, local_path=path,
                               sha256=sha(path), dependencies=[])
    provider = SimpleNamespace(resources=names, resolve=resolve)
    decoder = AssetConverter.__new__(AssetConverter)
    decoder.root = tmp_path / 'derived'
    decoder.root.mkdir()
    decoder.executable = tmp_path / 'converter.exe'
    decoder.identity = {'fixture': True}
    decoder.stats = {'cache_hits': 0, 'conversions': 0}
    decoder.log = lambda _: None
    calls = []
    def export(command, **kwargs):
        target = Path(command[command.index('-o') + 1])
        shader = target.parent / 'input' / names[1]
        assert shader.read_bytes() == files[names[1]].read_bytes()
        calls.append(shader.read_bytes())
        doc = {'meshes': [{}], 'skins': [{'joints': [1]}],
               'nodes': [{'mesh': 0, 'skin': 0}, {'name': 'root'}]}
        raw = json.dumps(doc).encode()
        raw += b' ' * (-len(raw) % 4)
        target.write_bytes(struct.pack('<4sIII4s', b'glTF', 2, 20 + len(raw), len(raw), b'JSON') + raw)
        return SimpleNamespace(returncode=0, stdout=warning, stderr='')
    monkeypatch.setattr('agr.asset_converter.subprocess.run', export)
    return decoder, provider, files, calls


def test_shader_payload_is_copied_and_invalidates_hero_cache(tmp_path, monkeypatch):
    decoder, provider, files, calls = fixture(tmp_path, monkeypatch)
    first, report = decoder.model('models/hero.vmdl', provider)
    assert report['compiled_shader_resources'] == 1
    shader = 'shaders/vulkan/pbr_features.vcs'
    assert report['inputs'][shader] == sha(files[shader])
    assert decoder.model('models/hero.vmdl', provider)[0] == first
    assert len(calls) == 1
    files[shader].write_bytes(b'updated shader without model changes')
    second, _ = decoder.model('models/hero.vmdl', provider)
    assert first != second and len(calls) == 2
    assert first.is_file()  # keep prior verified cache


def test_missing_shader_warning_rejects_otherwise_valid_hero(tmp_path, monkeypatch):
    decoder, provider, _, _ = fixture(tmp_path, monkeypatch, 'Failed to find shader "pbr.vfx"')
    with pytest.raises(ConversionError, match='material shaders: pbr.vfx'):
        decoder.model('models/hero.vmdl', provider)
    assert not list(decoder.root.glob('*/conversion.json'))
