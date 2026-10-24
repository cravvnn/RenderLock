"""User guides must survive source/portable staging and enter the manifest."""
import importlib.util
import json
from pathlib import Path

import pytest


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
    for name,contents in [('docs/README.md',b'guide index'),('docs/USER_GUIDE.md',b'user guide'),
                          ('artwork/renderlockmain.png',b'original artwork fixture'),
                          ('artwork/renderlock-intro.gif',b'GIF89a fixture'),
                          ('artwork/private-source.mp4',b'private source video'),
                          ('docs/.git/private.md',b'private git data'),('docs/private.blend',b'game data')]:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(contents)
    source=tmp_path/'source';source.mkdir()
    packager.stage_source(root,source)
    portable=tmp_path/'portable'
    packager.stage_documentation(source,portable)
    for name in ('docs/README.md','docs/USER_GUIDE.md','artwork/renderlockmain.png','artwork/renderlock-intro.gif',
                 'CLOTH_WORKFLOW.md','MAP_WORKFLOW.md','CONTRIBUTING.md'):
        assert (portable/name).read_bytes()==(root/name).read_bytes()
    assert not (source/'docs/private.blend').exists()
    assert not (portable/'docs/.git').exists()
    assert not (portable/'artwork/private-source.mp4').exists()


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
    assert set(packager.PORTABLE_DOCUMENTS)|{'docs/README.md','artwork/renderlockmain.png'} == set(records)
    for name,row in records.items():
        assert row['bytes']==(portable/name).stat().st_size
        assert row['sha256']==packager.digest(portable/name)
