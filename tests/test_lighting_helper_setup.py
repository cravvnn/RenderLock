import hashlib
from pathlib import Path

import pytest

from agr.asset_converter import ConversionError
from tools import prepare_lighting_helpers as setup


def test_helper_kit_preserves_identity_and_refuses_overwrite(tmp_path, monkeypatch):
    helpers = []
    for role in ('world', 'atlas', 'cube'):
        folder = tmp_path / ('input-' + role); folder.mkdir()
        helper = folder / (role + '.dll'); helper.write_bytes(role.encode())
        for suffix in ('.deps.json', '.runtimeconfig.json'):
            (folder / (role + suffix)).write_text('{}')
        (folder / 'private.vtex_c').write_bytes(b'must not copy')
        helpers.append(helper)
    dotnet = tmp_path / 'dotnet.exe'; dotnet.write_bytes(b'runtime')
    monkeypatch.setattr(setup, 'validate_hero_decoder_paths', lambda a,c,d: (Path(a),Path(c),Path(d)))
    def region(p,d):
        return [], {q.name: hashlib.sha256(q.read_bytes()).hexdigest()
                    for q in p.parent.iterdir() if q.suffix in ('.dll','.json','.exe')}
    monkeypatch.setattr(setup, 'regional_command', region)
    output = tmp_path / 'kit'
    manifest = setup.prepare_helpers(*helpers,dotnet,output)
    assert len(list(output.rglob('*.vtex_c'))) == 0
    for role in ('world','atlas','cube'):
        assert manifest['helpers'][role]['files'] == region(output/role/(role+'.dll'),dotnet)[1]
    before = (output/'helper-kit.json').read_bytes()
    with pytest.raises(ConversionError,match='already exists'):
        setup.prepare_helpers(*helpers,dotnet,output)
    assert (output/'helper-kit.json').read_bytes() == before
    (helpers[0].parent/'world.runtimeconfig.json').unlink()
    with pytest.raises(ConversionError,match='runtime metadata'):
        setup.prepare_helpers(*helpers,dotnet,tmp_path/'incomplete')
    assert not (tmp_path/'incomplete').exists()
