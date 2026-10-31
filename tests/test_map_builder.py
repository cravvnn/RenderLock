import hashlib
import io
import stat
import zipfile
import os
import sys

import pytest

from agr import map_builder


@pytest.mark.parametrize('name',['../outside.py','/absolute.py','C:/bad.py','tools\\bad.py','tools/../bad.py'])
def test_helper_archive_cannot_escape_cache(tmp_path,name):
    archive=tmp_path/'tools.zip'
    stored=name.replace(chr(92),'/')
    with zipfile.ZipFile(archive,'w') as stream:stream.writestr(stored,b'code')
    if stored!=name:
        # ZipInfo normalizes host separators on Windows. Construct the actual
        # hostile ZIP member bytes rather than accidentally testing a safe path.
        archive.write_bytes(archive.read_bytes().replace(stored.encode(),name.encode()))
    output=tmp_path/'extract';output.mkdir()
    with pytest.raises(ValueError,match='Map tools:'):
        map_builder._extract_archive(archive,output)
    assert list(output.iterdir())==[]


def test_helper_archive_symlink_is_rejected_before_any_file_is_written(tmp_path):
    archive=tmp_path/'tools.zip'
    with zipfile.ZipFile(archive,'w') as stream:
        stream.writestr('normal.py',b'code')
        link=zipfile.ZipInfo('link.py');link.create_system=3
        link.external_attr=(stat.S_IFLNK|0o777)<<16
        stream.writestr(link,b'outside.py')
    output=tmp_path/'extract';output.mkdir()
    with pytest.raises(ValueError,match='Map tools:'):
        map_builder._extract_archive(archive,output)
    assert list(output.iterdir())==[]


def test_corrupt_or_injected_component_is_preserved_and_replaced_from_verified_archive(tmp_path,monkeypatch):
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as stream:stream.writestr('tools/build.py',b'known source')
    payload=content.getvalue();digest=hashlib.sha256(payload).hexdigest()
    requests=[]
    def download(url,timeout):
        requests.append(url);return io.BytesIO(payload)
    monkeypatch.setattr(map_builder.urllib.request,'urlopen',download)
    folder=map_builder._component(tmp_path,'builder','https://example.test/pinned.zip',digest)
    (folder/'tools/build.py').write_bytes(b'changed source')
    (folder/'tools/injected.py').write_bytes(b'unexpected module')
    restored=map_builder._component(tmp_path,'builder','https://example.test/pinned.zip',digest)
    assert (restored/'tools/build.py').read_bytes()==b'known source'
    assert not (restored/'tools/injected.py').exists()
    preserved=list(tmp_path.glob('builder.preserved-*'))
    assert len(preserved)==1 and (preserved[0]/'tools/injected.py').is_file()
    assert len(requests)==1


def test_wrong_download_hash_never_becomes_an_executable_component(tmp_path,monkeypatch):
    monkeypatch.setattr(map_builder.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'changed remote data'))
    with pytest.raises(ValueError,match='identity'):
        map_builder._component(tmp_path,'builder','https://example.test/pinned.zip','0'*64)
    assert not (tmp_path/'builder').exists()
    assert list(tmp_path.glob('builder.download-*'))


def test_native_child_progress_is_forwarded_without_opening_a_console(capsys):
    map_builder._run([sys.executable,'-c',"print('map stage ready')"],dict(os.environ,PYTHONUTF8='1'))
    assert 'map stage ready' in capsys.readouterr().out


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows image-loader path limit')
def test_long_windows_cache_is_reported_before_reading_game_or_downloading_tools(tmp_path,monkeypatch):
    cache=tmp_path/('nested-cache-'*12)
    def unexpected(*args):raise AssertionError('No game or tool work before path preflight')
    monkeypatch.setattr(map_builder,'installed_fingerprint',unexpected)
    with pytest.raises(ValueError,match='shorter cache folder path'):
        map_builder.prepare_map('unused-game','unused-blender',cache)
    assert not cache.exists()
