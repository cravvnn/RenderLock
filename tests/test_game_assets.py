"""VPK, dependency closure and Steam discovery checks without a game install."""
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

import pytest

from agr.game_assets import (GameAssetError, GameAssetProvider, MissingResourceError,
                             VPKArchive, discover_installations, normalize_install_path,
                             resource_references, safe_resource_path)


def compiled(*references):
    strings = bytearray()
    entries = bytearray()
    for index, name in enumerate(references):
        pointer_position = 8 + 16 * index + 8
        name_position = 8 + 16 * len(references) + len(strings)
        entries += struct.pack("<Qq", index, name_position - pointer_position)
        strings += name.encode() + b"\0"
    rerl = struct.pack("<II", 8, len(references)) + entries + strings
    return (struct.pack("<IHHII", 28 + len(rerl), 12, 1, 8, 1)
            + struct.pack("<4sII", b"RERL", 8, len(rerl)) + rerl)


def write_vpk(path, resources, *, version=2, preload=0, external=False):
    """Independent fixture writer: payloads include both preload and body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    grouped = {}
    for name, data in resources.items():
        p = Path(name)
        extension = p.suffix[1:] or " "
        directory = p.parent.as_posix() if p.parent.as_posix() != "." else " "
        grouped.setdefault(extension, {}).setdefault(directory, []).append((p.stem if p.suffix else p.name, data))
    tree, body = bytearray(), bytearray()
    for extension, directories in grouped.items():
        tree += extension.encode() + b"\0"
        for directory, files in directories.items():
            tree += directory.encode() + b"\0"
            for name, data in files:
                count = min(preload, len(data))
                tree += name.encode() + b"\0"
                tree += struct.pack("<IHHIIH", zlib.crc32(data), count, 0 if external else 0x7FFF,
                                    len(body), len(data) - count, 0xFFFF)
                tree += data[:count]
                body += data[count:]
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"
    header = struct.pack("<III", 0x55AA1234, version, len(tree))
    if version == 2:
        header += struct.pack("<IIII", 0 if external else len(body), 0, 0, 0)
    path.write_bytes(header + tree + (b"" if external else body))
    if external:
        path.with_name(path.name[:-8] + "_000.vpk").write_bytes(body)


def installation(tmp_path, resources, **kwargs):
    root = tmp_path / "separate drive with spaces" / "Deadlock"
    write_vpk(root / "game/citadel/pak01_dir.vpk", resources, **kwargs)
    return root


def test_resource_identity_does_not_materialize_payloads(tmp_path):
    payload=compiled('materials/test.vmat')
    root=installation(tmp_path,{'models/test.vmdl_c':payload},preload=5,external=True)
    cache=tmp_path/'cache'
    provider=GameAssetProvider(root,cache)
    identity=provider.resource_identity('models/test.vmdl')
    assert identity=={'resource_path':'models/test.vmdl_c','sha256':hashlib.sha256(payload).hexdigest(),
                      'archive':str(root/'game/citadel/pak01_dir.vpk')}
    assert not cache.exists()


@pytest.mark.parametrize("version,external,preload", [(1, False, 0), (2, False, 5), (1, True, 3), (2, True, 999)])
def test_vpk_versions_numbered_chunks_and_preload(tmp_path, version, external, preload):
    root = installation(tmp_path, {"models/heroes/test.vmdl_c": compiled(), "readme": b"plain"},
                        version=version, external=external, preload=preload)
    provider = GameAssetProvider(root, tmp_path / "cache")
    model = provider.resolve("Models\\Heroes\\TEST.vmdl")
    assert model.local_path.read_bytes() == compiled()
    assert model.sha256 == hashlib.sha256(compiled()).hexdigest()
    assert provider.resolve("readme").local_path.read_bytes() == b"plain"
    assert model.resource_path == "models/heroes/test.vmdl_c"
    assert normalize_install_path(root / "game/citadel/pak01_dir.vpk") == root


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("content", [b"", b"shader configuration entirely in the directory\n"])
def test_directory_only_entry_ignores_unused_offset(tmp_path, version, external, content):
    """No payload exists at EntryOffset when EntryLength is zero."""
    path = tmp_path / 'pak01_dir.vpk'
    resource = 'shaders/vfx/black_unlit.ini'
    write_vpk(path, {resource: content}, version=version, external=external, preload=65535)
    data = bytearray(path.read_bytes())
    entry = (12 if version == 1 else 28) + len(b'ini\0shaders/vfx\0black_unlit\0')
    struct.pack_into('<I', data, entry + 8, 0xFFFFFFFF)
    path.write_bytes(data)
    if external:
        path.with_name('pak01_000.vpk').unlink()
    archive = VPKArchive(path)
    assert archive.entries[resource].length == 0
    assert archive.read(archive.entries[resource]) == content


def test_directory_only_shader_does_not_block_installed_model(tmp_path):
    root = installation(tmp_path, {'shaders/vfx/black_unlit.ini': b'preloaded shader ini',
                                   'models/test.vmdl_c': compiled()}, preload=65535)
    path = root / 'game/citadel/pak01_dir.vpk'
    data = bytearray(path.read_bytes())
    entry = 28 + len(b'ini\0shaders/vfx\0black_unlit\0')
    struct.pack_into('<I', data, entry + 8, 0xFFFFFFFF)
    path.write_bytes(data)
    provider = GameAssetProvider(root, tmp_path / 'cache')
    assert provider.resolve('models/test.vmdl').local_path.read_bytes() == compiled()
    assert provider.resolve('shaders/vfx/black_unlit.ini').local_path.read_bytes() == b'preloaded shader ini'


def test_directory_only_crc_and_nonempty_embedded_bounds_stay_checked(tmp_path):
    path = tmp_path / 'pak01_dir.vpk'
    resource = 'shaders/vfx/black_unlit.ini'
    entry = 28 + len(b'ini\0shaders/vfx\0black_unlit\0')
    write_vpk(path, {resource: b'preloaded'}, preload=65535)
    data = bytearray(path.read_bytes())
    struct.pack_into('<I', data, entry + 8, 0xFFFFFFFF)
    data[entry + 18] ^= 1
    path.write_bytes(data)
    archive = VPKArchive(path)
    with pytest.raises(GameAssetError, match='CRC32 mismatch'):
        archive.read(archive.entries[resource])
    write_vpk(path, {resource: b'real payload'}, preload=0)
    data = bytearray(path.read_bytes())
    struct.pack_into('<I', data, entry + 8, 0xFFFFFFFF)
    path.write_bytes(data)
    with pytest.raises(GameAssetError, match='embedded entry outside data'):
        VPKArchive(path)


def test_rerl_closure_core_precedence_missing_and_cycles(tmp_path):
    root = installation(tmp_path, {"models/hero.vmdl_c": compiled("materials/hero.vmat", "models/hero.vmdl"),
                                   "materials/hero.vmat_c": compiled("materials/core.vtex", "materials/missing.vtex")})
    write_vpk(root / "game/core/pak01_dir.vpk", {"materials/core.vtex_c": compiled(),
                                                "materials/hero.vmat_c": compiled("bad.vtex")})
    provider = GameAssetProvider(root, tmp_path / "cache")
    report = provider.materialize_closure(["models/hero.vmdl"])
    assert report["status"] == "incomplete"
    assert not report["errors"]
    assert len(report["resources"]) == 3
    assert report["missing"][0]["resource_path"] == "materials/missing.vtex_c"
    assert report["missing"][0]["referenced_by"] == ["materials/hero.vmat_c"]
    assert provider.shadowed_resources == 1
    assert Path(report["resources"]["materials/core.vtex_c"]["archive"]).parent.name == "core"
    assert Path(report["root"], "models/hero.vmdl_c").is_file()
    assert json.loads(Path(report["manifest_path"]).read_text())["closure_id"] == report["closure_id"]


def test_configured_mount_order_and_explicit_map(tmp_path):
    root = installation(tmp_path, {"models/test.vmdl_c": compiled("citadel.vtex")})
    write_vpk(root / "game/core/pak01_dir.vpk", {"models/test.vmdl_c": compiled("core.vtex")})
    (root / "game/citadel/gameinfo.gi").write_text('"GameInfo" { "FileSystem" { "SearchPaths" { Game core Game citadel } } }')
    provider = GameAssetProvider(root, tmp_path / "cache")
    assert provider.resolve("models/test.vmdl").dependencies == ("core.vtex",)
    write_vpk(root / "game/citadel/maps/dl_midtown.vpk", {"models/test.vmdl_c": compiled("map.vtex")})
    mapped = GameAssetProvider(root, tmp_path / "cache", map_name="dl_midtown")
    assert mapped.resolve("models/test.vmdl").dependencies == ("map.vtex",)
    with pytest.raises(MissingResourceError, match="start.vpk"):
        GameAssetProvider(root, tmp_path / "cache", map_name="start")


@pytest.mark.parametrize('mount_key', ['Game', 'Mod'])
@pytest.mark.parametrize('addon_mount', ['citadel/addons', 'citadel/addons/pak01_dir.vpk',
                                        'custom_theme', 'custom_theme/pak01_dir.vpk',
                                        'citadel/dmm_localization_default', 'core/addons'])
def test_addon_search_path_cannot_replace_recorded_stock_model(tmp_path, addon_mount, mount_key):
    model = 'models/heroes_wip/dynamo/dynamo.vmdl_c'
    material = 'materials/dynamo.vmat_c'
    stock_model = compiled('materials/dynamo.vmat')
    root = installation(tmp_path, {model: stock_model, material: compiled()})
    mount = root / 'game' / addon_mount
    addon = mount if mount.suffix == '.vpk' else mount / 'pak01_dir.vpk'
    write_vpk(addon, {model: compiled('models/heroes_wip/dynamo/dynamo_backup.vmdl'),
                      material: compiled('materials/missing_mod_texture.vtex'),
                      'materials/addon_only.vmat_c': compiled()})
    original_addon = addon.read_bytes()
    gameinfo = root / 'game/citadel/gameinfo.gi'
    configuration = ('"GameInfo" { "FileSystem" { "SearchPaths" { ' + mount_key + ' "' + addon_mount
                     + '" Mod citadel Game citadel Mod core Game core } } }')
    gameinfo.write_text(configuration)
    provider = GameAssetProvider(root, tmp_path / 'cache')
    resolved = provider.resolve(model)
    assert resolved.archive == root / 'game/citadel/pak01_dir.vpk'
    assert resolved.sha256 == hashlib.sha256(stock_model).hexdigest()
    closure = provider.materialize_closure([model])
    assert closure['status'] == 'complete'
    assert set(closure['resources']) == {model, material}
    assert str(addon) not in {row['path'] for row in provider.snapshot['archives']}
    assert any('not mounted' in warning and addon_mount in warning for warning in provider.warnings)
    assert provider.snapshot['ignored_search_paths'] == [{'path': addon_mount, 'reason': 'custom_search_path'}]
    with pytest.raises(MissingResourceError):
        provider.resolve('materials/addon_only.vmat')
    assert addon.read_bytes() == original_addon
    assert gameinfo.read_text() == configuration


def test_invalid_addon_archive_is_never_opened(tmp_path):
    root = installation(tmp_path, {'models/test.vmdl_c': compiled()})
    addon = root / 'game/citadel/addons/pak01_dir.vpk'
    addon.parent.mkdir()
    addon.write_bytes(b'not a VPK archive')
    (root / 'game/citadel/gameinfo.gi').write_text(
        'GameInfo { FileSystem { SearchPaths { Game citadel/addons Game citadel } } }')
    provider = GameAssetProvider(root, tmp_path / 'cache')
    assert provider.resolve('models/test.vmdl').archive == root / 'game/citadel/pak01_dir.vpk'


@pytest.mark.parametrize('mount', ['|gameinfo_path|.', '|all_source_engine_paths|citadel',
                                  'citadel/pak01_dir.vpk', '|all_source_engine_paths|citadel/pak01_dir.vpk'])
def test_stock_search_path_forms_are_retained(tmp_path, mount):
    root = installation(tmp_path, {'models/test.vmdl_c': compiled('materials/core.vmat')})
    write_vpk(root / 'game/core/pak01_dir.vpk', {'materials/core.vmat_c': compiled()})
    (root / 'game/citadel/gameinfo.gi').write_text(
        'GameInfo { FileSystem { SearchPaths { Game "'+mount+'" Game core } } }')
    provider = GameAssetProvider(root, tmp_path / 'cache')
    assert provider.materialize_closure(['models/test.vmdl'])['status'] == 'complete'
    assert provider.snapshot['ignored_search_paths'] == []
    assert provider.shadowed_resources == 0


def test_corrupt_winning_stock_archive_does_not_fall_back(tmp_path):
    resource = 'models/test.vmdl_c'
    root = installation(tmp_path, {resource: compiled()}, external=True)
    write_vpk(root / 'game/core/pak01_dir.vpk', {resource: compiled()})
    chunk = root / 'game/citadel/pak01_000.vpk'
    damaged = bytearray(chunk.read_bytes())
    damaged[-1] ^= 1
    chunk.write_bytes(damaged)
    provider = GameAssetProvider(root, tmp_path / 'cache')
    with pytest.raises(GameAssetError, match='CRC32 mismatch'):
        provider.resolve(resource)


def test_cache_verification_update_retains_old_content_and_no_hardlinks(tmp_path):
    root = installation(tmp_path, {"models/test.vmdl_c": compiled()})
    provider = GameAssetProvider(root, tmp_path / "cache")
    first = provider.resolve("models/test.vmdl")
    first.local_path.write_bytes(b"corrupted")
    assert provider.resolve("models/test.vmdl").local_path.read_bytes() == compiled()
    report = provider.materialize_closure(["models/test.vmdl"])
    working = Path(report["resources"]["models/test.vmdl_c"]["local_path"])
    working.write_bytes(b"a converter accidentally modified its input")
    assert first.local_path.read_bytes() == compiled()
    assert provider.materialize_closure(["models/test.vmdl"])["closure_id"] == report["closure_id"]
    assert working.read_bytes() == compiled()
    write_vpk(root / "game/citadel/pak01_dir.vpk", {"models/test.vmdl_c": compiled("different.vmat")})
    with pytest.raises(GameAssetError, match="changed"):
        provider.resolve("models/test.vmdl")
    second = GameAssetProvider(root, tmp_path / "cache").resolve("models/test.vmdl")
    assert second.sha256 != first.sha256
    assert first.local_path.read_bytes() == compiled()


def test_crc_and_numbered_chunk_missing_are_explicit(tmp_path):
    root = installation(tmp_path, {"models/test.vmdl_c": compiled()}, external=True)
    provider = GameAssetProvider(root, tmp_path / "cache")
    chunk = root / "game/citadel/pak01_000.vpk"
    data = bytearray(chunk.read_bytes())
    data[-1] ^= 1
    chunk.write_bytes(data)
    with pytest.raises(GameAssetError, match="CRC32 mismatch"):
        provider.resolve("models/test.vmdl")
    chunk.unlink()
    report = provider.materialize_closure(["models/test.vmdl"])
    assert report["missing"] and "numbered VPK chunk" in report["missing"][0]["reason"]


@pytest.mark.parametrize("path", ["../escape", "x/../../escape", "/etc/passwd", "C:\\bad", "x//file", "NUL.txt", "x/CON", "x./bad"])
def test_reject_unsafe_resource_paths(path):
    with pytest.raises(GameAssetError):
        safe_resource_path(path)


def test_index_rejects_traversal_duplicate_and_bad_bounds(tmp_path):
    path = tmp_path / "bad_dir.vpk"
    write_vpk(path, {"../escape.vtex_c": compiled()})
    with pytest.raises(GameAssetError, match="Unsafe"):
        VPKArchive(path)
    write_vpk(path, {"x/Foo.vtex_c": compiled(), "x/foo.vtex_c": compiled()})
    with pytest.raises(GameAssetError, match="Duplicate"):
        VPKArchive(path)
    write_vpk(path, {"x.vtex_c": compiled()})
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 8, 9999999)
    path.write_bytes(data)
    with pytest.raises(GameAssetError, match="tree size"):
        VPKArchive(path)


def test_unknown_resource_format_is_reported_not_silently_dropped(tmp_path):
    unknown = bytearray(compiled())
    struct.pack_into("<H", unknown, 4, 13)
    root = installation(tmp_path, {"new.vmdl_c": unknown})
    report = GameAssetProvider(root, tmp_path / "cache").materialize_closure(["new.vmdl"])
    assert report["status"] == "incomplete"
    assert "version: 13" in report["errors"][0]["reason"]


def test_rerl_bounds_reject_corrupt_name_pointer():
    data = bytearray(compiled("a.vmdl"))
    struct.pack_into("<q", data, 44, 1 << 50)
    with pytest.raises(GameAssetError, match="name outside"):
        resource_references(data)


def test_discovery_other_library_manifest_and_path_with_spaces(tmp_path, monkeypatch):
    steam = tmp_path / "Steam"
    library = tmp_path / "Other drive" / "SteamLibrary"
    root = library / "steamapps/common/Custom Deadlock Folder"
    write_vpk(root / "game/citadel/pak01_dir.vpk", {"a.txt": b"x"})
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps/libraryfolders.vdf").write_text(f'"libraryfolders" {{ "0" {{ "path" "{library.as_posix()}" }} }}')
    (library / "steamapps/appmanifest_1422450.acf").write_text('"AppState" { "installdir" "Custom Deadlock Folder" "buildid" "123" }')
    monkeypatch.setenv("STEAM_PATH", str(steam))
    monkeypatch.delenv("DEADLOCK_INSTALL", raising=False)
    assert root in discover_installations()
    metadata = GameAssetProvider(root, tmp_path / "cache").snapshot["metadata"]
    assert "123" in metadata["steam_manifest"]["text"]


def test_read_only_install_and_cache_symlink_guard(tmp_path):
    root = installation(tmp_path, {"models/test.vmdl_c": compiled()})
    with pytest.raises(GameAssetError, match="outside"):
        GameAssetProvider(root, root / "cache")
    cache = tmp_path / "cache"
    provider = GameAssetProvider(root, cache)
    outside = tmp_path / "outside"
    outside.mkdir()
    cache.mkdir()
    try:
        (cache / "objects").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    with pytest.raises(GameAssetError, match="escapes"):
        provider.resolve("models/test.vmdl")
    assert not list(outside.iterdir())
