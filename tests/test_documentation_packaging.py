"""User guides must survive source/portable staging and enter the manifest."""
import importlib.util
import json
from pathlib import Path

import pytest
from agr.updater import owned_name


def test_syntax_record_requires_compilable_source_bytes(tmp_path,packager):
    from portable_launcher import python_syntax_record
    root=tmp_path/'app';root.mkdir()
    source=root/'module.py';source.write_bytes(b'def broken(:\n')
    with pytest.raises(SyntaxError):
        packager.write_manifest(root,'fixture','source')
    assert not (root/'release_manifest.json').exists()
    source.write_bytes(b'value = 1\n')
    packager.write_manifest(root,'fixture','source')
    manifest=json.loads((root/'release_manifest.json').read_text())
    assert manifest['python_syntax']==python_syntax_record(manifest['files'])


@pytest.fixture
def packager(monkeypatch):
    folder = Path(__file__).parents[1] / 'tools'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('documentation_packager_test',folder/'package_character_beta.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_guides_and_original_artwork_survive_both_stages(tmp_path,packager):
    root=tmp_path/'checkout';root.mkdir()
    for name in packager.SOURCE_ROOT_FILES:
        (root/name).write_text('root document\n')
    (root/'README.md').write_text('[Guide](docs/README.md) [Map](MAP_WORKFLOW.md)\n'
                                  '<img src="artwork/renderlock-intro.gif">\n', encoding='utf-8')
    for name,contents in [('docs/README.md',b'[User](USER_GUIDE.md) [Home](../README.md)'),
                          ('docs/USER_GUIDE.md',b'[Map](../MAP_WORKFLOW.md)'),
                          ('artwork/renderlockmain.png',b'original artwork fixture'),
                          ('artwork/renderlock-intro.gif',b'GIF89a fixture'),
                          ('artwork/private-source.mp4',b'private source video'),
                          ('docs/.git/private.md',b'private git data'),('docs/private.blend',b'game data')]:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(contents)
    source=tmp_path/'source';source.mkdir()
    packager.stage_source(root,source)
    portable=tmp_path/'portable'
    packager.stage_documentation(source,portable)
    for name in ('docs/USER_GUIDE.md','artwork/renderlockmain.png','artwork/renderlock-intro.gif',
                 'CLOTH_WORKFLOW.md','MAP_WORKFLOW.md','CONTRIBUTING.md'):
        staged, original = portable/'tools'/name, root/name
        if staged.suffix == '.md':
            assert staged.read_text(encoding='utf-8') == original.read_text(encoding='utf-8')
        else:
            assert staged.read_bytes() == original.read_bytes()
    assert (portable/'README.md').read_text(encoding='utf-8') == (
        '[Guide](tools/docs/README.md) [Map](tools/MAP_WORKFLOW.md)\n'
        '<img src="tools/artwork/renderlock-intro.gif">\n')
    assert (portable/'tools/docs/README.md').read_text(encoding='utf-8') == (
        '[User](USER_GUIDE.md) [Home](../../README.md)')
    assert (portable/'tools/docs/USER_GUIDE.md').read_text(encoding='utf-8') == '[Map](../MAP_WORKFLOW.md)'
    assert not (source/'docs/private.blend').exists()
    assert not (portable/'tools/docs/.git').exists()
    assert not (portable/'tools/artwork/private-source.mp4').exists()


def test_portable_documentation_is_owned_by_the_release_manifest(tmp_path,packager):
    source=tmp_path/'source';source.mkdir()
    for name in packager.PORTABLE_DOCUMENTS:
        (source/name).write_text('documentation\n')
    (source/'docs').mkdir();(source/'docs/README.md').write_text('index')
    (source/'artwork').mkdir();(source/'artwork/renderlockmain.png').write_bytes(b'logo')
    portable=tmp_path/'portable';packager.stage_documentation(source,portable)
    packager.write_manifest(portable,'0.3.0b16','Windows-x64-portable')
    manifest=json.loads((portable/'release_manifest.json').read_text())
    records={r['path']:r for r in manifest['files']}
    expected = {name if name in packager.PORTABLE_ROOT_DOCUMENTS else 'tools/'+name
                for name in packager.PORTABLE_DOCUMENTS}
    assert expected|{'tools/docs/README.md','tools/artwork/renderlockmain.png'} == set(records)
    # This is the published b16 updater's unchanged path policy.
    assert all(owned_name(name) for name in records)
    for name,row in records.items():
        assert row['bytes']==(portable/name).stat().st_size
        assert row['sha256']==packager.digest(portable/name)
