import struct

import pytest

from agr.source_probe_assets import probe_regions
from agr.source_probe_layout import (derive_probe_layout, dimensions_for_plan,
                                     read_texture_layout, validate_probe_layout)
from agr.source_lighting_profiles import WORLD_6779, WORLD_6793


def write_vtex(path, *, width, height, depth, image_format=19, mips=1):
    payload = struct.pack('<HH4fHHHBB', 1, 0, 0, 0, 0, 0, width, height, depth,
                          image_format, mips) + b'\0' * 8
    entry = struct.pack('<4sII', b'DATA', 8, len(payload))
    size = 16 + 8 + 12 + len(payload)
    path.write_bytes(struct.pack('<IHHII', size, 12, 1, 16, 1) + b'\0' * 8 + entry + payload)
    return path


def layout(tmp_path, atlas=(244, 236, 1416), cube=(256, 256, 171),
           atlas_format=19, cube_mips=7):
    atlas_path = write_vtex(tmp_path / 'atlas.vtex_c', width=atlas[0], height=atlas[1],
                            depth=atlas[2], image_format=atlas_format)
    cube_path = write_vtex(tmp_path / 'cube.vtex_c', width=cube[0], height=cube[1],
                           depth=cube[2], mips=cube_mips)
    return derive_probe_layout(
        read_texture_layout(atlas_path), read_texture_layout(cube_path),
        atlas_resource='maps/m/atlas.vtex_c', atlas_sha256='a' * 64,
        cubemap_resource='maps/m/cube.vtex_c', cubemap_sha256='b' * 64)


def test_6801_atlas_and_cube_headers_produce_the_installed_layout(tmp_path):
    result = layout(tmp_path)
    assert result['dimensions'] == [244, 236, 236]
    assert result['cube_depth'] == 171
    assert result['atlas']['depth'] == 1416 and result['atlas']['format'] == 19
    assert result['cubemap']['mips'] == 7 and result['cubemap']['width'] == 256
    assert validate_probe_layout(result) is result


def test_probe_regions_use_the_explicit_layout(tmp_path):
    result = layout(tmp_path)
    probe = dict(classname='env_combined_light_probe_volume', hammeruniqueid='1:2',
                 array_index=170, indoor_outdoor_level=0, origin=[0, 0, 0], angles=[0, 0, 0],
                 box_mins=[-10] * 3, box_maxs=[10] * 3, edge_fade_dists=[1] * 3,
                 light_probe_size_x=1, light_probe_size_y=1, light_probe_size_z=1,
                 light_probe_atlas_x=1, light_probe_atlas_y=1, light_probe_atlas_z=235,
                 lightprobetexture='maps/m/atlas.vtex_c',
                 lightprobetexture_dlshd='maps/m/shadow.vtex_c')
    assert len(probe_regions(probe, result)) == 7
    probe['array_index'] = 171
    with pytest.raises(ValueError, match='array index'):
        probe_regions(probe, result)
    probe['array_index'] = 170
    probe['light_probe_atlas_z'] = 236
    with pytest.raises(ValueError, match='atlas bounds'):
        probe_regions(probe, result)


def test_atlas_must_contain_exactly_six_face_slabs(tmp_path):
    with pytest.raises(ValueError, match='six face'):
        layout(tmp_path, atlas=(244, 236, 1417))


def test_cubemap_size_and_mip_layout_must_match_the_decoder(tmp_path):
    with pytest.raises(ValueError, match='cubemap'):
        layout(tmp_path, cube=(128, 128, 171))
    with pytest.raises(ValueError, match='cubemap'):
        layout(tmp_path, cube_mips=6)


def test_atlas_format_must_match_the_qualified_decoder(tmp_path):
    with pytest.raises(ValueError, match='atlas'):
        layout(tmp_path, atlas_format=20)


def test_legacy_plan_without_layout_still_uses_the_world_table():
    assert dimensions_for_plan({'world_sha256': WORLD_6793}) == ([244, 236, 236], 168)
    assert dimensions_for_plan({'world_sha256': WORLD_6779}) == ([240, 236, 256], 191)
    with pytest.raises(ValueError, match='requalification'):
        dimensions_for_plan({'world_sha256': 'f' * 64})
