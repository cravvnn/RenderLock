import hashlib
import json

import pytest

from agr import blender_map_textures as textures


def make_package(tmp_path, monkeypatch, *, filename='Textures/color.png'):
    folder = tmp_path / 'map'; folder.mkdir()
    (folder / 'Textures').mkdir()
    raw = b'fixture PNG identity; no pixel decode in preflight'
    (folder / 'Textures/color.png').write_bytes(raw)
    doc = dict(schema_version=1, package_id=textures.PACKAGE_ID,
               library=dict(sha256=textures.LIBRARY_SHA256),
               textures=[dict(file=filename, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())])
    manifest = json.dumps(doc).encode()
    (folder / 'map-package.json').write_bytes(manifest)
    monkeypatch.setattr(textures, 'MANIFEST_SHA256', hashlib.sha256(manifest).hexdigest())
    return folder


def test_package_accepts_library_or_texture_folder_and_checks_source_bytes(tmp_path, monkeypatch):
    folder = make_package(tmp_path, monkeypatch)
    for selection in (folder, folder/'RenderLockMap.blend', folder/'Textures'):
        package = textures.texture_package(selection)
        assert package['textures']['color.png']['path'] == (folder/'Textures/color.png').resolve()
    (folder/'Textures/color.png').write_bytes(b'changed texture bytes')
    with pytest.raises(ValueError, match='Missing or changed'):
        textures.texture_package(folder)
    raw = b'fixture PNG identity; no pixel decode in preflight'
    (folder/'Textures/color.png').write_bytes(b'x' * len(raw))
    with pytest.raises(ValueError, match='Changed color.png'):
        textures.texture_package(folder)


def test_manifest_must_be_the_unchanged_download_not_an_arbitrary_file_list(tmp_path, monkeypatch):
    folder = make_package(tmp_path, monkeypatch)
    with (folder/'map-package.json').open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='unchanged current map'):
        textures.texture_package(folder)


@pytest.mark.parametrize('filename', ['../color.png', 'Textures/../color.png', '/Textures/color.png',
                                     'Textures\\color.png', 'Textures/color.jpg'])
def test_manifest_paths_cannot_escape_or_search_for_similar_names(tmp_path, monkeypatch, filename):
    folder = make_package(tmp_path, monkeypatch, filename=filename)
    with pytest.raises(ValueError, match='invalid or duplicate path'):
        textures.texture_package(folder)


def test_missing_folder_explains_extracting_the_entire_archive(tmp_path):
    with pytest.raises(ValueError, match='Extract the whole map ZIP'):
        textures.texture_package(tmp_path/'RenderLockMap.blend')


def texture_result():
    return dict(status='connected', package_id=textures.PACKAGE_ID,
                library_sha256=textures.LIBRARY_SHA256, manifest_sha256=textures.MANIFEST_SHA256,
                referenced_images=1556, connected_images=1556, already_packed=0,
                already_available=0, packed_images=0, copied_shared_images=0)


@pytest.mark.parametrize('mutation', ['missing', 'hash', 'count', 'boolean', 'manual'])
def test_requested_map_textures_cannot_be_lost_behind_a_successful_geometry_report(mutation):
    from agr import external_map as maps
    from test_external_map import map_result
    result = map_result()
    result.update(texture_relink='automatic', textures=texture_result())
    library = dict(sha256=maps.MAP_BLEND_SHA256, texture_manifest_sha256=textures.MANIFEST_SHA256)
    maps.validate_map_completion(result, library)
    if mutation == 'missing':
        result.pop('textures')
    elif mutation == 'hash':
        result['textures']['manifest_sha256'] = 'wrong'
    elif mutation == 'count':
        result['textures']['connected_images'] -= 1
    elif mutation == 'boolean':
        result['textures']['already_packed'] = False
    else:
        result['texture_relink'] = 'manual'
    with pytest.raises(RuntimeError, match='External map:'):
        maps.validate_map_completion(result, library)
