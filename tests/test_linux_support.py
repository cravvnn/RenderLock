"""Bounded Linux integration contracts; no native tool or desktop is launched."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agr import cli, game_assets, gui, source_lightmap_assets as lightmap
from agr.asset_converter import ConversionError
from agr.helper_files import helper_file_inventory
from tools import prepare_lighting_helpers as setup


def test_native_companion_inventory_changes_with_linux_payload(tmp_path):
    for name in ('Helper.dll', 'Helper.deps.json', 'libSkiaSharp.so', 'libother.so.1.2',
                 'libother.dylib', 'native.dll', 'private.vtex_c', 'lib.so.backup'):
        (tmp_path / name).write_bytes(name.encode())
    before = helper_file_inventory(tmp_path)
    assert set(before) == {'Helper.dll', 'Helper.deps.json', 'libSkiaSharp.so',
                           'libother.so.1.2', 'libother.dylib', 'native.dll'}
    (tmp_path / 'libSkiaSharp.so').write_bytes(b'changed native decoder')
    after = helper_file_inventory(tmp_path)
    assert before['libSkiaSharp.so'] != after['libSkiaSharp.so']
    assert {k:v for k,v in before.items() if k != 'libSkiaSharp.so'} == {
        k:v for k,v in after.items() if k != 'libSkiaSharp.so'}


def test_companion_outside_source_is_not_copied_or_cached(tmp_path):
    root = tmp_path / 'kit'; root.mkdir()
    outside = tmp_path / 'outside.so'; outside.write_bytes(b'outside')
    try: (root / 'libSkiaSharp.so').symlink_to(outside)
    except OSError as e: pytest.skip('Host cannot create file symlinks: ' + str(e))
    with pytest.raises(ConversionError, match='escapes'):
        helper_file_inventory(root)


def test_linux_native_libraries_survive_helper_preparation(tmp_path, monkeypatch):
    helpers = []
    for role in ('world', 'atlas', 'cube'):
        directory = tmp_path / role; directory.mkdir()
        helper = directory / (role + '.dll'); helper.write_bytes(role.encode())
        for suffix in ('.deps.json', '.runtimeconfig.json'):
            (directory / (role + suffix)).write_text('{}')
        (directory / 'libSkiaSharp.so').write_bytes(('native-' + role).encode())
        helpers.append(helper)
    dotnet = tmp_path / 'dotnet'; dotnet.touch()
    monkeypatch.setattr(setup, 'validate_hero_decoder_paths', lambda a,c,d: (Path(a),Path(c),Path(d)))
    monkeypatch.setattr(setup, 'regional_command', lambda p,d: ([],helper_file_inventory(Path(p).parent)))
    out = tmp_path / 'prepared'
    manifest = setup.prepare_helpers(*helpers, dotnet, out)
    for role in ('world', 'atlas', 'cube'):
        assert (out / role / 'libSkiaSharp.so').read_bytes() == ('native-' + role).encode()
        assert manifest['helpers'][role]['files']['libSkiaSharp.so'] == hashlib.sha256(
            ('native-' + role).encode()).hexdigest()


@pytest.mark.parametrize('platform,name', [('linux','libSkiaSharp.so'),('win32','libSkiaSharp.dll'),
                                          ('darwin','libSkiaSharp.dylib')])
def test_decoder_identity_uses_actual_platform_companion(monkeypatch, platform, name):
    monkeypatch.setattr(lightmap.sys, 'platform', platform)
    decoder = SimpleNamespace(identity={'executable_sha256':'executable',
        'companions':{'libSkiaSharp.so':'elf','libSkiaSharp.dll':'pe','libSkiaSharp.dylib':'macho'}})
    assert lightmap.image_decoder_identity(decoder) == ('executable',decoder.identity['companions'][name])
    assert lightmap.image_decoder_identity(decoder, executable_sha256='fresh') == (
        'fresh',decoder.identity['companions'][name])


def test_missing_linux_companion_cannot_fall_back_to_windows_or_none(monkeypatch):
    monkeypatch.setattr(lightmap.sys, 'platform', 'linux')
    decoder = SimpleNamespace(identity={'executable_sha256':lightmap.QUALIFIED_CONVERTER_20_1_SHA256,
        'companions':{'libSkiaSharp.dll':lightmap.QUALIFIED_SKIA_20_1_SHA256}})
    identity = lightmap.image_decoder_identity(decoder)
    assert identity[1] is None and identity not in lightmap.QUALIFIED_DECODER_PAIRS
    assert all(executable and native for executable,native in lightmap.QUALIFIED_DECODER_PAIRS)


def test_reported_linux_pair_still_requires_decoder_qualification(monkeypatch):
    from agr import source_npr_assets as npr
    monkeypatch.setattr(lightmap.sys, 'platform', 'linux')
    executable='3aaa77568beaf6b130d974993a42088d8ce58b2e001fdda42a2fd8aae58ffb4d'
    native='5039f4daa53be6fdf1742af522c24e3b77d5e7653936cc87ee09b9603cbdee53'
    monkeypatch.setattr(npr, 'sha', lambda path: executable)
    decoder=SimpleNamespace(executable='not-executed',identity={'companions':{'libSkiaSharp.so':native}})
    plan={'status':'sources_resolved_not_decoded','materials':[{}]}
    with pytest.raises(ConversionError,match='requires requalification'):
        npr.decode_npr_material_assets(plan,SimpleNamespace(),decoder)


def test_linux_discovery_does_not_select_packaged_windows_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.sys, 'platform', 'linux')
    monkeypatch.setattr(cli, '__file__', str(tmp_path / 'agr/cli.py'))
    monkeypatch.setattr(cli.shutil, 'which', lambda name: None)
    folder = tmp_path / 'tools/source2viewer'; folder.mkdir(parents=True)
    (folder / 'Source2Viewer-CLI.exe').touch()
    assert cli.discover_source2viewer() is None
    native = folder / 'Source2Viewer-CLI'; native.touch()
    assert cli.discover_source2viewer() == native.resolve()


def test_linux_blender_user_install_without_path_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(gui.sys, 'platform', 'linux')
    monkeypatch.setattr(gui.shutil, 'which', lambda name: None)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    native = tmp_path / '.local/bin/blender'; native.parent.mkdir(parents=True); native.touch()
    assert gui.discover_blender() == str(native.resolve())


def test_flatpak_steam_root_in_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('DEADLOCK_INSTALL', raising=False)
    wanted = tmp_path / '.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Deadlock'
    # Restrict admission to this one synthetic root; no installed files are read.
    def normalize(path):
        if Path(path) != wanted: raise game_assets.GameAssetError('Not installed')
        return wanted
    monkeypatch.setattr(game_assets, 'normalize_install_path', normalize)
    # Exercise the non-Windows discovery branch without changing pathlib's host.
    monkeypatch.setattr(game_assets, 'os', SimpleNamespace(name='posix',environ={}))
    assert game_assets.discover_installations() == [wanted]
