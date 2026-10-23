"""Identity and coordinate contract for the separately downloaded map library."""
from __future__ import annotations

import hashlib
from pathlib import Path

MAP_PACKAGE_ID = 'midtown-20261023'
MAP_BLEND_SHA256 = 'df313a68d33b716f8411f87acbed02c242be09dd518de6866233de140b016903'
MAP_BLEND_BYTES = 1193631059
MAP_DOWNLOAD_URL = 'https://maps.allevilmc.com/RenderLockMap-midtown-20261023-df313a68d33b.zip'
MAP_OPTIMIZER_URL = 'https://github.com/Miruneko0/Deadlock-Map-Optimizer'
MAP_COLLECTION = 'RenderLockMap'
CHAR_COLLECTION = 'RenderLockChar'
MAP_OBJECTS = 6392
MAP_MESHES = 6314
MAP_VERTICES = 38678716


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
        if path.suffix.lower() != '.blend' or not path.is_file() or before.st_size != MAP_BLEND_BYTES:
            raise ValueError('Choose RenderLockMap.blend from the supported map download, after extracting the ZIP.')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if identity(before) != identity(after) or digest != MAP_BLEND_SHA256:
            raise ValueError('The map differs from the supported download. Keep its original library unchanged; edit or cut the final exported scene instead.')
        return dict(path=str(path), sha256=digest, package_id=MAP_PACKAGE_ID)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError('External map: ' + str(exc)) from exc


def map_coordinate_matrix(coordinates=None):
    # The provided map's raw Blender glTF basis was independently checked using
    # installed-world vertices. Do not apply the character rig's model yaw.
    from .installed_map import blender_map_matrix
    return blender_map_matrix(coordinates)


def validate_map_completion(result, library, coordinates=None):
    import numpy as np
    expected = dict(status='appended', package_id=MAP_PACKAGE_ID,
                    library_sha256=library['sha256'], collection=MAP_COLLECTION,
                    character_collection=CHAR_COLLECTION, imported_objects=MAP_OBJECTS,
                    mesh_objects=MAP_MESHES, vertices=MAP_VERTICES,
                    texture_relink='manual', map_cutting='manual', linked_libraries=0)
    if not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()):
        raise RuntimeError('External map: Blender completion lacks the requested map hierarchy or identity; the scene was not published.')
    try:
        matrix = np.asarray(result.get('coordinate_matrix'), dtype=float)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('External map: Blender completion has an invalid coordinate matrix; the scene was not published.') from exc
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix, map_coordinate_matrix(coordinates), rtol=0, atol=1e-7)):
        raise RuntimeError('External map: Blender completion has a different map coordinate transform; the scene was not published.')
