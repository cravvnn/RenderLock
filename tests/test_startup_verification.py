import hashlib
import json

import pytest

import portable_launcher as launcher
from test_portable_launcher import installation


def qualify(root):
    path=root/'release_manifest.json'
    manifest=json.loads(path.read_text())
    # Mirror the actual packager's prerequisite: compile all source first.
    for row in manifest['files']:
        if row['path'].endswith('.py'):
            compile((root/row['path']).read_bytes(),row['path'],'exec',dont_inherit=True)
    manifest['python_syntax']=launcher.python_syntax_record(manifest['files'])
    path.write_text(json.dumps(manifest))
    return manifest


def test_qualified_sources_are_still_hashed_without_recompiling(installation,monkeypatch):
    qualify(installation)
    original=launcher._verify_file
    checks=[]
    def check(root,entry,compile_source):
        checks.append((entry['path'],compile_source))
        return original(root,entry,compile_source)
    monkeypatch.setattr(launcher,'_verify_file',check)
    launcher.verify(installation)
    assert len(checks)==7 and not any(compile_source for _,compile_source in checks)
    target=installation/'agr/gui.py'
    raw=target.read_bytes();target.write_bytes(b'x'*len(raw))
    with pytest.raises(ValueError,match='Changed file: agr/gui.py'):
        launcher.verify(installation)


@pytest.mark.parametrize('change',['source','interpreter','absent'])
def test_missing_or_stale_qualification_falls_back_to_syntax_validation(installation,change):
    qualify(installation)
    path=installation/'release_manifest.json'
    manifest=json.loads(path.read_text())
    target=installation/'agr/gui.py';target.write_bytes(b'def broken(:\n')
    row=next(row for row in manifest['files'] if row['path']=='agr/gui.py')
    row.update(bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    if change=='interpreter':
        manifest['python_syntax']=dict(launcher.python_syntax_record(manifest['files']),interpreter='another Python')
    elif change=='absent':
        manifest.pop('python_syntax')
    path.write_text(json.dumps(manifest))
    with pytest.raises(SyntaxError):
        launcher.verify(installation)


@pytest.mark.parametrize('path',['C:/outside.py','agr//gui.py','agr/./gui.py','../gui.py','/gui.py','agr/gui.py:stream'])
def test_invalid_manifest_paths_fail_before_workers_read_files(installation,path):
    manifest_path=installation/'release_manifest.json'
    manifest=json.loads(manifest_path.read_text());manifest['files'][0]['path']=path
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Invalid manifest path'):
        launcher.verify(installation)
