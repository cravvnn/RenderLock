"""Native path spelling retains source identity and supports long Windows IO."""
import os
from pathlib import Path

from agr.path_io import native_path
from agr.asset_converter import sha


def test_native_path_keeps_long_unicode_file_bytes(tmp_path):
    folder = tmp_path
    for index in range(5):
        folder /= 'unicode café Ω ' + str(index) + 'x' * 35
    folder.mkdir(parents=True)
    path = folder / 'source.bin'
    path.write_bytes(b'qualified source payload')
    assert len(str(path)) > 260
    with open(native_path(path), 'rb') as stream:
        assert stream.read() == b'qualified source payload'
    assert Path(native_path(path)).samefile(path)
    assert sha(native_path(path)) == sha(path)


def test_native_path_is_idempotent_and_preserves_relative_selection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path('relative.dat')
    path.write_bytes(b'relative source')
    native = native_path(path)
    assert native_path(native) == native
    assert Path(native).read_bytes() == b'relative source'
    if os.name == 'nt':
        assert native.startswith('\\\\?\\')
        assert native_path('\\\\server\\share\\image.png') == '\\\\?\\UNC\\server\\share\\image.png'
