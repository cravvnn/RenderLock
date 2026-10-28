"""Identity and coordinate contract for the separately downloaded map library."""
from __future__ import annotations

import hashlib
from pathlib import Path

MAP_PACKAGE_ID = 'midtown-rebatched-20261024'
MAP_BLEND_SHA256 = 'bd48e9355123bf01945753e74a2799d40b66b4d681dbfced35638ea356d5082b'
MAP_BLEND_BYTES = 1258415284
MAP_DOWNLOAD_URL = 'https://maps.allevilmc.com/download'
MAP_OPTIMIZER_URL = 'https://github.com/Miruneko0/Deadlock-Map-Optimizer'
MAP_COLLECTION = 'RenderLockMap'
CHAR_COLLECTION = 'RenderLockChar'
MAP_OBJECTS = 6542
MAP_MESHES = 6461
MAP_VERTICES = 38926624


_LEGACY_MAPS = {
    'df313a68d33b716f8411f87acbed02c242be09dd518de6866233de140b016903': {
        'package_id': 'midtown-20261023', 'bytes': 1193631059,
        'objects': 6392, 'meshes': 6314, 'vertices': 38678716,
    },
}


def map_definition(library):
    """Look up exact qualified bytes; caller-supplied counts are not trusted."""
    definitions = dict(_LEGACY_MAPS)
    definitions[MAP_BLEND_SHA256] = dict(package_id=MAP_PACKAGE_ID, bytes=MAP_BLEND_BYTES,
        objects=MAP_OBJECTS, meshes=MAP_MESHES, vertices=MAP_VERTICES)
    try:
        return dict(definitions[library['sha256']])
    except (KeyError, TypeError) as exc:
        raise ValueError('External map: the requested library identity is not qualified.') from exc


def validate_map_blender(report):
    version = report.get('version', [])
    if (not isinstance(version, list) or len(version) != 3
            or any(type(v) is not int for v in version) or version[:2] < [5, 2]):
        raise ValueError('External map: the library was prepared with Blender 5.2. Choose Blender 5.2 or newer in Setup, or continue without the downloaded map.')


def validate_map_library(path):
    """Require the supported unmodified download before expensive preparation."""
    try:
        path = Path(path).expanduser().resolve()
        before = path.stat()
        sizes = {MAP_BLEND_BYTES, *(entry['bytes'] for entry in _LEGACY_MAPS.values())}
        if path.suffix.lower() != '.blend' or not path.is_file() or before.st_size not in sizes:
            raise ValueError('Choose RenderLockMap.blend from the supported map download, after extracting the ZIP.')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if identity(before) != identity(after) or digest not in {MAP_BLEND_SHA256, *_LEGACY_MAPS}:
            raise ValueError('The map differs from the supported download. Keep its original library unchanged; edit or cut the final exported scene instead.')
        definition = map_definition({'sha256': digest})
        if after.st_size != definition['bytes']:
            raise ValueError('Map library size differs from its qualified identity.')
        library = dict(path=str(path), sha256=digest, package_id=definition['package_id'])
        if digest == MAP_BLEND_SHA256:
            from .blender_map_textures import texture_package
            package = texture_package(path)
            library['texture_manifest_sha256'] = package['manifest_sha256']
        return library
    except (OSError, RuntimeError, ValueError) as exc:
        if str(exc).startswith('External map: '):
            raise
        raise ValueError('External map: ' + str(exc)) from exc


def map_coordinate_matrix(coordinates=None):
    # The provided map's raw Blender glTF basis was independently checked using
    # installed-world vertices. Do not apply the character rig's model yaw.
    from .installed_map import blender_map_matrix
    return blender_map_matrix(coordinates)


def validate_map_completion(result, library, coordinates=None):
    import numpy as np
    try:
        definition = map_definition(library)
    except ValueError as exc:
        raise RuntimeError('External map: completion requested an unqualified library identity.') from exc
    expected = dict(status='appended', package_id=definition['package_id'],
                    library_sha256=library['sha256'], collection=MAP_COLLECTION,
                    character_collection=CHAR_COLLECTION, imported_objects=definition['objects'],
                    mesh_objects=definition['meshes'], vertices=definition['vertices'],
                    map_cutting='manual', linked_libraries=0)
    if not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()):
        raise RuntimeError('External map: Blender completion lacks the requested map hierarchy or identity; the scene was not published.')
    if result.get('texture_relink') not in {'manual', 'automatic'}:
        raise RuntimeError('External map: Blender completion lacks the texture outcome; the scene was not published.')
    if library.get('texture_manifest_sha256') is not None:
        from .blender_map_textures import validate_texture_completion
        validate_texture_completion(result.get('textures'), library['texture_manifest_sha256'])
        if result['texture_relink'] != 'automatic':
            raise RuntimeError('External map: requested textures were not connected; the scene was not published.')
    try:
        matrix = np.asarray(result.get('coordinate_matrix'), dtype=float)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('External map: Blender completion has an invalid coordinate matrix; the scene was not published.') from exc
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix, map_coordinate_matrix(coordinates), rtol=0, atol=1e-7)):
        raise RuntimeError('External map: Blender completion has a different map coordinate transform; the scene was not published.')


def validate_map_proxy_completion(result, map_result):
    """Do not silently publish a requested proxy that Blender did not finish."""
    from .blender_map_proxy import PROXY_ALGORITHM, PROXY_COLLECTION
    expected = dict(status='ready', collection=PROXY_COLLECTION, algorithm=PROXY_ALGORITHM,
                    source_map_sha256=MAP_BLEND_SHA256, source_meshes=MAP_MESHES,
                    source_vertices=MAP_VERTICES, full_map_hidden_in_viewport=True,
                    proxy_hidden_in_render=True)
    if (not isinstance(map_result, dict) or map_result.get('status') != 'appended'
            or map_result.get('library_sha256') != MAP_BLEND_SHA256
            or not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items())):
        raise RuntimeError('Viewport proxy: Blender completion lacks the requested map proxy; the scene was not published.')
    if result['full_map_hidden_in_viewport'] is not True or result['proxy_hidden_in_render'] is not True:
        raise RuntimeError('Viewport proxy: Blender completion has invalid viewport or render visibility; the scene was not published.')
    counts = ('source_faces', 'proxy_meshes', 'proxy_vertices', 'proxy_faces')
    if (any(type(result.get(key)) is not int or result[key] <= 0 for key in counts)
            or result['proxy_meshes'] > MAP_MESHES or result['proxy_vertices'] > MAP_VERTICES
            or result['proxy_faces'] > result['source_faces']):
        raise RuntimeError('Viewport proxy: Blender completion has invalid proxy geometry counts; the scene was not published.')
