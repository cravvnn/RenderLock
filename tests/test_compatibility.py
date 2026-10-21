import struct
from types import SimpleNamespace

import pytest

from agr import compatibility
from agr.source_brdf_assets import SOURCE_SHA256 as BRDF_SHA
from agr.source_lightmap import SHADER_SHA256 as DEFERRED_SHA
from agr.source_npr_package import QUALIFIED_SHADER_SHA256


def write_vtex(path, *, width, height, depth, image_format=19, mips=1):
    payload = struct.pack('<HH4fHHHBB', 1, 0, 0, 0, 0, 0, width, height, depth,
                          image_format, mips) + b'\0' * 8
    entry = struct.pack('<4sII', b'DATA', 8, len(payload))
    size = 16 + 8 + 12 + len(payload)
    path.write_bytes(struct.pack('<IHHII', size, 12, 1, 16, 1) + b'\0' * 8 + entry + payload)


def provider(tmp_path, *, atlas_depth=1416, cube_depth=171, npr=None, deferred=DEFERRED_SHA):
    (tmp_path / 'game/citadel').mkdir(parents=True)
    (tmp_path / 'game/citadel/steam.inf').write_text(
        'ClientVersion=9999\r\nServerVersion=9999\r\nVersionDate=Oct 21 2026\r\n', encoding='utf-8')
    write_vtex(tmp_path / 'atlas.vtex_c', width=244, height=236, depth=atlas_depth)
    write_vtex(tmp_path / 'cube.vtex_c', width=256, height=256, depth=cube_depth, mips=7)
    identities = {
        'maps/m/world.vwrld_c': '1' * 64,
        'maps/m/lightmaps/env_light_probe_volume_atlas.vtex_c': '2' * 64,
        'maps/m/cubemaps/env_cubemap_array.vtex_c': '3' * 64,
        compatibility.NPR_SHADER: npr or next(iter(QUALIFIED_SHADER_SHA256)),
        compatibility.DEFERRED_SHADER: deferred,
        compatibility.BRDF_RESOURCE: BRDF_SHA,
    }

    class Provider:
        install_path = tmp_path
        map_name = 'm'

        def resource_identity(self, name):
            return dict(resource_path=name, sha256=identities[name])

        def resolve(self, name):
            return SimpleNamespace(local_path=tmp_path / ('atlas.vtex_c' if 'atlas' in name else 'cube.vtex_c'),
                                   sha256=identities[name])

    return Provider()


def test_character_lighting_ready_and_static_map_separately_qualified(tmp_path, monkeypatch):
    monkeypatch.setattr(compatibility, 'selected_world', lambda p: 'maps/m/world.vwrld_c')
    report = compatibility.installed_compatibility(provider(tmp_path))
    assert report['game']['ClientVersion'] == '9999'
    assert report['probe_layout']['dimensions'] == [244, 236, 236]
    assert report['probe_layout']['cube_depth'] == 171
    assert report['hero_lighting']['status'] == 'ready'
    assert report['npr_lighting']['status'] == 'ready'
    assert report['character_lighting']['status'] == 'ready'
    assert report['static_map_lighting']['status'] == 'ready'
    assert report['status'] == 'ready'


def test_character_ready_while_static_map_requires_requalification(tmp_path, monkeypatch):
    monkeypatch.setattr(compatibility, 'selected_world', lambda p: 'maps/m/world.vwrld_c')
    report = compatibility.installed_compatibility(provider(tmp_path, deferred='f' * 64))
    assert report['hero_lighting']['status'] == 'ready'
    assert report['character_lighting']['status'] == 'ready'
    assert report['static_map_lighting']['status'] == 'requalification_required'
    assert report['status'] == 'character_ready_static_map_requalification'


def test_changed_npr_shader_blocks_npr_but_not_hero_lighting(tmp_path, monkeypatch):
    monkeypatch.setattr(compatibility, 'selected_world', lambda p: 'maps/m/world.vwrld_c')
    report = compatibility.installed_compatibility(provider(tmp_path, npr='e' * 64))
    assert report['hero_lighting']['status'] == 'ready'
    assert report['npr_lighting']['status'] == 'requalification_required'
    assert report['character_lighting']['status'] == 'requalification_required'
    assert report['status'] == 'requalification_required'
    assert any('npr_pbr_shader changed' in reason for reason in report['reasons'])


def test_unsupported_probe_texture_layout_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(compatibility, 'selected_world', lambda p: 'maps/m/world.vwrld_c')
    report = compatibility.installed_compatibility(provider(tmp_path, atlas_depth=1417))
    assert report['character_lighting']['status'] == 'requalification_required'
    assert report['probe_layout']['status'] == 'failed'
    assert any('six face' in reason for reason in report['reasons'])
